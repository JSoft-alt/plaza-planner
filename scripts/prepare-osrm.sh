#!/bin/sh
# Run inside the pinned OSRM container using docker compose run --rm prepare.
set -eu
if [ ! -f /data/map.osm.pbf ]; then
  echo 'Missing /data/map.osm.pbf. Download your region first; see deploy/SETUP.md.' >&2
  exit 1
fi
if [ -f /data/map.osrm.properties ]; then
  echo 'Existing graph found. Use a fresh osrm-data directory for a rebuild; preserve the old one.' >&2
  exit 1
fi
osrm-extract -p /opt/car.lua /data/map.osm.pbf
osrm-partition /data/map.osrm
osrm-customize /data/map.osrm
