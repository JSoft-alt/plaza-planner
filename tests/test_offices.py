"""Office discovery counts mapped organizations inside real outlines, never clusters."""
import unittest
from unittest.mock import patch

from pydantic import ValidationError
import discovery
from models import SearchInput, projection


def config(**changes):
    return SearchInput.model_validate(dict(origin=dict(lat=40.75,lng=-73.98,label='Start',confirmed=True,source='pin'),
        discovery_kind='offices',min_companies=2,diameter_miles=1) | changes)


def fixture():
    _, inverse = projection(40.75,-73.98)
    def node(i,x,y,tags):
        lng,lat=inverse(x,y)
        return dict(type='node',id=i,lon=lng,lat=lat,tags=tags)
    def building(i,x,tags):
        return dict(type='way',id=i,tags=tags,geometry=[dict(zip(('lon','lat'),inverse(x+a,b)))
            for a,b in [(-60,-60),(60,-60),(60,60),(-60,60),(-60,-60)]])
    return [building(1,0,{'building':'yes','name':'Professional Building'}),
            node(11,0,0,{'office':'lawyer','name':'Law Firm'}),
            node(12,1,0,{'office':'lawyer','name':'Other Law Firm'}),
            node(13,20,0,{'amenity':'dentist','name':'Dental Practice'}),
            node(14,21,0,{'amenity':'dentist','name':'Dental Practice'}),
            node(15,30,0,{'office':'company','name':'Software Company'}),
            node(16,32,0,{'shop':'hairdresser','name':'Salon'}),
            building(2,250,{'building':'hospital','amenity':'hospital','name':'Hospital'}),
            node(21,250,0,{'healthcare':'hospital','name':'Hospital'}),
            building(3,450,{'building':'office','name':'Empty mapped building'}),
            node(31,440,0,{'disused:office':'company','name':'Closed'}),
            node(40,-250,0,{'amenity':'clinic','name':'Standalone Clinic'}),
            node(41,-252,0,{'office':'lawyer','name':'Nearby Standalone Law Firm'})]


class Offices(unittest.TestCase):
    def test_distinct_tenants_in_one_building_and_no_retail_counts(self):
        results=discovery.qualify(fixture(),config())
        self.assertEqual(len(results),1)
        self.assertEqual(results[0]['id'],'way:1')
        self.assertEqual(results[0]['company_count'],4)
        self.assertEqual(results[0]['entity_kind'],'building')
        self.assertNotIn('shop_count',results[0])
        self.assertEqual(results[0]['provenance']['eligible_ids'],['node:11','node:12','node:13','node:15'])
        self.assertEqual(discovery.qualify(fixture(),config(min_companies=5)),[])

    def test_standalone_hospital_counts_once_and_nearby_nodes_are_not_clustered(self):
        results=discovery.qualify(fixture(),config(min_companies=1))
        counts={r['id']:r['company_count'] for r in results}
        self.assertEqual(counts,{'way:1':4})
        self.assertNotIn('way:3',counts)

    def test_office_zone_uses_mapped_boundary(self):
        elements=fixture()[:6]
        elements[0]['tags']={'landuse':'commercial','name':'Office Zone'}
        result=discovery.qualify(elements,config())[0]
        self.assertEqual(result['entity_kind'],'office_zone')
        self.assertEqual(result['company_count'],4)

    def test_matching_name_in_different_suites_is_not_merged(self):
        elements=fixture()[:3]
        elements[1]['tags'].update(name='Company',**{'addr:unit':'101'})
        elements[2]['tags'].update(name='Company',**{'addr:unit':'202'})
        self.assertEqual(discovery.qualify(elements,config())[0]['company_count'],2)

    def test_rules_include_other_companies_without_retail_chain_exclusions(self):
        elements=fixture()[:3]
        elements[1]['tags']={'office':'company','brand':'Apple','name':'Apple Office'}
        self.assertEqual(discovery.qualify(elements,config(min_companies=2))[0]['company_count'],2)

    def test_legacy_configuration_and_strict_minimum(self):
        legacy=config().model_dump();legacy.pop('discovery_kind');legacy.pop('min_companies')
        self.assertEqual(SearchInput.model_validate(legacy).discovery_kind,'retail')
        for changes in [dict(min_companies=0),dict(min_companies=1.5),dict(discovery_kind='unknown')]:
            with self.assertRaises(ValidationError):config(**changes)

    def test_office_queries_have_distinct_cache_keys_and_generic_building_coverage(self):
        from test_discovery_services import response
        import tempfile
        from pathlib import Path
        import store
        with tempfile.TemporaryDirectory() as folder,patch.object(store,'DATA',Path(folder)),patch.object(discovery,'_blocked_until',0),patch.object(discovery.requests,'post',return_value=response(200,{'elements':[]})) as post:
            discovery.fetch_tile((40,-74,40.001,-73.999))
            discovery.fetch_tile((40,-74,40.001,-73.999),discovery_kind='offices')
            self.assertEqual(post.call_count,2)
            query=post.call_args.kwargs['data']['data']
            self.assertIn('["office"]',query)
            self.assertIn('["building"]["building"!="no"]',query)
            self.assertNotIn('["shop"]',query)
