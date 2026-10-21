#!/usr/bin/env python3
"""Explicit live OSRM matrix + chained route check. Uses public test coordinates."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from models import SearchInput
import routing

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--osrm-url', default=routing.OSRM)
args = parser.parse_args()
routing.OSRM = args.osrm_url.rstrip('/')
config = SearchInput(origin=dict(lat=40.758,lng=-73.9855,label='Times Square public road test',source='pin',confirmed=True),batch_size=8)
coordinates = [(40.7527,-73.9772),(40.7484,-73.9857),(40.7614,-73.9776),(40.7506,-73.9935),(40.742,-73.989),(40.769,-73.982),(40.762,-73.973),(40.746,-73.976),
               (40.751,-73.988),(40.755,-73.99),(40.754,-73.98),(40.761,-73.986),(40.766,-73.979),(40.745,-73.991),(40.749,-73.98),(40.756,-73.974)]
stops = [dict(id=str(i+1),name=f'Public road test {i+1}',lat=lat,lng=lng) for i,(lat,lng) in enumerate(coordinates)]
first = routing.calculate(config,stops[:8])
second = routing.calculate(config,stops[8:],start=first['end'])
assert second['start']==first['end']
assert len(first['order'])==len(second['order'])==8
assert set(first['order']).isdisjoint(second['order'])
print(json.dumps(dict(first=dict(order=first['order'],seconds=first['duration_seconds'],end=first['end']['id'],optimal=first['optimal'],geometry_points=len(first['geometry']['coordinates'])),
                      second=dict(start=second['start']['id'],order=second['order'],seconds=second['duration_seconds'],optimal=second['optimal'],geometry_points=len(second['geometry']['coordinates']))),indent=2))
