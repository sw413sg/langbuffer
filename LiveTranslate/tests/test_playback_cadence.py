import unittest
from live_translate.playback_cadence import PlaybackCadence


class CadenceTests(unittest.TestCase):
    def test_freeze_visible_despite_catchup_and_continuous_pts(self):
        cadence = PlaybackCadence()
        for wall, pts in [(10, 0), (10.033, 33000), (11.033, 66000), (11.034, 99000)]:
            cadence.observe(wall, pts)
        result = cadence.summary()
        self.assertEqual(result['gaps_over_200ms'], 1)
        self.assertEqual(result['maximum_callback_gap_ms'], 1000)
        self.assertEqual(result['first_gaps'][0]['pts_step_ms'], 33)

    def test_bounded_gap_details_and_unknown_pts(self):
        cadence = PlaybackCadence(capacity=2)
        for i in range(100):
            cadence.observe(i, -1)
        result = cadence.summary()
        self.assertEqual(result['gaps_over_200ms'], 99)
        self.assertEqual(len(result['first_gaps']), 2)
        self.assertIsNone(result['first_gaps'][0]['pts_step_ms'])
