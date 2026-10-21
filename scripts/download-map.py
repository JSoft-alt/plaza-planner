#!/usr/bin/env python3
"""Download a user-selected OSM extract, using only the Python standard library."""
import argparse
import hashlib
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('url', help='HTTPS .osm.pbf URL from your chosen data provider')
parser.add_argument('--directory', default='osrm-data')
args = parser.parse_args()
url = urlsplit(args.url)
if url.scheme != 'https' or not url.path.endswith('.osm.pbf'):
    parser.error('Use an HTTPS .osm.pbf URL.')
directory = Path(args.directory)
directory.mkdir(parents=True, exist_ok=True)
output = directory / 'map.osm.pbf'
if output.exists() or list(directory.glob('*.osrm.*')):
    parser.error('Existing map or graph found. Choose a fresh directory; existing data will not be overwritten.')
partial = directory / 'map.osm.pbf.part'
request = Request(args.url, headers={'User-Agent': 'PlazaPlanner-map-setup/1.0'})
sha = hashlib.sha256()
size = 0
try:
    with urlopen(request, timeout=60) as response, partial.open('xb') as handle:
        expected = response.headers.get('Content-Length')
        while chunk := response.read(1024 * 1024):
            handle.write(chunk)
            sha.update(chunk)
            size += len(chunk)
        if expected and size != int(expected):
            raise ValueError('Incomplete download')
    if size == 0:
        raise ValueError('Empty download')
    partial.replace(output)
    (directory / 'download-receipt.txt').write_text(f'URL: {args.url}\nBytes: {size}\nSHA256: {sha.hexdigest()}\n')
    print(f'Saved {output}: {size:,} bytes. Receipt SHA256 records this download; it is not a provider signature.')
except Exception:
    # Leave a failed partial for diagnosis; explicitly remove it before retrying.
    raise
