"""Chained membership, fixed endpoints, link limits and real optimizer inputs."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
import app
from models import SearchInput
import routing
import sharing
import store


def fake_osrm(service, points, **params):
    size = len(points)
    if service == 'table':
        return dict(code='Ok', durations=[[0 if a == b else abs(a-b)*11 + (a*3+b*7)%17 + 2 for b in range(size)] for a in range(size)],
                    sources=[dict(distance=2)]*size, destinations=[dict(distance=2)]*size)
    return dict(code='Ok', waypoints=[dict(distance=2)]*size, routes=[dict(duration=90*(size-1), distance=800*(size-1),
                geometry=dict(type='LineString', coordinates=[list(p) for p in points]), legs=[dict(duration=90)]*(size-1))])


class ChainedBatches(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data = store.DATA
        store.DATA = Path(self.temp.name)
        self.client = TestClient(app.app)
        store.init()
        self.headers = {'x-planner-csrf': self.client.get('/api/bootstrap').json()['csrf']}
        self.config = SearchInput(origin=dict(lat=40.758,lng=-73.9855,label='Public test origin',source='pin',confirmed=True),batch_size=8)
        self.sid = store.insert_scan(self.config.model_dump(mode='json'))['id']
        self.plazas = [dict(id=f'way:{i}',name=f'Test {i}',lat=40.758+i*.0002,lng=-73.9855+i*.0001,rank=i,address='Test street') for i in range(1,18)]
        store.update_scan(self.sid,status='complete',plazas=self.plazas)

    def tearDown(self):
        store.DATA = self.old_data
        self.temp.cleanup()

    def calculate(self,tid,closed=False):
        response = self.client.post(f'/api/scans/{self.sid}/trips/{tid}/route',json={'return_to_origin':closed},headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_eight_new_points_plus_carried_start_and_final_short_batch(self):
        with patch.object(routing,'request_osrm',side_effect=fake_osrm) as request:
            first = store.new_trip(self.sid)
            rec = self.calculate(first)
            first_end = rec['trips'][0]['route']['end']
            self.assertEqual(rec['trips'][0]['plaza_ids'],[p['id'] for p in self.plazas[:8]])
            second = store.new_trip(self.sid)
            rec = self.calculate(second)
            second_trip = rec['trips'][1]
            self.assertEqual(second_trip['plaza_ids'],[p['id'] for p in self.plazas[8:16]])
            self.assertEqual(second_trip['start']['id'],first_end['id'])
            tables = [call.args[1] for call in request.call_args_list if call.args[0]=='table']
            self.assertEqual([len(points) for points in tables],[9,9])
            self.assertEqual(tables[1][0],(first_end['lng'],first_end['lat']))
            self.assertTrue(second_trip['route']['optimal'])
            self.assertEqual(second_trip['route']['start'],second_trip['start'])
            self.assertEqual(len(set(second_trip['route']['order'])),8)
            third = store.new_trip(self.sid)
            rec = self.calculate(third)
            self.assertEqual([len(t['plaza_ids']) for t in rec['trips']],[8,8,1])
            self.assertEqual(rec['trips'][2]['start']['id'],second_trip['route']['end']['id'])
            self.assertEqual([p['rank'] for p in rec['plazas']],list(range(1,18)))
            assignments = [pid for t in rec['trips'] for pid in t['plaza_ids']]
            self.assertEqual(len(assignments),len(set(assignments)))
            calls_before = request.call_count
            frozen = self.client.post(f'/api/scans/{self.sid}/trips/{first}/route',json={},headers=self.headers)
            self.assertEqual(frozen.status_code,422)
            self.assertEqual(request.call_count,calls_before)
            with self.assertRaises(ValueError):store.update_trip(self.sid,first,route=rec['trips'][0]['route'])
            with self.assertRaises(ValueError):store.new_trip(self.sid)
            self.assertEqual(store.get_scan(self.sid)['trips'][1]['start'],second_trip['start'])

    def test_return_to_start_carries_actual_return_endpoint(self):
        with patch.object(routing,'request_osrm',side_effect=fake_osrm):
            tid = store.new_trip(self.sid)
            rec = self.calculate(tid,True)
            start = rec['trips'][0]['route']['start']
            self.assertEqual(rec['trips'][0]['route']['end'],start)
            store.new_trip(self.sid)
            following = store.get_scan(self.sid)['trips'][1]['start']
            self.assertEqual((following['lat'],following['lng']),(start['lat'],start['lng']))

    def test_connection_check_success_and_failure_do_not_assign_points(self):
        with patch.object(routing,'request_osrm',side_effect=fake_osrm):
            response=self.client.post('/api/routing/check',json=self.config.model_dump(mode='json'),headers=self.headers)
            self.assertEqual(response.status_code,200,response.text)
        with patch.object(routing,'request_osrm',side_effect=ValueError('offline')):
            self.assertEqual(self.client.post('/api/routing/check',json=self.config.model_dump(mode='json'),headers=self.headers).status_code,422)
        self.assertEqual(store.get_scan(self.sid)['trips'],[])

    def test_legacy_first_route_can_anchor_but_old_later_routes_require_recalculation(self):
        first=store.new_trip(self.sid)
        store.update_trip(self.sid,first,route=dict(order=['way:8'],return_to_origin=False))
        second=store.new_trip(self.sid)
        self.assertEqual(store.get_scan(self.sid)['trips'][1]['start']['id'],'way:8')
        with store.db() as conn:
            conn.execute('UPDATE trips SET payload=? WHERE id=?',(store.dump(dict(plaza_ids=[p['id'] for p in self.plazas[8:16]],route=dict(order=['way:16']),error=None)),second))
        self.assertEqual(store.new_trip(self.sid),second)
        with patch.object(routing,'request_osrm',side_effect=fake_osrm):self.calculate(second)
        third=store.new_trip(self.sid)
        self.assertNotEqual(third,second)


class GoogleRouteLinks(unittest.TestCase):
    def test_order_continuity_limits_and_no_missing_or_extra_stops(self):
        for count in [1,8,10,11,15,16]:
            points=[dict(lat=40+i*.001,lng=-74+i*.002) for i in range(count+1)]
            links=sharing.route_links(points)
            coordinates=lambda p:f"{p['lat']:.7f},{p['lng']:.7f}"
            for platform,max_waypoints in [('desktop',9),('mobile',3),('legs',0)]:
                path=[]
                for part in links[platform]:
                    self.assertLessEqual(len(part['url']),2048)
                    self.assertEqual(urlsplit(part['url']).path,'/maps/dir/')
                    params=parse_qs(urlsplit(part['url']).query)
                    waypoints=params.get('waypoints',[''])[0].split('|') if 'waypoints' in params else []
                    self.assertLessEqual(len(waypoints),max_waypoints)
                    segment=[params['origin'][0]]+waypoints+[params['destination'][0]]
                    if path:self.assertEqual(segment[0],path[-1])
                    path.extend(segment if not path else segment[1:])
                self.assertEqual(path,[coordinates(p) for p in points])
                self.assertNotIn('/maps/dir',sharing.place_url(points[0]))
