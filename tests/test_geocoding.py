import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import geocoding
import store


class FreeGeocoding(unittest.TestCase):
    def setUp(self):
        self.previous = store.DATA
        self.temp = tempfile.TemporaryDirectory()
        store.DATA = Path(self.temp.name)
        store.init()
        geocoding._next_request.update(census=0, nominatim=0)
        geocoding._blocked_until.update(census=0, nominatim=0)

    def tearDown(self):
        store.DATA = self.previous
        self.temp.cleanup()

    def reply(self, payload, status=200, headers=None):
        response = Mock(status_code=status, headers=headers or {})
        response.json.return_value = payload
        return response

    def test_census_default_no_automatic_private_address_fallback(self):
        response = self.reply({'result': {'addressMatches': []}})
        with patch.object(geocoding.requests, 'get', return_value=response) as get:
            result = geocoding.lookup('123 Example Street, New York')
        self.assertEqual(result['provider'], 'census')
        self.assertEqual(result['matches'], [])
        self.assertEqual(get.call_count, 1)
        self.assertIn('census.gov', get.call_args.args[0])

    def test_normalized_queries_use_persistent_cache(self):
        response = self.reply([dict(lat='40.7346',lon='-73.8700',display_name='Queens Center',addresstype='mall')])
        with patch.object(geocoding.requests, 'get', return_value=response) as get:
            first = geocoding.lookup('Queens   Center', 'nominatim')
            store.init()
            second = geocoding.lookup('queens center', 'nominatim')
        self.assertEqual(first, second)
        self.assertEqual(get.call_count, 1)
        self.assertIn('OpenStreetMap', first['attribution'])
        self.assertEqual(first['matches'][0]['lat'], 40.7346)

    def test_uncached_queries_are_globally_paced(self):
        with patch.object(geocoding.requests, 'get', return_value=self.reply([])) as get, \
             patch.object(geocoding.time, 'monotonic', return_value=10), \
             patch.object(geocoding.time, 'sleep') as sleep:
            geocoding.lookup('Public museum London', 'nominatim')
            geocoding.lookup('Public museum Paris', 'nominatim')
        self.assertEqual(get.call_count, 2)
        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], 1.1)

    def test_rate_limit_respected_without_automatic_retries(self):
        response = self.reply([], status=429, headers={'Retry-After': '120'})
        with patch.object(geocoding.requests, 'get', return_value=response) as get:
            for query in ['Public place one', 'Public place two']:
                with self.assertRaisesRegex(ValueError, '限流'):
                    geocoding.lookup(query, 'nominatim')
        self.assertEqual(get.call_count, 1)

    def test_malformed_response_coordinates_and_missing_label_rejected(self):
        for bad in [dict(lat='nan',lon='1',display_name='Bad'), dict(lat='91',lon='1',display_name='Bad'),
                    dict(lat=True,lon='1',display_name='Bad'), dict(lat='40',lon='1',display_name='')]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                geocoding.parse_matches([bad], 'nominatim')

    def test_area_results_do_not_claim_entrance_precision(self):
        results = geocoding.parse_matches([dict(lat='51.5',lon='-.1',display_name='London',addresstype='city')], 'nominatim')
        self.assertIn('校正', results[0]['precision'])

    def test_public_service_failure_is_actionable(self):
        with patch.object(geocoding.requests, 'get', side_effect=geocoding.requests.Timeout()):
            with self.assertRaisesRegex(ValueError, '地图中放置起点'):
                geocoding.lookup('Public place failure', 'nominatim')

    def test_invalid_provider_and_query_rejected_before_network(self):
        with patch.object(geocoding.requests, 'get') as get:
            with self.assertRaises(ValueError): geocoding.lookup('Queens Center', 'paid-api')
            with self.assertRaises(ValueError): geocoding.lookup('  ', 'census')
        get.assert_not_called()


if __name__ == '__main__':
    unittest.main()
