"""Build a source-only Windows ZIP from HEAD; no credentials, data or Mac venv.

Windows command/PowerShell files get CRLF; container shell scripts retain LF.
Git is needed only by the maintainer building this ZIP, not by its recipient.
"""
import argparse
import io
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT.parent/'plaza-planner-windows.zip')
    args=parser.parse_args()
    archive=subprocess.check_output(['git','archive','--format=zip','--prefix=plaza-planner-windows/','HEAD'],cwd=ROOT)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(archive)) as source,zipfile.ZipFile(args.output,'w',compression=zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            relative=Path(*Path(info.filename).parts[1:])
            if relative.parts and relative.parts[0] in {'.env','data','osrm-data','.venv','.venv-windows'}:
                raise ValueError(f'Refusing to package private or machine-specific content: {relative}')
            content=source.read(info.filename)
            if relative.suffix in {'.cmd','.ps1'}:
                content=content.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')
            info.compress_type=zipfile.ZIP_DEFLATED
            target.writestr(info,content)
    print(args.output.resolve())


if __name__=='__main__':main()
