"""Run npm ci in this folder first, then python3 update-vendor.py.

Normal application startup uses the already vendored files, without Node/CDN.
"""
from pathlib import Path
import shutil

root = Path(__file__).parent
target = root.parent / "static" / "vendor"
for source, name in [
    ("maplibre-gl/dist/maplibre-gl.js", "maplibre-gl.js"),
    ("maplibre-gl/dist/maplibre-gl.css", "maplibre-gl.css"),
    ("maplibre-gl/dist/LICENSE.txt", "MAPLIBRE-LICENSE.txt"),
    ("terra-draw/dist/terra-draw.umd.js", "terra-draw.js"),
    ("terra-draw-maplibre-gl-adapter/dist/terra-draw-maplibre-gl-adapter.umd.js", "terra-draw-adapter.js"),
]:
    shutil.copyfile(root / "node_modules" / source, target / name)
