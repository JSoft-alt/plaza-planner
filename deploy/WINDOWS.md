# Windows setup

Use **64-bit Windows 11** with WSL2 and a local Linux Docker engine for the full application, including automatic regional OSRM downloads/preprocessing. Compatible Windows 10 installations with working WSL2 may also run this path, but check the runtime vendor's current OS support. Windows on ARM, 32-bit Windows, Windows Server and native Windows OSRM binaries have not been qualified here.

No paid API, hosted matrix service, GPU or cloud computer is needed. The WSL2 + Ubuntu + Docker Engine path below does not require Docker Desktop. If you already use Docker Desktop, its **Linux containers** backend is supported by the launcher; check [Docker Desktop's own terms](https://docs.docker.com/desktop/setup/install/windows-install/) for your organization. The app does not enroll you in a subscription.

## Full application: first setup

1. Extract **the entire** `plaza-planner-windows.zip` into a normal writable folder, for example `C:\Users\YourName\Apps\plaza-planner-windows`. Do not run files inside Explorer's ZIP preview. Git and Python are not needed on Windows for this container path.
2. If you do not already have a local Linux Docker engine, follow [Microsoft's WSL installation](https://learn.microsoft.com/en-us/windows/wsl/install). In an administrator PowerShell terminal, the initial command is:

   ```powershell
   wsl --install -d Ubuntu
   ```

   Restart if requested, open Ubuntu once, and finish its local user setup. Check `wsl --list --verbose` shows version **2** for Ubuntu. This installation step may require enabling hardware virtualization on the PC.
3. Inside Ubuntu, follow the [official Docker Engine Ubuntu installation](https://docs.docker.com/engine/install/ubuntu/) using the apt repository, including **Docker Compose plugin** and Buildx. Run Docker as your WSL user using Docker's documented [post-install steps](https://docs.docker.com/engine/install/linux-postinstall/). Exit/reopen Ubuntu after group changes. Check:

   ```sh
   sudo service docker start
   docker info --format '{{.OSType}}'
   docker compose version
   ```

   The engine must report `linux`. These are existing-machine setup steps; the planner launcher does not elevate privileges, install OS features, change firewall rules or rewrite Docker credentials. If you already have a working Docker Desktop Linux backend, skip the Ubuntu Engine installation.
4. Double-click **`run-windows.cmd`**. First launch builds the image and installs its Python dependencies; keep the progress window open until it reports readiness. It then opens **http://127.0.0.1:8011/** in your default browser.

The launcher first looks for a working local Windows Docker CLI/engine, then Docker inside your default WSL distribution. It rejects remote Docker contexts so calculations stay on this PC. To choose a particular distribution, from the extracted folder:

```powershell
.\run-windows.cmd -Backend WSL -Distro Ubuntu
```

To use only Docker Desktop's local CLI:

```powershell
.\run-windows.cmd -Backend Docker
```

The `.cmd` wrapper uses a per-process execution-policy option to run the included PowerShell file; it does not change the machine's saved execution policy. Managed PCs may still block scripts according to organization policy.

## Normal use and storage

Both discovery sections, circle/polygon editing, current location, company counts, history, schedules, batch chaining, local matrix optimization, Google previews and sharing use the same app as macOS/Linux. Chrome or Edge with WebGL support is suitable for the drawing editor. Desktop sharing falls back when a native share sheet is unavailable. Phone GPS/sharing still needs your private HTTPS/Tailscale setup; localhost on a Windows PC is not a phone-access URL.

OSRM downloads and preprocesses a region on this computer when needed. This can take time and substantial RAM/disk; start with a small region and allow headroom (16 GB RAM is a useful starting point, not a universal guarantee). A large extract can exceed available resources. Internet is needed to download uncached regional OSM maps, look up addresses and load Google previews. Cached area discovery and filtering run locally with Pyosmium; a separate Overpass server is not required. Once the data is obtained, filtering, counts, the OSRM matrix and optimization execute locally.

The fixed Compose project name is `plaza-planner`. Persistent history, schedules, exports and road caches live in its **`plaza-planner_planner-data` Docker volume**; renaming the extracted folder does not create a fresh project identity. Closing the launcher or browser does not stop the app. A running Docker engine/WSL session is still required for scheduled searches; do not rely on the computer running schedules while asleep or shut down.

```powershell
.\run-windows.cmd -Action status
.\run-windows.cmd -Action logs
.\run-windows.cmd -Action stop
# Start again, preserving history:
.\run-windows.cmd
```

Stopping uses `compose stop planner`, never deletes volumes. **Do not run `docker compose down -v` or delete Docker volumes** if you want to keep history. Docker/WSL resets can remove volumes; back up before resetting either runtime. Switching between a Docker Desktop engine and a separate Ubuntu Docker Engine uses different volume stores; it does not automatically move history. The Mac data directory, Python environment and road graph are not included in this source ZIP.

## Optional native Windows Python

For a PC that already provides a compatible **local OSRM service**, install **64-bit Python 3.12** from [python.org](https://www.python.org/downloads/windows/) and double-click:

```powershell
.\run-native-windows.cmd
```

The launcher creates `.venv-windows`, installs dependencies once (again after requirements change), and starts a single Uvicorn worker bound to `127.0.0.1:8011`. It does not activate PowerShell environments or modify global Python packages. Python 3.11–3.14 x64 is accepted; dependency resolution was checked for Windows CPython 3.12 x64. Keep its terminal open; Ctrl+C stops the native app. Native data defaults to this folder's `data\`; `PLANNER_DATA_DIR` can point to a different writable folder. Config files are UTF-8; Notepad's optional UTF-8 BOM is supported for `.env`. Time-zone data remains included for compatibility with archived records.

Set `.env` for your existing local OSRM, for example:

```dotenv
OSRM_URL=http://127.0.0.1:5001
OSRM_AUTO_DOWNLOAD=0
```

The native Python launcher does **not** bundle a native Windows OSRM engine. Without an existing reachable service, discovery/history can run but road routing cannot. Use the full container launcher for automatic region downloads and OSRM, instead of treating Python alone as the complete routing installation. Run only one of the two app modes at a time; they use separate data stores.

## Troubleshooting and verification

- **No local Linux engine:** start Docker Desktop with Linux containers, or open Ubuntu and start its Docker Engine. Check your selected WSL distribution and `docker compose version`.
- **Port 8011 occupied:** stop the other planner instance or the service holding the port. The launcher does not kill unrelated processes. Native mode can use `$env:PORT='8013'` before `run-native-windows.cmd`; full Compose mode uses 8011.
- **Source folder has spaces/non-ASCII characters:** launcher paths are passed as arguments and WSL paths are translated using `wslpath`; no shell evaluation is used. If moving to a different PC, extract fresh source rather than copying a Mac `.venv` or a Windows `.venv-windows`.
- **PowerShell policy enforced by work/school:** follow your administrator's policy. The launcher does not override Group Policy.
- **Road preparation fails:** inspect logs with the command above, available storage and Docker/WSL memory. Search/history remain saved. No public demo-routing fallback or paid server is used.
- **App starts but OSM search is slow:** use the full container path, which includes osmium-tool for native area extraction. A Python-only installation without that tool uses a slower compatible reader. New regional downloads/OSRM preparation may exceed one minute; observed download/processing phase ETAs are shown when available.

Qualification: source/dependency/launcher checks and actual app-process portability tests passed on macOS. The PowerShell launcher was executed with stubbed container commands under official PowerShell 7, not a real Windows Docker engine. The existing Windows host was unreachable during this change. Native Windows 5.1, WSL startup and container build/runtime have **not** been established by those checks. The manual `Checks` GitHub workflow now includes `windows-latest` and the actual app-process tests, but it has not been dispatched; running it may consume the repository's Actions quota. Do not describe these artifacts as a Windows-certified installer.
