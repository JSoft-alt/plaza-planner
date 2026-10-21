import json
import unittest
import tempfile
from pathlib import Path
import store
from unittest.mock import MagicMock, patch

import requests
import discovery


def response(status, payload=None):
    result = MagicMock(status_code=status)
    result.__enter__.return_value = result
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError(response=result)
    result.iter_content.return_value = [json.dumps(payload or {'elements': []}).encode()]
    return result


class IsolatedServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.blocked_patch=patch.object(discovery,"_blocked_until",0)
        self.blocked_patch.start()
        self.original_data=store.DATA
        store.DATA=Path(self.temp.name)
    def tearDown(self):
        self.blocked_patch.stop()
        store.DATA=self.original_data
        self.temp.cleanup()


class DiscoveryServices(IsolatedServiceTest):
    def test_outage_uses_one_fallback_and_same_query(self):
        with patch.object(discovery, 'OVERPASS', 'https://primary.example/api'), \
             patch.object(discovery, 'OVERPASS_FALLBACK', 'https://secondary.example/api'), \
             patch.object(discovery.requests, 'post', side_effect=[response(502), response(200)]) as post, \
             patch.object(discovery.time, 'sleep'):
            self.assertEqual(discovery.fetch_tile((40,-74,40.01,-73.99)), [])
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[0].kwargs['data'], post.call_args_list[1].kwargs['data'])

    def test_rate_limit_or_access_denial_never_switches_provider(self):
        for status in (403,429):
            discovery._blocked_until=0
            with self.subTest(status=status), patch.object(discovery.requests, 'post', return_value=response(status)) as post:
                with self.assertRaisesRegex(ValueError, '限流'):
                    discovery.fetch_tile((40,-74,40.01,-73.99))
                self.assertEqual(post.call_count, 1)

    def test_partial_server_result_is_not_mistaken_for_complete(self):
        with patch.object(discovery.requests, 'post', return_value=response(200, {'elements': [], 'remark': 'Query timed out'})):
            with self.assertRaisesRegex(ValueError, '未完整'):
                discovery.fetch_tile((40,-74,40.01,-73.99))

    def test_both_outages_stop_after_two_attempts(self):
        with patch.object(discovery, 'OVERPASS_FALLBACK', 'https://secondary.example/api'), \
             patch.object(discovery.requests, 'post', side_effect=requests.Timeout()) as post, \
             patch.object(discovery.time, 'sleep'):
            with self.assertRaises(requests.Timeout):
                discovery.fetch_tile((40,-74,40.01,-73.99))
        self.assertEqual(post.call_count, 2)

class SearchFeedback(IsolatedServiceTest):
    def test_fetch_reports_active_provider_and_fallback_before_waiting(self):
        events=[]
        def transport(*args,**kwargs):
            self.assertTrue(events,'UI needs a provider activity event before the network wait')
            return response(502) if len(events)==1 else response(200)
        with patch.object(discovery,'OVERPASS','https://primary.example/api'), \
             patch.object(discovery,'OVERPASS_FALLBACK','https://secondary.example/api'), \
             patch.object(discovery.requests,'post',side_effect=transport), patch.object(discovery.time,'sleep'):
            discovery.fetch_tile((40,-74,40.01,-73.99),on_activity=lambda **event:events.append(event))
        self.assertEqual([event['attempt'] for event in events if event['phase']=='query'],[1,2])
        self.assertTrue(any(event.get('provider')=='secondary.example' for event in events))

    def test_recent_identical_area_reuses_data_without_network(self):
        events=[]
        bounds=(40,-74,40.01,-73.99)
        with patch.object(discovery.requests,'post',return_value=response(200,{'elements':[{'type':'node','id':1,'lat':40,'lon':-74}]})) as post:
            first=discovery.fetch_tile(bounds)
            second=discovery.fetch_tile(bounds,on_activity=lambda **event:events.append(event))
        self.assertEqual(first,second)
        self.assertEqual(post.call_count,1)
        self.assertEqual(events[0]['phase'],'cache')
        with patch.object(discovery.time,'time',return_value=discovery.time.time()+901),patch.object(discovery.requests,'post',return_value=response(200)) as post:
            discovery.fetch_tile(bounds)
            self.assertEqual(post.call_count,1,'Expired data must refresh')

    def test_cancel_does_not_start_backup_request(self):
        calls=0
        def cancelled():return calls>0
        def transport(*args,**kwargs):
            nonlocal calls
            calls+=1
            raise requests.Timeout()
        with patch.object(discovery,'OVERPASS_FALLBACK','https://secondary.example/api'),patch.object(discovery.requests,'post',side_effect=transport):
            with self.assertRaisesRegex(ValueError,'取消'):
                discovery.fetch_tile((40,-74,40.01,-73.99),cancelled=cancelled)
        self.assertEqual(calls,1)

    def test_retry_after_is_respected_without_repeated_requests(self):
        reply=response(429)
        reply.headers={'Retry-After':'120'}
        with patch.object(discovery.requests,'post',return_value=reply) as post:
            with self.assertRaisesRegex(ValueError,'120'):
                discovery.fetch_tile((40,-74,40.01,-73.99))
            with self.assertRaisesRegex(ValueError,'重试'):
                discovery.fetch_tile((40,-74,40.01,-73.99))
        self.assertEqual(post.call_count,1)


if __name__ == '__main__':
    unittest.main()
