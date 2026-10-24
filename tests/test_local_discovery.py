"""Local extract searches must complete even when public Overpass is unavailable."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import requests
import osmium
import discovery
import store
from models import SearchInput


def write_fixture(root):
    routing=root/'routing';routing.mkdir(parents=True)
    region=dict(type='Feature',properties=dict(id='test-region',name='Test region',urls={'pbf':'https://download.geofabrik.de/test-region-latest.osm.pbf'}),
        geometry=dict(type='Polygon',coordinates=[[[-74,40],[-73,40],[-73,41],[-74,41],[-74,40]]]))
    store.atomic_json(routing/'regions.json',dict(type='FeatureCollection',features=[region]))
    directory=routing/'cached';directory.mkdir()
    xml=directory/'fixture.osm'
    xml.write_text('''<osm version="0.6">
<node id="1" lat="40.7495" lon="-73.9805"/><node id="2" lat="40.7495" lon="-73.9795"/>
<node id="3" lat="40.7505" lon="-73.9795"/><node id="4" lat="40.7505" lon="-73.9805"/>
<node id="11" lat="40.75" lon="-73.9801"><tag k="office" v="lawyer"/><tag k="name" v="律师事务所"/></node>
<node id="12" lat="40.75" lon="-73.9799"><tag k="amenity" v="dentist"/><tag k="name" v="牙科诊所"/></node>
<way id="20"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/><tag k="building" v="yes"/><tag k="name" v="医疗办公楼"/></way>
</osm>''',encoding='utf-8')
    with osmium.SimpleWriter(str(directory/'map.osm.pbf')) as writer:
        for obj in osmium.FileProcessor(str(xml)):writer.add(obj)
    store.atomic_json(directory/'ready.json',dict(region_id='test-region',source=region['properties']['urls']['pbf']))
    return region


class LocalDiscovery(unittest.TestCase):
    def test_office_search_completes_from_local_pbf_during_public_outage(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(store,'DATA',Path(folder)),patch.dict('os.environ',{'DISCOVERY_SOURCE':'local'}):
            store.init();write_fixture(store.DATA)
            config=SearchInput(origin=dict(lat=40.75,lng=-73.98,label='Test origin',confirmed=True,source='pin'),
                discovery_kind='offices',diameter_miles=1,min_companies=2)
            scan=store.insert_scan(config.model_dump(mode='json'))
            with patch.object(discovery,'fetch_tile',side_effect=requests.Timeout('public provider unavailable')) as network:
                discovery.run_scan(scan['id'])
            result=store.get_scan(scan['id'])
            self.assertEqual(result['status'],'complete',result['message'])
            self.assertEqual(result['plazas'][0]['company_count'],2)
            self.assertEqual(result['plazas'][0]['name'],'医疗办公楼')
            network.assert_not_called()
            self.assertTrue(list((store.DATA/'exports').glob('*.json')))

    def test_repeat_search_uses_cached_features_without_rereading_pbf(self):
        import local_discovery
        with tempfile.TemporaryDirectory() as folder,patch.object(store,'DATA',Path(folder)):
            write_fixture(store.DATA)
            config=SearchInput(origin=dict(lat=40.75,lng=-73.98,label='Test',source='pin',confirmed=True),discovery_kind='offices',diameter_miles=1)
            rules=json.loads(discovery.OFFICE_RULES.read_text())
            first,_=local_discovery.search(config,rules,lambda **e:None,lambda:False)
            with patch.object(local_discovery.osmium,'FileProcessor',side_effect=AssertionError('Cache must avoid another PBF read')):
                second,_=local_discovery.search(config,rules,lambda **e:None,lambda:False)
            self.assertEqual(discovery.qualify(first,config,rules),discovery.qualify(second,config,rules))

    def test_complete_region_coverage_is_required(self):
        import local_discovery
        from shapely.geometry import box
        with tempfile.TemporaryDirectory() as folder,patch.object(store,'DATA',Path(folder)):
            region=write_fixture(store.DATA)
            with self.assertRaisesRegex(ValueError,'完整'):
                local_discovery.select_regions({'features':[region]},box(-74,40,-72,41))

    def test_cancel_stops_before_network_or_pbf_processing(self):
        import local_discovery
        config=SearchInput(origin=dict(lat=40.75,lng=-73.98,label='Test',source='pin',confirmed=True))
        with patch.object(local_discovery,'catalog',side_effect=AssertionError('Cancelled search must not query')):
            with self.assertRaisesRegex(ValueError,'取消'):
                local_discovery.search(config,{},lambda **e:None,lambda:True)


if __name__=='__main__':unittest.main()
