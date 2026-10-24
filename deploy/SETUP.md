# Free self-hosting: planner + OSRM + matrix optimization

All routing software runs on your CPU. No Google API key or paid routing/geocoding subscription is required. Existing hardware can host it without a hosting subscription; electricity, storage and Internet access still apply. A rented cloud machine is optional and may cost money. ChatGPT can help deploy to a machine you control; a normal ChatGPT conversation is not an always-on hosting service.

## What runs where

- `planner` is the Python application: Chinese UI, discovery, SQLite, scheduling and optimizer.
- `osrm` supplies an asymmetric driving-time matrix (`table`) and the ordered road geometry (`route`). Optimization runs in Python alongside the planner; no separate matrix cloud account is needed.
- Browser map: MapLibre + OpenFreeMap. Polygon tools: Terra Draw. Address lookup: Census / explicitly selected public Nominatim. Discovery: local regional OSM PBFs through Pyosmium (optional legacy Overpass mode). Maps URLs need no API key.
- OSRM accepts longitude,latitude. Google Maps URLs use latitude,longitude. Durations are seconds. Matrix point 0 is the fixed batch start; points 1..b are new destinations.

## Docker Compose (Linux, Windows Docker Desktop, macOS Docker)

Windows users: use [WINDOWS.md](WINDOWS.md) and `run-windows.cmd`. It also supports the free WSL2 + Ubuntu Docker Engine path without Docker Desktop. The launcher translates Windows paths, checks the local Linux engine, preserves the project/volume identity and waits for web readiness before opening the browser. The native Python alternative uses `run-native-windows.cmd` and requires an existing local OSRM service.

Install Docker Engine + Compose on Linux, or a suitable Docker distribution on your own desktop. Use a folder with enough free storage and memory for the selected region. Start with a regional extract, not the planet. New York preparation previously used several GB of RAM; allow headroom (e.g. 16 GB available RAM), and measure your selected extract. This is not a guaranteed resource bound.

From this repository's root:

```sh
docker compose up -d --build planner
# Open http://localhost:8011, confirm a start, then search/calculate.
docker compose logs --tail=40 planner
```

The planner image now contains Python and the pinned official OSRM engine. Missing regional road data is automatically downloaded from Geofabrik and prepared in the background when a confirmed location or batch needs it. Child OSRM processes bind to private container loopback ports. The browser shows actual download MB and preprocessing phases; preparation can be cancelled. A route request returns 202 while preparing, and the UI retries until ready or a definite error. Never treat 202 as a computed route.

`data/routing/` in the persistent `planner-data` volume contains the regional catalog, PBF files, graph caches, preparation logs and ready manifests. Region + OSRM version + profile content determine the cache key. Reopening a cached area starts its prepared graph without downloading again. Completed graphs are not rebuilt on each pin movement or daily schedule. Update data intentionally if fresher roads are required; do not edit a graph being served.

Default individual extract download limit is **2048 MB**, configurable using `OSRM_MAX_EXTRACT_MB`; 0 removes the ceiling only on a host with adequate resources. The downloader also checks free disk with preparation headroom. Global/cross-region batches may require a much larger common extract than a local trip; do not promise unlimited geography on a small host. No paid machine is purchased automatically. Keep at least several GB RAM and sufficient disk for your intended region; a size check is not an exact memory prediction.

The app binds only to host loopback at port 8011. **Do not use `docker compose down -v`** when keeping history. Stop with `docker compose stop`. The healthcheck checks web availability, not road coverage. Only two managed region engines remain active when possible; in-use engines are protected, and older graph files stay cached.

For fixed/manual OSRM instead of automatic provisioning, existing `scripts/download-map.py` and the `prepare`/`osrm` services remain available:

```sh
python3 scripts/download-map.py https://download.geofabrik.de/north-america/us/new-york-latest.osm.pbf
docker compose run --rm prepare
OSRM_AUTO_DOWNLOAD=0 OSRM_URL=http://osrm:5000 docker compose --profile manual up -d --build planner osrm
```

On PowerShell, set those environment values with `$env:OSRM_AUTO_DOWNLOAD="0"` and `$env:OSRM_URL="http://osrm:5000"` before the Compose command. Manual preprocessing/runtime must use the same pinned image. The scripts refuse to overwrite completed manual graphs. Keep backups when rebuilding.

## Native macOS / Linux

Automatic mode requires installed OSRM binaries and car.lua. With Homebrew OSRM installed, `./run.sh` can now download and prepare missing regions itself; no manual graph is required. To reuse a fixed graph instead, use one prepared with the same binary version. On macOS Homebrew: `brew install osrm-backend osmium-tool` (verify current installation requirements with Homebrew). Prepare a new extract directory with `osrm-extract -p /path/to/car.lua osrm-data/map.osm.pbf`, then `osrm-partition osrm-data/map.osrm` and `osrm-customize osrm-data/map.osrm`. The Lua profile is part of your OSRM installation; locate it rather than guessing its installed prefix.

```sh
./scripts/start-osrm.sh /absolute/path/to/map.osrm
# In a second terminal:
./run.sh
# In a third terminal:
.venv/bin/python scripts/smoke-route.py
```

The start script only reads graph files and serves `127.0.0.1:5001`. The new app can read the old project's existing New York graph through this script; it does not modify the old application, its database or graph. Reusing a graph does not update its OSM data date.

If OSRM is on another private machine, prefer an SSH tunnel bound to localhost rather than exposing the unauthenticated OSRM endpoint:

```sh
ssh -N -L 127.0.0.1:5001:127.0.0.1:5001 your-user@your-private-routing-host
```

The app's `.env` can set `OSRM_URL=http://127.0.0.1:5001`; restart the app after changes. Keep the app and optimization on the host if you want all calculations remote. Do not use a public demo server as the production backend.

## Private phone access with Tailscale HTTPS

GPS needs a secure context: localhost on the same device works; a phone's plain HTTP LAN URL does not. Once Tailscale is configured and you have inspected existing Serve rules, choose an unused HTTPS port and proxy only the planner. Example, **only if 8444 is unused**:

```sh
tailscale serve status
tailscale serve --bg --https=8444 http://127.0.0.1:8011
```

Open the HTTPS URL reported by Tailscale on the phone. Keep this private to authorized tailnet users; do not enable Funnel or overwrite existing Serve routes. Do not expose FastAPI or OSRM to the public Internet. The app assumes a trusted single-user/local or private-tailnet deployment; public multi-user hosting needs actual authentication and isolation first.

## Local location discovery

`DISCOVERY_SOURCE=local` is the default. Search reuses downloaded routing PBF source files and downloads only missing covering regions into private data/osm-extracts. It checks coverage of the whole selected area, reads actual mapped company/shop features, and caches region/area results. With osmium-tool, native smart extraction first reduces the area before Python geometry work. Docker includes it; native installs should add it for speed. Python-only fallback remains compatible but slower. First regional downloads/road preparation can still take minutes; repeats use cached features. Separate NY and NJ extracts can cover a cross-state search without downloading the whole Northeast. This is location discovery; an actual driving batch must still have an OSRM graph covering its exact matrix points. The default download ceiling also applies to discovery extracts. Map snapshot dates are saved in JSON.

Set `DISCOVERY_SOURCE=overpass` only when you want the old public/live query path; provider limits and checkpoints remain. Road preparation after a completed search now prefetches only origin + the first batch, instead of every destination in a wide search.

## Acceptance checks after deployment

1. Open the app. Both circle and drawing modes show “使用当前位置”. If GPS is denied, a readable error appears and address/pin selection stays usable.
2. Place or drag a pin (or use its arrow keys). Coordinates update; a deliberate pin move selects that start. Apply the region. A changed center/area requires a fresh search; older scans remain in history. The circle is centered on the pin. Batch 1 uses the exact confirmed coordinates from that search, not the prior address.
3. Search saves its full discovery queue before calculating. Confirm real local OSM results and saved source dates; zero results may reflect OSM coverage or filters. No invented businesses.
4. Set b=8 before search. First batch reserves continuous-plan visits 1..8, optimizes from the confirmed start and displays new stops 1..8. Second reserves continuous-plan visits 9..16, starts at the saved endpoint of batch 1, and has 8 new stops plus that carried start. Final batch may be shorter. Check original discovery ranks stay unchanged and restart retains starts/routes.
5. New continuous routes end at the planned final stop and cannot return to the origin each batch. Legacy routes with an explicitly saved return still carry that actual returned endpoint. Once a successor is saved, the earlier route is locked.
6. `b<=12` is exact for the returned duration matrix at millisecond precision under the fixed start/end; 13..15 uses a three-second heuristic. The whole-area plan is a one-second heuristic, not a proven global optimum. These estimates exclude live traffic and verified business entrances.
7. Each destination Share produces a verified Google-issued short link or `/maps/place/...`, never `/maps/dir`. Whole-route Google Maps links intentionally use `/maps/dir/?api=1`. They preserve order; Google independently calculates roads/time. For 8 new stops the desktop link includes all 8 plus origin. Mobile browser segments are 4 + 4 destinations with a shared endpoint; per-leg links cover products that ignore waypoints. Check the actual target app retains every stop.
8. Run `python -m unittest discover -s tests -q`, `node --test tests/test_map_geometry.cjs tests/test_native_share.cjs tests/test_search_feedback.cjs tests/test_display_label.cjs`, the live OSRM smoke, and `docker compose config --quiet`. A Compose configuration check is not proof the container built or deployed.

Official references: [OSRM deployment](https://github.com/Project-OSRM/osrm-backend#quick-start), [OSRM API](https://project-osrm.org/docs/v5.24.0/api/), [official image](https://github.com/Project-OSRM/osrm-backend/pkgs/container/osrm-backend), [Maps URL limits and no-key usage](https://developers.google.com/maps/documentation/urls/get-started), [Geofabrik New York](https://download.geofabrik.de/north-america/us/new-york.html).

Drawing-mode constraint: the confirmed initial starting pin must be covered by the polygon (interior or boundary, including vertices). Enforce on both frontend and backend. Moving a pin or editing any vertex revalidates; an outside pin blocks Apply/Search. Carried later-batch starts are previously discovered in-area destinations, not a replacement discovery center.

Native sharing: supported iPhone browsers open the built-in sheet directly on the stop Share click, using an immediate Google /maps/place destination URL before awaiting any network. Physical-device behavior still requires verification. Repeated map searches can use a fifteen-minute raw-data cache; fresh public service requests can be delayed/rate-limited. The UI shows real stage/elapsed information, and Retry-After is respected.

Backups: stop the planner before copying its entire data directory (SQLite, exports, checkpoints, outreach refusals and archived schedules share the database). In Compose, `docker compose stop planner`, then use a temporary `planner-data` volume mount with `docker compose run --rm --no-deps --user 0 -v ./backups:/backup planner python -c "import shutil; shutil.make_archive('/backup/planner-data', 'gztar', '/app/data')"`. Keep backups private, restart with `docker compose up -d planner`, and verify the archive before deleting anything. Restore only while the planner is stopped, into the same volume, preserving ownership for UID 10001; retain the current data as a rollback copy first. Map graphs are reproducible from their extract and pinned OSRM version, but save the download receipt/config too.

## Continuous-route update

New trips now use continuous_routing.py to make one whole-area road-aware heuristic plan (one-second improvement budget, 500-stop limit) and persist its order before splitting batches. Matrix requests are blocked below the default OSRM 100-input limit. Existing routes are not rewritten; use the copy/replan action. Each new batch retains its planned end; <=12 stops are exact only with those fixed endpoints. Road-uncovered locations remain flagged in a geographic suffix and need the correct graph when calculating their batches. No global-optimality or real-time-traffic claim is made. New continuous trips do not return to the original start.

## Outreach update

Use real premises containing at least two mapped businesses/practices as worker drop-off clusters. Keep plausible unverified poster/flyer candidates, including ordinary chains; exclude the configured big shops/malls and explicit locally recorded refusals. Do not present unknown policies as approval. Refusal records are reversible under “已确认拒收”; saved history/routes are not rewritten.

“下一趟地点数” accepts 1–15 new destinations for each new batch and defaults to the previous requested size. A short final batch does not overwrite that preference. Fixed start/end continuity and uniqueness constraints remain.

Scheduled search UI/background dispatch are removed. Existing schedules are archived/disabled on startup; writes to retired endpoints return 410. No automatic recurring searches should run.

Download and processing ETAs describe their labelled phase, based on observed bandwidth/local timing records. Unknown first-run times remain unknown; do not promise a worldwide cold download/build under one minute.
