"""Behavioral checks for geography, durable queues, schedules and service failures.

All external APIs are mocked here. Run optional live smoke checks separately.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import itertools
import json
from pathlib import Path
import tempfile
import os
import time
import unittest
from unittest.mock import patch, Mock

from fastapi.testclient import TestClient
from pydantic import ValidationError

os.environ['OSRM_AUTO_DOWNLOAD']='0'
import app
import discovery
from models import SearchInput, ScheduleInput, cron_trigger, projection, sweep
import routing
import sharing
import store


def config(**changes):
    return SearchInput.model_validate(dict(origin=dict(lat=40.75,lng=-73.98,label="Test starting address",confirmed=True,source="address"),
                                     min_shops=2,batch_size=2) | changes)


def plaza(i):
    return dict(id=f"way:{i}", name=f"Plaza {i}", address="Mapped street", lat=40.751+i*.0001,
                lng=-73.98, rank=i, angle=0, distance_miles=.5)


def fixture():
    _, inverse=projection(40.75,-73.98)
    def coordinates(x,y):
        lng,lat=inverse(x,y)
        return {"lat":lat,"lon":lng}
    def outline(i,x=0,tags=None):
        points=[coordinates(a+x,b) for a,b in [(-80,-80),(80,-80),(80,80),(-80,80),(-80,-80)]]
        return dict(type="way",id=i,tags=tags or {"shop":"mall","name":"Mapped Mall","addr:street":"First Street"},geometry=points)
    def node(i,x,y,tags):
        return dict(type="node",id=i,tags=tags,**coordinates(x,y))
    return [outline(1),outline(2,tags={"landuse":"retail"}),
            node(3,0,0,{"shop":"hairdresser","name":"Independent Hair"}),
            node(4,2,0,{"shop":"hairdresser","name":"Independent Hair"}),
            node(5,20,20,{"shop":"laundry","name":"Local Laundry"}),
            node(6,30,0,{"shop":"convenience","brand":"Walmart"}),
            outline(7,5000),node(8,5000,0,{"shop":"tattoo","name":"Tattoo"}),
            node(9,5010,0,{"shop":"bakery","name":"Bakery"})]


class Geography(unittest.TestCase):
    def test_boundary_qualification_dedup_and_anchor(self):
        results=discovery.qualify(fixture(),config(diameter_miles=1))
        self.assertEqual(len(results),1)
        self.assertEqual(results[0]["shop_count"],2)
        self.assertEqual(results[0]["id"],"way:1")
        self.assertEqual(results[0]["provenance"]["eligible_ids"],["node:3","node:5"])

    def test_polygon_is_not_intersected_with_retained_circle(self):
        _,inverse=projection(40.75,-73.98)
        polygon=[inverse(x,y) for x,y in [(4800,-300),(5300,-300),(5300,300),(4800,300)]]
        lng,lat=inverse(4820,-280)
        origin=dict(lat=lat,lng=lng,label='Inside polygon, outside retained circle',confirmed=True,source='pin')
        results=discovery.qualify(fixture(),config(origin=origin,mode="polygon",diameter_miles=.1,polygon=polygon))
        self.assertEqual([p["id"] for p in results],["way:7"])

    def test_small_area_uses_one_query_not_four_overlapping_queries(self):
        self.assertEqual(len(discovery.tiles(config(diameter_miles=1))),1)
        self.assertEqual(len(discovery.tiles(config(diameter_miles=10))),4)

    def test_cardinals_and_baseline_and_ties(self):
        _,inverse=projection(40.75,-73.98)
        points=[]
        for pid,(x,y) in zip(["n","e","s","w"],[(0,100),(100,0),(0,-100),(-100,0)]):
            lng,lat=inverse(x,y);points.append(dict(id=pid,lat=lat,lng=lng))
        self.assertEqual([p["id"] for p in sweep(points,config())],["n","e","s","w"])
        self.assertEqual([p["id"] for p in sweep(points,config(baseline=90))],["e","s","w","n"])
        ties=[dict(id="b",lat=40.76,lng=-73.98),dict(id="a",lat=40.76,lng=-73.98)]
        self.assertEqual([p["id"] for p in sweep(ties,config())],["a","b"])

    def test_invalid_geometry_and_values(self):
        for change in [dict(diameter_miles=31),dict(batch_size=16),dict(min_shops=1.5),dict(baseline=float('nan')),
                       dict(mode="polygon",polygon=[[-74,40],[-73,41],[-74,41],[-73,40]]),
                       dict(mode="polygon",polygon=[[-74,40],[-73,41]]),
                       dict(mode="polygon",polygon=[[-170,20],[170,20],[170,21]])]:
            with self.subTest(change=change),self.assertRaises(ValidationError):config(**change)

    def test_polygon_must_cover_start_interior_and_boundary(self):
        polygon=[[-74,40.7],[-73.9,40.7],[-73.9,40.8],[-74,40.8]]
        self.assertEqual(config(mode='polygon',polygon=polygon).mode,'polygon')
        edge=dict(lat=40.75,lng=-74,label='Boundary start',confirmed=True,source='pin')
        self.assertEqual(config(origin=edge,mode='polygon',polygon=polygon).origin.lng,-74)
        vertex=dict(edge,lat=40.7)
        self.assertEqual(config(origin=vertex,mode='polygon',polygon=polygon).origin.lat,40.7)
        with self.assertRaisesRegex(ValidationError,'起点必须'):
            config(origin=dict(edge,lng=-74.01),mode='polygon',polygon=polygon)

    def test_rules(self):
        rules=json.loads(discovery.RULES.read_text())
        for name in ["McDonald’s","Home Depot","Walmart","Starbucks","KFC","CVS"]:
            self.assertIsNone(discovery.category({"name":name,"shop":"convenience"},rules))
        for tags in [{"amenity":"restaurant","cuisine":"italian;pizza"},{"shop":"tattoo"},
                     {"sport":"yoga"},{"shop":"hardware"}]:
            self.assertIsNotNone(discovery.category(tags,rules))


class Routing(unittest.TestCase):
    def test_exact_open_and_return(self):
        matrix=[[0,5,9,2],[7,0,1,9],[2,5,0,8],[8,1,3,0]]
        for closed in [False,True]:
            order,optimal=routing.optimize(matrix,closed)
            def cost(path):return sum(matrix[a][b] for a,b in zip([0]+list(path),list(path)+([0] if closed else [])))
            self.assertTrue(optimal)
            self.assertEqual(cost(order),min(cost(p) for p in itertools.permutations([1,2,3])))

    def test_invalid_matrices(self):
        for matrix in [[[0,None],[1,0]],[[0,-1],[1,0]],[[0,float('inf')],[1,0]],[[0,1]],[[1,2],[1,0]]]:
            with self.subTest(matrix=matrix),self.assertRaises(ValueError):routing.optimize(matrix)

    def test_bounded_large_solution_is_not_claimed_optimal(self):
        matrix=[[0 if a==b else abs(a-b)+1 for b in range(14)] for a in range(14)]
        path,optimal=routing.optimize(matrix)
        self.assertFalse(optimal);self.assertEqual(set(path),set(range(1,14)))

    def test_snapping_and_bad_geometry_rejected(self):
        with self.assertRaises(ValueError):routing.validate_waypoints({"sources":[{"distance":1000}]},1,["sources"])
        matrix=[[0,5],[5,0]]
        table=dict(code="Ok",durations=matrix,sources=[{"distance":0}]*2,destinations=[{"distance":0}]*2)
        bad=dict(code="Ok",waypoints=[{"distance":0}]*2,routes=[dict(geometry={"type":"LineString","coordinates":[[0,0],[float('nan'),0]]})])
        with patch.object(routing,'request_osrm',side_effect=[table,bad]),self.assertRaises(ValueError):
            routing.calculate(config(),[plaza(1)])


class Application(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();store.DATA=Path(cls.temp.name)
        cls.client=TestClient(app.app);cls.client.__enter__()
        cls.headers={"x-planner-csrf":cls.client.get('/api/bootstrap').json()['csrf']}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None,None,None);cls.temp.cleanup()

    def make_scan(self,count=5):
        rec=store.insert_scan(config().model_dump(mode="json"))
        store.update_scan(rec['id'],status="complete",plazas=[plaza(i) for i in range(1,count+1)])
        return rec['id']

    def test_no_routing_during_search_and_export(self):
        with patch.object(discovery,'fetch_tile',return_value=fixture()),patch.object(routing,'request_osrm',side_effect=AssertionError('Routing during search')),patch.object(discovery.time,'sleep'):
            response=self.client.post('/api/scans',json=config(diameter_miles=1).model_dump(mode="json"),headers=self.headers)
            self.assertEqual(response.status_code,200,response.text);sid=response.json()['id']
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                rec=store.get_scan(sid)
                if rec['status'] not in ['queued','running']:break
                time.sleep(.01)
            self.assertEqual(rec['status'],'complete',rec['message'])
            self.assertEqual(len(rec['plazas']),1)
            files=list((store.DATA/'exports').glob('*.json'));self.assertTrue(files)
            self.assertEqual(json.loads(self.client.get(f'/api/scans/{sid}/export').content)['id'],sid)

    def test_office_search_persists_kind_threshold_and_schedule(self):
        from test_offices import fixture as office_fixture
        c=config(discovery_kind='offices',min_companies=3,diameter_miles=1)
        rec=store.insert_scan(c.model_dump(mode='json'))
        with patch.object(discovery,'fetch_tile',return_value=office_fixture()) as fetch:
            discovery.run_scan(rec['id'])
        saved=self.client.get(f"/api/scans/{rec['id']}").json()
        self.assertEqual(saved['status'],'complete',saved['message'])
        self.assertEqual(saved['config']['discovery_kind'],'offices')
        self.assertEqual(saved['config']['min_companies'],3)
        self.assertEqual(saved['plazas'][0]['company_count'],4)
        self.assertEqual(fetch.call_args.kwargs['discovery_kind'],'offices')
        self.assertEqual(saved['qualification_rules']['categories'][0]['id'],'hospital')
        trip=store.new_trip(rec['id'])
        self.assertEqual(store.get_scan(rec['id'])['trips'][0]['start']['lat'],c.origin.lat)
        export=self.client.get(f"/api/scans/{rec['id']}/export").json()
        self.assertEqual(export['config']['discovery_kind'],'offices')
        history=self.client.get('/api/history').json()['items']
        self.assertTrue(any(r['id']==rec['id'] and r['config']['min_companies']==3 for r in history))
        response=self.client.post('/api/schedules',json=dict(name='Offices',config=c.model_dump(mode='json'),cron='0 9 * * *',timezone='America/New_York',enabled=False),headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        saved_schedule=next(s for s in self.client.get('/api/schedules').json()['items'] if s['id']==response.json()['id'])
        self.assertEqual(saved_schedule['config']['discovery_kind'],'offices')
        self.assertEqual(saved_schedule['config']['min_companies'],3)

    def test_duplicate_batches_refresh_and_final_short_trip(self):
        sid=self.make_scan()
        with ThreadPoolExecutor(max_workers=4) as executor:
            first=list(executor.map(lambda _:store.new_trip(sid),range(4)))
        self.assertEqual(len(set(first)),1)
        store.update_trip(sid,first[0],route={'order':['way:2'], 'start':store.get_scan(sid)['trips'][0]['start']})
        second=store.new_trip(sid);store.update_trip(sid,second,route={'order':['way:4'], 'start':store.get_scan(sid)['trips'][1]['start']})
        third=store.new_trip(sid)
        rec=store.get_scan(sid)
        self.assertEqual([len(t['plaza_ids']) for t in rec['trips']],[2,2,1])
        ids=[i for t in rec['trips'] for i in t['plaza_ids']]
        self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual(self.client.get(f'/api/scans/{sid}').json()['trips'],rec['trips'])

    def test_route_failure_keeps_scan_and_assignments(self):
        sid=self.make_scan(2);tid=store.new_trip(sid)
        with patch.object(routing,'calculate',side_effect=ValueError('OSRM unavailable')):
            response=self.client.post(f'/api/scans/{sid}/trips/{tid}/route',json={},headers=self.headers)
        self.assertEqual(response.status_code,422)
        rec=store.get_scan(sid);self.assertEqual(rec['status'],'complete')
        self.assertEqual(len(rec['plazas']),2);self.assertEqual(rec['trips'][0]['error'],'OSRM unavailable')

    def test_invalid_form_csrf_and_filter(self):
        self.assertEqual(self.client.post('/api/scans',json={}).status_code,403)
        body=config().model_dump(mode="json");body['batch_size']=100
        response=self.client.post('/api/scans',json=body,headers=self.headers)
        self.assertEqual(response.status_code,422);self.assertIn('batch_size',response.json()['errors'])
        self.assertEqual(self.client.get('/api/history?q=not-a-match').json()['total'],0)

    def test_partial_retry_and_cancel(self):
        sid=store.insert_scan(config(diameter_miles=1).model_dump(mode="json"))['id']
        with patch.object(discovery,'fetch_tile',side_effect=ValueError('rate limited')):
            discovery.run_scan(sid)
        self.assertEqual(store.get_scan(sid)['status'],'failed')
        store.update_scan(sid,cancel=True)
        with patch.object(discovery,'fetch_tile',side_effect=AssertionError('Should not fetch')):
            discovery.run_scan(sid)
        self.assertEqual(store.get_scan(sid)['status'],'cancelled')
        with patch.object(discovery,'fetch_tile',return_value=fixture()),patch.object(discovery.time,'sleep'):
            store.update_scan(sid,cancel=False);discovery.run_scan(sid)
        self.assertEqual(store.get_scan(sid)['status'],'complete')

    def test_cron_timezone_numeric_weekday_and_validation(self):
        monday=datetime(2026,10,19,0,0,tzinfo=timezone.utc)
        trigger=cron_trigger('0 9 * * 1-5','America/New_York')
        fire=trigger.get_next_fire_time(None,monday)
        self.assertEqual((fire.weekday(),fire.hour),(0,9))
        sunday=cron_trigger('0 9 * * 0','UTC').get_next_fire_time(None,monday)
        self.assertEqual(sunday.weekday(),6)
        for expression,tz in [('bad','UTC'),('0 9 * * *','No/Timezone'),('0 25 * * *','UTC')]:
            with self.assertRaises(ValidationError):ScheduleInput(name='test',config=config(),cron=expression,timezone=tz)

    def test_schedule_crud_tick_overlap_and_clear_scope(self):
        body=dict(name='Recurring test',config=config().model_dump(mode="json"),cron='0 9 * * *',timezone='America/New_York',enabled=True)
        response=self.client.post('/api/schedules',json=body,headers=self.headers);self.assertEqual(response.status_code,200,response.text)
        sid=response.json()['id']
        with store.db() as conn:conn.execute('UPDATE schedules SET next_run=? WHERE id=?',(time.time()-1,sid))
        with patch.object(app.pool,'submit') as submit:
            app.schedule_tick();self.assertEqual(submit.call_count,1)
            with store.db() as conn:conn.execute('UPDATE schedules SET next_run=? WHERE id=?',(time.time()-1,sid))
            app.schedule_tick();self.assertEqual(submit.call_count,1)
        running=next(s for s in app.schedules_list() if s['id']==sid)['running']
        with patch.object(discovery,'run_scan',side_effect=lambda scan_id:store.update_scan(scan_id,status='complete')):
            app.run(running,sid)
        spec=next(s for s in app.schedules_list() if s['id']==sid)
        self.assertIsNone(spec['running']);self.assertIn('完成',spec['last_outcome'])
        self.assertEqual(self.client.put('/api/schedules/'+sid,json={**body,'enabled':False},headers=self.headers).status_code,200)
        store.delete_scans();self.assertTrue(any(s['id']==sid for s in app.schedules_list()))
        self.assertEqual(self.client.delete('/api/schedules/'+sid,headers=self.headers).status_code,200)

    def test_persistence_restart_recovery_and_safe_export(self):
        sid=self.make_scan(1);before=store.get_scan(sid)
        store.init();store.recover();self.assertEqual(before,store.get_scan(sid))
        self.assertNotIn('/',store.safe_name(before))
        pending=store.insert_scan(config().model_dump(mode="json"))['id'];store.recover()
        self.assertEqual(store.get_scan(pending)['status'],'partial')

    def test_route_preparation_is_pending_and_keeps_batch_membership(self):
        sid=self.make_scan(2);tid=store.new_trip(sid)
        import road_data
        with patch.object(road_data,'request',return_value=dict(phase='download',job_id='a'*24,message='downloading')):
            response=self.client.post(f'/api/scans/{sid}/trips/{tid}/route',json={},headers=self.headers)
        self.assertEqual(response.status_code,202,response.text)
        self.assertTrue(response.json()['preparing'])
        trip=store.get_scan(sid)['trips'][0]
        self.assertEqual(trip['plaza_ids'],['way:1','way:2']);self.assertIsNone(trip['route'])

    def test_google_embed_endpoint_uses_saved_optimized_order(self):
        sid=self.make_scan(2);tid=store.new_trip(sid)
        store.update_trip(sid,tid,route=dict(order=['way:2','way:1'],return_to_origin=False))
        response=self.client.get(f'/api/scans/{sid}/embeds?tid={tid}')
        self.assertEqual(response.status_code,200)
        from urllib.parse import parse_qs,urlsplit
        data=response.json()
        query=parse_qs(urlsplit(data['route'][0]['url']).query)
        import embeds
        self.assertEqual(query['daddr'][0],' to:'.join(embeds.coordinate(plaza(i)) for i in [2,1]))
        self.assertEqual(len(data['places']),2)

    def test_sharing_only_destination_formats(self):
        sid=self.make_scan(1)
        for link in ['https://maps.app.goo.gl/VerifiedExample']:
            with patch.object(sharing,'short_link',return_value=link):
                result=self.client.post(f'/api/scans/{sid}/share/way:1',headers=self.headers).json()
                self.assertEqual(result['url'],link)
        with patch.object(sharing,'short_link',side_effect=ValueError('Google down')):
            result=self.client.post(f'/api/scans/{sid}/share/way:1',headers=self.headers).json()
            self.assertIn('google.com/maps/place/',result['url']);self.assertNotIn('/maps/dir',result['url'])

    def test_short_link_requires_correct_google_redirect_and_is_cached(self):
        destination=plaza(99)
        issued=Mock(text=")]}'\n[\"https://maps.app.goo.gl/GoogleIssuedTest\"]")
        good=Mock(status_code=302,headers={'Location':sharing.place_url(destination)+'?skid=test'})
        with patch.object(sharing.requests,'get',side_effect=[issued,good]) as get:
            first=sharing.short_link(destination)
            second=sharing.short_link(destination)
        self.assertEqual(first,second);self.assertEqual(get.call_count,2)
        wrong=Mock(status_code=302,headers={'Location':'https://www.google.com/maps/dir/40,-73'})
        with patch.object(sharing.requests,'get',side_effect=[issued,wrong]),self.assertRaises(ValueError):
            sharing.short_link(plaza(98))

    def test_export_failure_retains_sqlite_and_partial_tiles_resume(self):
        sid=self.make_scan(1)
        with patch.object(store,'atomic_json',side_effect=OSError('disk full')):
            store.auto_export(sid)
        self.assertEqual(len(store.get_scan(sid)['plazas']),1)
        self.assertTrue(store.get_scan(sid)['export_error'])
        store.export_scan(sid)
        self.assertIsNone(store.get_scan(sid)['export_error'])
        rec=store.insert_scan(config(diameter_miles=10).model_dump(mode='json'));sid=rec['id']
        with patch.object(discovery,'fetch_tile',side_effect=[fixture(),ValueError('timeout')]),patch.object(discovery.time,'sleep'):
            discovery.run_scan(sid)
        self.assertEqual(store.get_scan(sid)['status'],'partial')
        self.assertGreater(len(store.get_scan(sid)['plazas']),0)
        completed=len(store.get_scan(sid)['completed_tiles'])
        with patch.object(discovery,'fetch_tile',return_value=fixture()) as fetch,patch.object(discovery.time,'sleep'):
            discovery.run_scan(sid)
        self.assertEqual(fetch.call_count,len(discovery.tiles(config(diameter_miles=10)))-completed)
        self.assertEqual(store.get_scan(sid)['status'],'complete')


if __name__=='__main__':unittest.main()
