"""Provisioning behavior without external downloads or native binaries."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import Mock, patch

import road_data
import store
import embeds
from urllib.parse import parse_qs,urlsplit


def feature(name,bounds,url=None):
    west,south,east,north=bounds
    return dict(properties=dict(id=name,name=name,urls={'pbf':url or f'https://download.geofabrik.de/{name}-latest.osm.pbf'}),
                geometry=dict(type='Polygon',coordinates=[[[west,south],[east,south],[east,north],[west,north],[west,south]]]))


class RegionSelection(unittest.TestCase):
    def test_single_region_and_whole_cross_boundary_batch(self):
        catalog={'features':[feature('parent',(-10,-10,10,10)),feature('west',(-3,-3,0,3)),feature('east',(0,-3,3,3))]}
        self.assertEqual(road_data.select_region(catalog,[(-1,0)])['id'],'west')
        self.assertEqual(road_data.select_region(catalog,[(-1,0),(1,0)])['id'],'parent')
        with self.assertRaises(ValueError):road_data.select_region(catalog,[(50,50)])

    def test_non_provider_download_sources_are_rejected(self):
        for url in ['http://download.geofabrik.de/file.osm.pbf','https://evil.example/file.osm.pbf']:
            with self.subTest(url=url),self.assertRaises(ValueError):
                road_data.select_region({'features':[feature('bad',(-1,-1,1,1),url)]},[(0,0)])


class Provisioning(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='road data with spaces ')
        self.old=store.DATA;store.DATA=Path(self.temp.name)
        self.manager=road_data.Manager('http://127.0.0.1:9')
    def tearDown(self):
        self.manager.close();store.DATA=self.old;self.temp.cleanup()

    def test_concurrent_identical_requests_have_one_background_job(self):
        gate=Event()
        with patch.object(self.manager,'prepare',side_effect=lambda *args:gate.wait(2)) as prepare:
            with ThreadPoolExecutor(max_workers=4) as pool:
                jobs=list(pool.map(lambda _:self.manager.request([(1,2),(2,3)]),range(8)))
            gate.set()
            self.manager.pool.shutdown(wait=True)
            self.assertEqual(prepare.call_count,1)
        self.assertEqual(len({j['job_id'] for j in jobs}),1)
        self.assertNotIn('points',self.manager.public(jobs[0]))

    def test_cached_graph_restart_does_not_download_or_preprocess(self):
        key='a'*24;points=[(0,0)]
        self.manager.jobs[key]=dict(job_id=key,phase='queued',started_at=time.time())
        profile=Path(self.temp.name)/'car.lua';profile.write_text('test profile')
        def start(*args):return 'http://127.0.0.1:12345'
        with patch.object(self.manager,'base_covers',return_value=False),patch.object(self.manager,'get_catalog',return_value={'features':[feature('region',(-1,-1,1,1))]}), \
             patch.object(road_data,'profile_path',return_value=profile),patch.object(road_data.shutil,'which',return_value='/installed/binary'), \
             patch.object(road_data.subprocess,'check_output',return_value='v-test'),patch.object(self.manager,'download') as download, \
             patch.object(self.manager,'command') as command,patch.object(self.manager,'start_engine',side_effect=start):
            self.manager.prepare(key,points)
            self.assertEqual(self.manager.jobs[key]['phase'],'ready')
            self.assertEqual(command.call_count,3);self.assertEqual(download.call_count,1)
            # Simulate restarting the same region from the saved ready manifest.
            self.manager.prepare(key,points)
            self.assertEqual(command.call_count,3);self.assertEqual(download.call_count,1)
            self.assertEqual([call.args[2] for call in command.call_args_list],['extract','partition','customize'])
            for call in command.call_args_list:
                self.assertNotIn(str(store.DATA),call.args[1][-1],'Native OSRM input arguments must be relative for paths containing spaces')

    def test_failed_preparation_never_publishes_ready_manifest(self):
        key='b'*24;self.manager.jobs[key]=dict(job_id=key,phase='queued',started_at=time.time())
        with patch.object(self.manager,'base_covers',return_value=False),patch.object(self.manager,'get_catalog',side_effect=ValueError('offline')):
            self.manager.prepare(key,[(0,0)])
        self.assertEqual(self.manager.jobs[key]['phase'],'failed')
        self.assertEqual(list(self.manager.root.glob('*/ready.json')),[])

    def test_cancelled_job_stops_before_download(self):
        key='c'*24;self.manager.jobs[key]=dict(job_id=key,phase='queued',started_at=time.time())
        self.manager.cancel(key)
        with patch.object(self.manager,'download') as download:self.manager.prepare(key,[(0,0)])
        self.assertEqual(self.manager.jobs[key]['phase'],'cancelled');download.assert_not_called()

    def test_eviction_preserves_an_engine_used_by_a_route(self):
        for i in range(3):
            process=Mock();process.poll.return_value=None
            self.manager.engines[str(i)]=dict(process=process,users=1 if i==0 else 0,used_at=i)
        self.manager.trim('2')
        self.manager.engines['0']['process'].terminate.assert_not_called()
        self.manager.engines['1']['process'].terminate.assert_called_once()
        self.manager.engines['2']['process'].terminate.assert_not_called()


class GoogleEmbeds(unittest.TestCase):
    def test_keyless_place_and_route_preserve_every_coordinate(self):
        points=[dict(lat=40+i*.001,lng=-74+i*.002) for i in range(17)]
        with patch.dict('os.environ',{'GOOGLE_MAPS_EMBED_KEY':''}):
            self.assertEqual(parse_qs(urlsplit(embeds.place(points[0])).query)['output'],['embed'])
            segments=embeds.route(points)
        combined=[]
        for item in segments:
            params=parse_qs(urlsplit(item['url']).query)
            coords=[params['saddr'][0]]+params['daddr'][0].split(' to:')
            if combined:self.assertEqual(coords[0],combined[-1])
            combined+=coords if not combined else coords[1:]
        self.assertEqual(combined,[embeds.coordinate(p) for p in points])

    def test_optional_official_embed_uses_only_embed_api(self):
        points=[dict(lat=40,lng=-74),dict(lat=40.1,lng=-74.1),dict(lat=40.2,lng=-74.2)]
        with patch.dict('os.environ',{'GOOGLE_MAPS_EMBED_KEY':'TEST_KEY'}):
            self.assertIn('/maps/embed/v1/place?',embeds.place(points[0]))
            route=embeds.route(points)[0]['url']
            self.assertIn('/maps/embed/v1/directions?',route)
            self.assertEqual(parse_qs(urlsplit(route).query)['waypoints'],[embeds.coordinate(points[1])])
