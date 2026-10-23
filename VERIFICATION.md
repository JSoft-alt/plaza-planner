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
