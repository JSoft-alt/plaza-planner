# Free self-hosting: planner + OSRM + matrix optimization

All routing software runs on your CPU. No Google API key or paid routing/geocoding subscription is required. Existing hardware can host it without a hosting subscription; electricity, storage and Internet access still apply. A rented cloud machine is optional and may cost money. ChatGPT can help deploy to a machine you control; a normal ChatGPT conversation is not an always-on hosting service.

## What runs where

- `planner` is the Python application: Chinese UI, discovery, SQLite, scheduling and optimizer.
- `osrm` supplies an asymmetric driving-time matrix (`table`) and the ordered road geometry (`route`). Optimization runs in Python alongside the planner; no separate matrix cloud account is needed.
- Browser map: MapLibre + OpenFreeMap. Polygon tools: Terra Draw. Address lookup: Census / explicitly selected public Nominatim. Retail discovery: Overpass. Maps URLs need no API key.
- OSRM accepts longitude,latitude. Google Maps URLs use latitude,longitude. Durations are seconds. Matrix point 0 is the fixed batch start; points 1..b are new destinations.

## Docker Compose (Linux, Windows Docker Desktop, macOS Docker)

Install Docker Engine + Compose on Linux, or a suitable Docker distribution on your own desktop. Use a folder with enough free storage and memory for the selected region. Start with a regional extract, not the planet. New York preparation previously used several GB of RAM; allow headroom (e.g. 16 GB available RAM), and measure your selected extract. This is not a guaranteed resource bound.

From this repository's root:

```sh
# Only run once; New York example. Choose data covering every planned trip.
python3 scripts/download-map.py https://download.geofabrik.de/north-america/us/new-york-latest.osm.pbf
# Windows: use `py -3` instead of `python3` if needed.
docker compose run --rm prepare
docker compose up -d --build osrm planner
# Open http://localhost:8011 on the host machine.
docker compose logs --tail=40 osrm planner
```

The preprocessing and runtime use the **same pinned official OSRM image and digest**. `extract → partition → customize` prepares MLD driving data. The script refuses to overwrite a completed graph. For a fresh map or OSRM version, prepare a new directory first, keep a backup, and replace the old graph only after successful checks. Failed partial preparation may leave files: preserve or rename that directory before a clean retry. Never mix graph versions or run preprocessing on a graph being served.

OSRM has no published host port; the app connects internally to `http://osrm:5000`. The app binds only to host loopback at port 8011. SQLite/exports use the persistent `planner-data` volume; **do not use `docker compose down -v`** when keeping history. Stop normally with `docker compose stop`. OSRM may take longer than the app to start; use “检查路线服务” after it is ready. The app healthcheck checks the web app only; it does not assert road-data coverage.

To test the real New York matrix and two chained 8-new-stop routes inside the app container:

```sh
docker compose exec planner python scripts/smoke-route.py --osrm-url http://osrm:5000
```

That smoke uses public Manhattan road test points, not discovered businesses. For another region, test its actual reachable public points and real discovered destinations. A successful local probe does not prove every destination is within the extract or within 100 m of a driveable road.

## Native macOS / Linux

If OSRM is already installed, use a graph prepared with the same binary version. On macOS Homebrew: `brew install osrm-backend` (verify current installation requirements with Homebrew). Prepare a new extract directory with `osrm-extract -p /path/to/car.lua osrm-data/map.osm.pbf`, then `osrm-partition osrm-data/map.osrm` and `osrm-customize osrm-data/map.osrm`. The Lua profile is part of your OSRM installation; locate it rather than guessing its installed prefix.

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

## Acceptance checks after deployment

1. Open the app. Both circle and drawing modes show “使用当前位置”. If GPS is denied, a readable error appears and address/pin selection stays usable.
2. Place or drag a pin (or use its arrow keys). Coordinates update; a deliberate pin move selects that start. Apply the region. A changed center/area requires a fresh search; older scans remain in history. The circle is centered on the pin. Batch 1 uses the exact confirmed coordinates from that search, not the prior address.
3. Search saves its full sweep queue before calculating. Confirm real Overpass results; zero results may reflect OSM coverage or filters. No invented businesses.
4. Set b=8 before search. First batch reserves queue ranks 1..8, optimizes from the confirmed start and displays new stops 1..8. Second reserves ranks 9..16, starts at the saved endpoint of batch 1, and has 8 new stops plus that carried start. Final batch may be shorter. Check original queue stays unchanged and restart retains starts/routes.
5. Default is open route. If “最后返回本趟起点” was explicitly chosen, the actual endpoint is the return point, and that is the next batch's start. Once a successor is saved, the earlier route is locked.
6. `b<=12` is exact for the returned duration matrix at millisecond precision; 13..15 uses a three-second heuristic and must not claim proven optimality. These estimates exclude live traffic and verified business entrances.
7. Each destination Share produces a verified Google-issued short link or `/maps/place/...`, never `/maps/dir`. Whole-route Google Maps links intentionally use `/maps/dir/?api=1`. They preserve order; Google independently calculates roads/time. For 8 new stops the desktop link includes all 8 plus origin. Mobile browser segments are 4 + 4 destinations with a shared endpoint; per-leg links cover products that ignore waypoints. Check the actual target app retains every stop.
8. Run `python -m unittest discover -s tests -q`, `node --test tests/test_map_geometry.cjs tests/test_native_share.cjs tests/test_search_feedback.cjs`, the live OSRM smoke, and `docker compose config --quiet`. A Compose configuration check is not proof the container built or deployed.

Official references: [OSRM deployment](https://github.com/Project-OSRM/osrm-backend#quick-start), [OSRM API](https://project-osrm.org/docs/v5.24.0/api/), [official image](https://github.com/Project-OSRM/osrm-backend/pkgs/container/osrm-backend), [Maps URL limits and no-key usage](https://developers.google.com/maps/documentation/urls/get-started), [Geofabrik New York](https://download.geofabrik.de/north-america/us/new-york.html).

Drawing-mode constraint: the confirmed initial starting pin must be covered by the polygon (interior or boundary, including vertices). Enforce on both frontend and backend. Moving a pin or editing any vertex revalidates; an outside pin blocks Apply/Search. Carried later-batch starts are previously discovered in-area destinations, not a replacement discovery center.

Native sharing: supported iPhone browsers open the built-in sheet directly on the stop Share click, using an immediate Google /maps/place destination URL before awaiting any network. Physical-device behavior still requires verification. Repeated map searches can use a fifteen-minute raw-data cache; fresh public service requests can be delayed/rate-limited. The UI shows real stage/elapsed information, and Retry-After is respected.

Backups: stop the planner before copying its entire data directory (SQLite, exports, checkpoints and schedules share the database). In Compose, `docker compose stop planner`, then use a temporary read-only `planner-data` volume mount with `docker compose run --rm --no-deps --user 0 -v ./backups:/backup planner python -c "import shutil; shutil.make_archive('/backup/planner-data', 'gztar', '/app/data')"`. Keep backups private, restart with `docker compose up -d planner`, and verify the archive before deleting anything. Restore only while the planner is stopped, into the same volume, preserving ownership for UID 10001; retain the current data as a rollback copy first. Map graphs are reproducible from their extract and pinned OSRM version, but save the download receipt/config too.
