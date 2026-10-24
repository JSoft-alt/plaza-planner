import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch
import estimates
import store


class Estimates(unittest.TestCase):
    def test_download_estimate_requires_measured_rate_and_known_length(self):
        self.assertIsNone(estimates.download_eta(100,0,5))
        self.assertIsNone(estimates.download_eta(0,1000,5))
        self.assertIsNone(estimates.download_eta(100,1000,.1))
        self.assertEqual(estimates.download_eta(250,1000,5),15)

    def test_first_processing_time_is_unknown_then_uses_local_observations(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(store,'DATA',Path(folder)):
            self.assertIsNone(estimates.expected('region'))
            estimates.record('region',20);estimates.record('region',30)
            self.assertEqual(estimates.expected('region'),30)
