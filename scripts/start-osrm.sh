#!/bin/sh
# Native macOS/Linux alternative. Preprocess with the same installed OSRM version.
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
graph=${1:-"$project_dir/osrm-data/map.osrm"}
if ! command -v osrm-routed >/dev/null 2>&1; then
  echo 'Install OSRM first. macOS: brew install osrm-backend. Or use Docker Compose.' >&2
  exit 1
fi
if [ ! -f "$graph.properties" ] || [ ! -f "$graph.cell_metrics" ]; then
  echo "Missing preprocessed MLD graph: $graph.*. See deploy/SETUP.md." >&2
  exit 1
fi
graph_dir=$(CDPATH= cd -- "$(dirname -- "$graph")" && pwd)
graph_name=$(basename -- "$graph")
cd "$graph_dir"
exec osrm-routed --algorithm mld --ip 127.0.0.1 --port "${OSRM_PORT:-5001}" "$graph_name"
