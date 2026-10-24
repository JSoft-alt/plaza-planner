# Verification — 2026-10-21

## Automated behavior

43 Python checks pass with `python -m unittest discover -s tests -q`. Coverage includes exact/asymmetric matrix optimization, chained 8+8+1 memberships, nine matrix points for an eight-new-stop batch, persisted start/end continuity, return-to-start handling, frozen predecessor routes (including the database write boundary), legacy trip recovery, concurrency/idempotence, persistence, scheduling, geocoder/discovery failure behavior, and destination sharing/redirect validation.

Polygon validation accepts a starting pin in the interior or on an edge/vertex and rejects one outside. The discovery test that separates polygon coverage from a retained diameter was updated to put the origin inside the polygon while keeping the distant mall outside the retained circle.

Seven Node checks pass with `node --test tests/test_map_geometry.cjs tests/test_native_share.cjs tests/test_search_feedback.cjs`. They cover circle radius/ring isolation, polygon containment including a concave notch, immediate Web Share activation before any network wait, native cancellation/fallback, desktop verified-link sharing, truthful elapsed/progress, backup-provider/download/filter/cancel stages, and local connection loss. `node --check static/app.js` and Python compilation pass.

For the reported apparently stuck search, the pre-fix feedback test failed because fetch_tile had no activity reporting. It now passes, as do short-cache reuse/expiry, respecting Retry-After without repeated network requests, and cancellation before a backup request. Recent matching map regions reuse a fifteen-minute cache, capped at 64 entries. No one-second sleep remains after the final region. HTTP connect/read limits are now 5/25 seconds and the Overpass computation timeout is 20 seconds; these are per-operation limits, not a guaranteed total wall-clock SLA.

## Live local routing

Found installed native OSRM **v26.9.0** and the old project's preprocessed **New York** extract. Started the engine on **127.0.0.1:5001**, reading existing graph files without rewriting them or modifying the old application. This graph's files date from September 28; it is not a fresh OSM import.

`scripts/smoke-route.py` ran against that real engine using sixteen explicitly labeled public Manhattan road test points. First batch: optimized order **5,2,4,8,1,7,3,6**, **1359.2 s**, 509 geometry coordinates, endpoint 6. Second batch: start **6**, optimized new order **13,16,15,11,9,14,10,12**, **1284.4 s**, 381 geometry coordinates. Both are exact for their returned matrices at millisecond precision. These are road test points, not discovered/qualified businesses.

The isolated UI fixture was also run with `PLANNER_QA_REAL_OSRM=1`. Through the actual UI/API, two eight-new-stop routes calculated with real OSRM. Batch 2 visibly carried batch 1's endpoint and displayed eight numbered new destinations with per-stop share actions. Fixture business names are synthetic and clearly labeled; its database is separate from normal history.

## Browser evidence

- Desktop: connected numbered itinerary, carried-start label, real route times, Share icons and whole-route Maps action.
- Opened the generated desktop URL in actual Google Maps: it retained **nine places**, a starting point and all eight ordered destinations. No Google Share dialog was used.
- Before the direct-native-share refinement, a per-stop Share in the actual browser obtained a genuine Google-issued short link and displayed the verified status. The final native path shares an immediate /maps/place URL to preserve Safari click activation; timing, cancellation and fallback are covered by Node behavior tests. No message was sent.
- 389px phone viewport: Google Maps segment chooser and map tools fit without horizontal page overflow. Current-location control remains visible in circle and drawing modes. Real physical iPhone GPS, touch gestures and the native share sheet have not been tested on hardware.
- On the real basemap, drew a polygon excluding the origin: Apply disabled and an explanatory warning appeared. Moved the pin inside: actual coordinates filled the map address field, the pin was selected automatically, Apply enabled. Applied region: form adopted the moved coordinates, old result view was cleared, prior scans remained in history.
- Existing free-map checks cover point drawing, Terra Draw vertex/midpoint edits, keyboard alternatives, cancel/Escape, retained diameter and route preview. No paid Google map/geocoding keys are used.

## Live external services and limits

Census returned a public White House address match, and Nominatim returned Queens Center by name in earlier live checks during this task. A full Queens Center discovery request returned 683 mapped features but no mall meeting the minimum of three qualifying small shops; coverage was not invented to fill that gap.

The later live small Manhattan polygon query exhausted its public-service path and failed while preserving the saved search. A focused follow-up found that the primary Overpass provider was returning **HTTP 429** (both tested HTTP methods). The final app surfaces that rate limit, observes Retry-After (at least a 60-second cooldown) and does not switch services to bypass 403/429. Public map/discovery/geocoding services cannot provide guaranteed latency or completeness. A cache hit speeds repeat requests; it does not make live public data unlimited or guarantee first-query speed.

## Deployment qualification

Repository includes Dockerfile, pinned official OSRM image/digest in Compose, graph preparation/download/native start scripts, live route smoke, manual-only CI workflow, setup steps and a copyable ChatGPT handoff prompt. Shell syntax, Python compilation and YAML parsing passed. Docker is not installed on this Mac, so container build/runtime and `docker compose config` are not locally qualified. The native Mac app and real OSRM path are qualified as above. No cloud host or Tailscale Serve rule was created or changed.

Software uses free components. An existing suitable computer can host it without an API subscription; electricity/storage and optional rented compute are separate. A normal ChatGPT conversation cannot host an always-on server. OSM completeness, independent-business classification, driveway entrances, live traffic and actual iPhone handoff are not guaranteed. No Tesla integration is enabled.


## Automatic regions and embedded Maps update — 2026-10-22

The suite now has **54 passing Python checks**, including region selection for a whole cross-boundary batch, rejected non-provider download URLs, duplicate-request coalescing, cached restart without re-download/reprocessing, failed/cancelled preparation, protecting in-use engines, the 202 pending route contract and preserved membership, and Google embed waypoint order. The existing seven Node checks still pass.

A real missing region outside the old New York graph was requested: **Monaco**. The automatic path downloaded the official **693,466-byte** PBF, ran native **v26.9.0** extract/partition/customize, started a private local server, and returned a real route between public Monaco road points: **44.8 seconds**, **31 geometry coordinates**. A repeat reused the ready endpoint/cache. Native smoke caught and fixed OSRM handling of input paths containing spaces and the explicit boolean syntax required for --mmap. Logs and road caches stay under ignored private data/routing, outside source artifacts.

Actual Google legacy iframes rendered a multi-stop blue driving route without an API key. The eight-new-stop batch's resolved iframe contained **all nine coordinate pairs** in order; its More Options link retained all eight destinations. Place preview and route preview use Google, while the exact OSRM geometry remains in MapLibre. Google routing can produce different roads/times. Official Embed API remains an optional configuration requiring the user's own key/billing-enabled project; it is not used by default.

Dockerfile/Compose were updated so automatic mode has OSRM binaries inside the planner container. Docker is still unavailable on this Mac; YAML/source checks do not establish container runtime success. Large world-spanning requests remain limited by available extract coverage, configured download ceiling and actual host RAM/disk. This update does not establish native iPhone hardware behavior or universal Google legacy iframe support.


## Hospital / office discovery update — 2026-10-22

**62 Python checks and 7 Node checks pass.** New behavioral checks cover distinct dental/law/company tenants in one building, duplicate POI/footprint hospital records counted once, standalone facilities only at minimum 1, no nearby-node clusters, real office-zone boundaries, different suites, no invented occupants from generic office-building names, company-mode rules/threshold/cache separation, old config defaults, and persisted scan/history/export/schedule type and minimum. Existing retail, batch and route checks remain passing.

Disposable browser QA on port 8012 uses a separate temporary SQLite database and clearly labelled synthetic destinations/mocked OSRM, never normal history. Verified both mode controls; company counts per building/office zone; office history reopening restores the active mode/minimum; first batch has 8 new stops; second batch carries the saved endpoint and uses the final 2 new stops; Google place/route embeds and route/share actions remain present. Desktop and 389px mobile layouts were inspected; mobile document width equals viewport width with no horizontal overflow.

A bounded live hospital-area Overpass request timed out on the primary/fallback path. Live office coverage was therefore not established in this update; the app preserves its real progress/error/retry behavior, and fixture evidence does not establish public-provider latency or tenant completeness. The existing local OSRM service is still running on loopback, and the normal app was restarted after confirming no running/queued searches. No paid resources or remote calculation service were added.

## Windows compatibility update — 2026-10-22

**66 Python checks and 7 Node checks pass.** The new portability checks launch actual isolated app processes from a copied source directory containing Chinese characters and spaces, read a UTF-8 BOM `.env` with a Chinese value, force Python tzdata instead of an OS time-zone database, reopen/export Chinese office history, persist a scheduled search, reject a concurrent instance sharing the data directory, and restart without losing records. Additional checks cover a Chinese OSM cache and native launcher dependency reuse/failure without touching history. These app processes ran on macOS, not Windows.

The official Microsoft PowerShell **7.6.8 macOS ARM64** portable distribution was downloaded outside the repository and verified against its release SHA-256. It parsed/executed the Windows launcher with stubbed engine/network/browser calls. **Six PowerShell scenarios pass:** local Docker startup, WSL distribution/path translation, stop without volume deletion, missing engine, rejected remote engine, and failed container startup. This establishes launcher control flow, not actual Windows PowerShell 5.1 or Docker/WSL behavior.

Pip's binary-only dependency resolver succeeded for **Windows x64 CPython 3.12** for the complete requirements, including Shapely, pyproj, OR-Tools and tzdata, without requiring a C++ source build. Resolution is distinct from installing/running those wheels on Windows. The app now explicitly reads caches/checkpoints/road metadata as UTF-8; `.env` accepts a UTF-8 BOM. Native Python uses `.venv-windows`; Docker contexts exclude it. Git checkout attributes preserve LF shell scripts and CRLF Windows launchers. The Windows packaging script emits CRLF `.cmd`/`.ps1` files and excludes machine data/virtualenvs from its source ZIP.

Full Windows operation uses `run-windows.cmd` with a local Linux Docker engine, including WSL2 Ubuntu Docker Engine without requiring Docker Desktop. It retains a fixed Compose project/volume, checks engine type, opens the browser only after app readiness, and never deletes history volumes. Native Python is an alternative only when a local OSRM service already exists; the source package does not contain native Windows OSRM binaries.

The existing Windows host's SSH connection timed out. No native Windows install/startup, WSL boot, browser interaction on Windows, or real Docker container build/runtime was verified in this update. Docker is still absent on this Mac. The manual-only GitHub Checks workflow now includes Windows and Linux; it was not dispatched; hosted Actions minutes were not used for this update. No VM, paid service, public endpoint or global execution-policy change was created. See deploy/WINDOWS.md for prerequisites, exact launch commands and remaining qualification boundaries.

## Local discovery and fast continuous routes — 2026-10-23

**80 Python checks and 10 Node checks pass.** New checks cover full extract coverage, cancellation before I/O, cached local discovery during public-service outages, small service plazas versus excluded malls/boxes and their interiors, address display, asymmetric matrix block indexes, exact fixed-end batch optimization, plan persistence, and carrying the actual optimized endpoint. Test data stays isolated from normal history. Windows x64 CPython 3.12 binary resolution includes the new Pyosmium dependency; native Windows and Docker execution remain unverified.

The failed wide-area medical search was blocked by public Overpass timeouts/limits while localhost remained accessible. Default discovery now reads local regional OSM PBFs, retaining the optional public mode. Actual New York plus New Jersey extracts cover the user's entire polygon; cached New York alone does not. Both source headers report 2026-10-22T20:21:33Z. Live searches completed with **150 medical/office destinations in 1.8 seconds** and, after the retail exclusions, **234 retail destinations in 7.2 seconds** using cached features. First downloads/indexing can still take minutes. These are mapped results under the saved minimum/filter rules, not proof of complete business coverage.

Fresh retail results exclude American Dream, Americana Manhasset and named major malls/big boxes. Size/floor rules also reject qualifying large mall polygons and their contained entries without invalidating independent neighboring small plazas. Missing OSM tags can still limit classification. The full Queens Center origin remains stored, while the interface displays **Queens Center · 90-15 Queens Boulevard, Elmhurst, NY**.

For scan b0f03e059f5d492d86627954beee730c, the real local OSRM whole-area plan took **3.36 seconds for 234 destinations**. Matrix requests use at most 90 input coordinates per block, four local workers, and a one-second OR-Tools improvement budget. The resulting order is persisted once and split into batches; it no longer repeatedly slices the angular queue. There is a 500-destination limit. Existing legacy routes remain intact and can be copied for continuous planning without rediscovery.

The first two actual eight-new-stop batches calculated in **0.063 and 0.068 seconds**, with OSRM estimated driving times **21.96 and 14.62 minutes**. Batch two's saved start is **way:283189927, 40.71571848002665, -73.8322946294698**, exactly batch one's optimized last stop. Each matrix includes the carried start plus eight new destinations; each batch preserves its planned final endpoint. These times describe the new membership, not a direct comparison to an old batch containing different locations.

The current New York road graph cannot snap/connect **28 destinations** under the existing checks. Every destination stays in the plan; these 28 are explicitly flagged in a geographic suffix, and their real driving batches still require road-data preparation/validation. Their placement is not a road-time optimum. Neither the full heuristic nor Google previews establish a proven global optimum or account for live traffic. Regional download/preprocessing time is outside the measured ready-graph planning time.

Live UI review confirmed the shortened origin, 7.2-second search/3.4-second plan notices, explicit pending-road warning, second-batch carried start, disabled per-batch return for continuous plans, Google route embed and share/open actions. Screenshots were saved outside the repository. Final localhost checks returned HTTP 200 for /, /api/bootstrap and /api/history in 0.010/0.002/0.010 seconds. The normal server remains available on 127.0.0.1:8011. No paid service, cloud optimizer or public endpoint was introduced.
