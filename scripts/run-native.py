"""Native Windows Python launcher; full automatic OSRM uses run-windows.cmd.

Paths are argument arrays, independent of CWD and PowerShell activation policies.
No system Python packages or existing macOS/Linux virtualenv are modified.
"""
import argparse
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import urllib.request
import venv
import webbrowser

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if not (3, 11) <= sys.version_info[:2] < (3, 15) or struct.calcsize('P') != 8:
        raise ValueError('Use 64-bit Python 3.11-3.14 (3.12 recommended).')
    port = int(os.environ.get('PORT', '8011'))
    if not 1 <= port <= 65535:
        raise ValueError('PORT must be between 1 and 65535.')
    environment = ROOT / '.venv-windows'
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file():
        print('Creating the private Python environment...', flush=True)
        venv.EnvBuilder(with_pip=True).create(environment)
    requirements = ROOT / 'requirements.txt'
    receipt = environment / 'planner-requirements.sha256'
    fingerprint = hashlib.sha256(requirements.read_bytes()).hexdigest()
    if not receipt.is_file() or receipt.read_text(encoding='ascii').strip() != fingerprint:
        print('Installing application dependencies (first launch or changed requirements)...', flush=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(requirements)], cwd=ROOT, check=True)
        receipt.write_text(fingerprint, encoding='ascii')
    url = f'http://127.0.0.1:{port}/'
    print(f'Starting {url}\nKeep this window open. Ctrl+C stops the native app.\n'
          'Native routing requires an existing local OSRM service; use run-windows.cmd for automatic road setup.', flush=True)
    child_env = os.environ | {'PYTHONUTF8': '1', 'PYTHONUNBUFFERED': '1'}
    process = subprocess.Popen([str(python), '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1',
                                '--port', str(port), '--workers', '1'], cwd=ROOT, env=child_env)
    stopped = threading.Event()

    def open_when_ready():
        for _ in range(60):
            if process.poll() is not None or stopped.is_set():
                return
            try:
                with urllib.request.urlopen(url + 'api/bootstrap', timeout=1):
                    if not stopped.is_set():
                        webbrowser.open(url)
                    return
            except (OSError, ValueError):
                stopped.wait(1)

    if not args.no_browser:
        threading.Thread(target=open_when_ready, daemon=True).start()
    try:
        return process.wait()
    except KeyboardInterrupt:
        # Ctrl+C is delivered to Uvicorn too, allowing its lifespan to close DB/jobs.
        try:
            return process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.terminate()
            return process.wait(timeout=10)
    finally:
        stopped.set()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'Launch failed: {error}', file=sys.stderr)
        sys.exit(1)
