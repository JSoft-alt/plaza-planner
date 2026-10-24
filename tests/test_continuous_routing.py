"""Fast whole-area ordering, table block indexes and preserved batch endpoints."""
import itertools
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import app
import continuous_routing as continuous
from models import SearchInput
import routing
import store


def places(n=6):
    return [dict(id=f'way:{i}',name=f'Place {i}',lat=40.75,lng=-73.98+i*.001,rank=i,address='Mapped street') for i in range(1,n+1)]


class ContinuousRouting(unittest.TestCase):
    def test_block_tables_preserve_asymmetric_indexes_and_engine_limit(self):
        points=[(i,0) for i in range(105)]
        def table(service,pts,**params):
            self.assertLessEqual(len(pts),90)
            sources=[int(i) for i in params['sources'].split(';')]
            targets=[int(i) for i in params['destinations'].split(';')]
            return dict(durations=[[0 if pts[a]==pts[b] else pts[a][0]*1000+pts[b][0] for b in targets] for a in sources],
                sources=[{'distance':0}]*len(sources),destinations=[{'distance':0}]*len(targets))
        with patch.object(routing,'request_osrm',side_effect=table):matrix=continuous.matrix_blocks(points,'local')
        self.assertEqual(matrix[100][2],100002)
        self.assertEqual(matrix[2][100],2100)
        self.assertEqual(matrix[104][104],0)

    def test_global_plan_preserves_every_location_and_marks_missing_road_coverage(self):
        config=SearchInput(origin=dict(lat=40.75,lng=-73.98,label='Start',confirmed=True,source='pin'))
        scan=dict(config=config.model_dump(mode='json'),plazas=places())
        def nearest(point,endpoint):return round(point[0],6) < -73.975
        def matrix(points,endpoint):return [[0 if i==j else abs(i-j)*10+1 for j in range(len(points))] for i in range(len(points))]
        with patch.object(continuous,'nearest',side_effect=nearest),patch.object(continuous,'matrix_blocks',side_effect=matrix):
            plan=continuous.build(scan,'local')
        self.assertEqual(set(plan['order']),{p['id'] for p in scan['plazas']})
        self.assertEqual(len(plan['order']),6)
        self.assertEqual(plan['pending_road_ids'],['way:5','way:6'])
        self.assertEqual(set(plan['order'][-2:]),{'way:5','way:6'})
        self.assertFalse(plan['optimal'])

    def test_exact_small_batch_keeps_its_continuous_plan_endpoint(self):
        matrix=[[0,9,1,4],[2,0,1,8],[6,2,0,1],[1,1,8,0]]
        order,optimal=routing.optimize(matrix,fixed_end=1)
        cost=lambda path:sum(matrix[a][b] for a,b in zip([0]+list(path),path))
        self.assertTrue(optimal);self.assertEqual(order[-1],1)
        self.assertEqual(cost(order),min(cost(path) for path in itertools.permutations([1,2,3]) if path[-1]==1))


class ContinuousPersistence(unittest.TestCase):
    def test_plan_is_built_once_and_copy_preserves_old_history(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(store,'DATA',Path(folder)):
            store.init();config=SearchInput(origin=dict(lat=40.75,lng=-73.98,label='Start',confirmed=True,source='pin'),batch_size=2)
            scan=store.insert_scan(config.model_dump(mode='json'));store.update_scan(scan['id'],status='complete',plazas=places())
            client=TestClient(app.app);headers={'x-planner-csrf':client.get('/api/bootstrap').json()['csrf']}
            plan=dict(mode='continuous',order=['way:3','way:1','way:4','way:2','way:6','way:5'],optimal=False,pending_road_ids=[],seconds=.1)
            with patch.object(app.road_data,'request',return_value=dict(phase='ready',endpoint='local')),patch.object(continuous,'build',return_value=plan) as build:
                first=client.post(f"/api/scans/{scan['id']}/trips",json={},headers=headers)
                self.assertEqual(first.status_code,200,first.text)
                again=client.post(f"/api/scans/{scan['id']}/trips",json={},headers=headers)
                self.assertEqual(again.json()['trips'][0]['id'],first.json()['trips'][0]['id'])
                self.assertEqual(build.call_count,1)
            trip=first.json()['trips'][0]
            self.assertEqual(trip['plaza_ids'],['way:3','way:1'])
            store.update_trip(scan['id'],trip['id'],route=dict(order=trip['plaza_ids'],start=trip['start'],end=places()[0]))
            second=store.new_trip(scan['id']);saved=store.get_scan(scan['id'])
            self.assertEqual(saved['trips'][1]['plaza_ids'],['way:4','way:2'])
            self.assertEqual(saved['trips'][1]['start']['id'],'way:1')
            clone=client.post(f"/api/scans/{scan['id']}/continuous-copy",json={},headers=headers)
            self.assertEqual(clone.status_code,200,clone.text)
            self.assertEqual(clone.json()['trips'],[])
            self.assertEqual(len(store.get_scan(scan['id'])['trips']),2)
