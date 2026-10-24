"""Outreach candidates remain unverified; only explicit refusals exclude them."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import discovery
import store
import app
from fastapi.testclient import TestClient
from test_planner import config, fixture, plaza


class Outreach(unittest.TestCase):
    def test_confirmed_refusal_is_persistent_reversible_and_does_not_block_neighbors(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(store,'DATA',Path(folder)):
            store.init();data=fixture()
            store.set_outreach_decline('way:1','Refusing plaza')
            self.assertEqual(discovery.qualify(data,config(diameter_miles=10))[0]['id'],'way:7')
            store.init();self.assertEqual(store.outreach_declines()[0]['id'],'way:1')
            store.set_outreach_decline('way:1','Refusing plaza',False)
            self.assertIn('way:1',[p['id'] for p in discovery.qualify(data,config(diameter_miles=10))])

    def test_unknown_chain_and_general_restaurant_are_possible_candidates(self):
        rules=json.loads(discovery.RULES.read_text())
        self.assertIsNotNone(discovery.category({'amenity':'cafe','brand':'Starbucks'},rules))
        self.assertIsNotNone(discovery.category({'amenity':'restaurant','cuisine':'mexican'},rules))
        self.assertIsNotNone(discovery.category({'shop':'electronics','name':'Local Phone Repair'},rules))
        self.assertIsNone(discovery.category({'shop':'department_store','brand':"Macy's"},rules))

    def test_single_company_is_not_a_worker_dropoff_cluster(self):
        data=fixture()[:1]+[fixture()[2]]
        data[0]['tags']={'building':'office','name':'Single company building'}
        data[1]['tags']={'office':'lawyer','name':'One practice'}
        self.assertEqual(discovery.qualify(data,config(discovery_kind='offices',min_companies=1)),[])

    def test_new_batch_size_then_default_carry_with_no_duplicate_assignment(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(store,'DATA',Path(folder)):
            store.init();scan=store.insert_scan(config(batch_size=8).model_dump(mode='json'))
            places=[plaza(i) for i in range(1,22)];places[0]['company_count']=1
            store.update_scan(scan['id'],status='complete',plazas=places)
            def finish(tid):
                t=store.get_scan(scan['id'])['trips'][-1]
                end=next(p for p in store.get_scan(scan['id'])['plazas'] if p['id']==t['plaza_ids'][-1])
                store.update_trip(scan['id'],tid,route=dict(order=t['plaza_ids'],start=t['start'],end=end))
            first=store.new_trip(scan['id'],batch_size=5)
            self.assertEqual(store.new_trip(scan['id'],batch_size=9),first)
            finish(first)
            client=TestClient(app.app);headers={'x-planner-csrf':client.get('/api/bootstrap').json()['csrf']}
            for invalid in [True,0,16,2.5]:
                self.assertEqual(client.post(f"/api/scans/{scan['id']}/trips",json={'batch_size':invalid},headers=headers).status_code,422)
            response=client.post(f"/api/scans/{scan['id']}/trips",json={'batch_size':3},headers=headers)
            self.assertEqual(response.status_code,200,response.text)
            second=response.json()['trips'][-1]['id'];finish(second)
            third=store.new_trip(scan['id']);rec=store.get_scan(scan['id'])
            self.assertEqual([len(t['plaza_ids']) for t in rec['trips']],[5,3,3])
            self.assertEqual(rec['trips'][2]['start']['id'],rec['trips'][1]['route']['end']['id'])
            ids=[i for t in rec['trips'] for i in t['plaza_ids']]
            self.assertEqual(len(ids),len(set(ids)))
            self.assertNotIn('way:1',ids)
            self.assertEqual(rec['last_batch_size'],3)

