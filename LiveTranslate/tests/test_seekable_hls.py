import threading
import unittest
from live_translate.seekable_hls import read_window, PlaylistSource
from synthetic import playlist
from live_translate import transport as prototype


def window(sequence=0, count=100, previous=None, end=False):
    return read_window(playlist(sequence, count, end), 'https://example.test/list',
                       previous)


class SeekableHlsTests(unittest.TestCase):
    def source(self, snapshot):
        source = PlaylistSource(None, None)
        source.snapshot = snapshot
        return source

    def test_hours_available_and_selection_snaps_back_without_future_access(self):
        current = window(count=7200)
        self.assertEqual(current.end-current.start, 14400)
        self.assertEqual(current.select(current.end-3600.5).start, 10798)
        self.assertEqual(current.select('live').start, 14394)

    def test_default_live_and_replay_have_distinct_origins(self):
        self.assertEqual(window().select().start, 194)
        self.assertEqual(window(end=True).select().start, 0)

    def test_rolling_window_preserves_timeline_identity(self):
        first = window(100)
        second = window(102, previous=first)
        self.assertEqual(second.start, 4)
        self.assertEqual(second.end, 204)
        self.assertEqual(second.select(30).sequence, first.select(30).sequence)

    def test_expired_and_future_selection_are_rejected(self):
        current = window(5, previous=window())
        for value in (0, current.end+1, float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                current.select(value)

    def test_reset_gap_and_duration_change_rejected(self):
        for sequence in (1, 500):
            with self.assertRaises(ValueError):
                window(sequence, previous=window(10))
        changed = playlist(10, 100).replace('#EXTINF:2,', '#EXTINF:3,', 1)
        with self.assertRaisesRegex(ValueError, 'live_timeline_changed'):
            read_window(changed, 'https://example.test/list', window(10))

    def test_backfill_continues_through_refresh_with_zero_based_local_clock(self):
        source = self.source(window(10, 4))
        origin = []
        rows = source.entries(2, 8, threading.Event(), origin.append)
        first = next(rows)
        source.snapshot = window(11, 6, previous=source.snapshot, end=True)
        result = [first, *rows]
        self.assertEqual(origin, [2])
        self.assertEqual([row[0] for row in result], [0, 2, 4, 6])
        self.assertEqual([row[2].rsplit('/', 1)[1] for row in result], ['11.ts', '12.ts', '13.ts', '14.ts'])

    def test_expiry_during_read_never_jumps_to_live(self):
        source = self.source(window(10, 4))
        rows = source.entries(0, 0, threading.Event(), lambda _: None)
        next(rows)
        source.snapshot = window(13, 4, previous=source.snapshot)
        with self.assertRaisesRegex(ValueError, 'live_window_expired'):
            next(rows)

    def test_cancel_while_waiting_for_server(self):
        source = self.source(window(10, 3))
        stop = threading.Event()
        rows = source.entries('live', 0, stop, lambda _: None)
        for _ in range(3):
            next(rows)
        stop.set()
        self.assertEqual(list(rows), [])

    def test_server_end_converts_to_replay_and_finishes(self):
        source = self.source(window(10, 3))
        rows = source.entries(2, 0, threading.Event(), lambda _: None)
        next(rows)
        source.snapshot = window(10, 3, previous=source.snapshot, end=True)
        self.assertEqual(len(list(rows)), 1)

    def test_independent_reader_updates_metadata_without_media_or_playback_clock(self):
        calls = []
        def fetch(url, headers, limit):
            calls.append(limit)
            return playlist(0, 4000, True).encode(), 'https://example.test/list'
        source = PlaylistSource(lambda: {'url': 'manifest'}, fetch)
        source.start()
        current = source.window(threading.Event())
        source.close()
        self.assertEqual(current.end, 8000)
        self.assertEqual(calls, [2*1024*1024])
        self.assertFalse(source.thread.is_alive())

    def test_follow_endlist_treats_growing_snapshots_as_live(self):
        payloads = iter((playlist(0, 3, True), playlist(0, 4, True)))
        source = PlaylistSource(lambda: {'url': 'manifest'},
            lambda *args: (next(payloads).encode(), 'https://example.test/list'),
            follow_endlist=True)
        try:
            source.start()
            first = source.window(threading.Event())
            self.assertTrue(first.live)
            with source.condition:
                source.condition.wait_for(lambda: source.snapshot.end > first.end, timeout=4)
            self.assertGreater(source.snapshot.end, first.end)
        finally:
            source.close()

    def test_follow_endlist_polls_metadata_at_bounded_short_interval(self):
        source = PlaylistSource(lambda: {'url': 'manifest'},
            lambda *args: (playlist(0, 3, True).encode(), 'https://example.test/list'),
            follow_endlist=True)
        waits = []
        source.stop.wait = lambda interval: waits.append(interval) or True
        source.run()
        self.assertEqual(waits, [1.0])

    def test_kick_length_segments_remain_bounded_but_are_supported(self):
        current = read_window('#EXTM3U\n#EXT-X-TARGETDURATION:13\n#EXTINF:12.516,\na.ts\n#EXT-X-ENDLIST\n',
                              'https://example.test/list')
        self.assertIsNone(current.segments[0].error)
        too_long = read_window('#EXTM3U\n#EXT-X-TARGETDURATION:16\n#EXTINF:15.1,\na.ts\n#EXT-X-ENDLIST\n',
                               'https://example.test/list')
        self.assertEqual(too_long.segments[0].error, 'invalid_segment_duration')

    def test_later_unsupported_media_does_not_hide_archive_and_seek_can_start_new_epoch(self):
        text = ('#EXTM3U\n#EXT-X-TARGETDURATION:17\n#EXTINF:2,\na.ts\n'
                '#EXTINF:16.55,\nb.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:2,\nc.ts\n#EXT-X-ENDLIST')
        current = read_window(text, 'https://example.test/list')
        source = self.source(current)
        rows = source.entries(0, 0, threading.Event(), lambda _: None)
        self.assertEqual(next(rows)[1], 2)
        with self.assertRaisesRegex(ValueError, 'invalid_segment_duration'):
            next(rows)
        later = list(source.entries(18.55, 0, threading.Event(), lambda _: None))
        self.assertEqual(len(later), 1)
        self.assertTrue(later[0][2].endswith('c.ts'))

    def test_continuous_playback_stops_at_discontinuity_without_skipping(self):
        text = playlist(0, 2, True).replace('#EXTINF:2,\n1.ts', '#EXT-X-DISCONTINUITY\n#EXTINF:2,\n1.ts')
        source = self.source(read_window(text, 'https://example.test/list'))
        rows = source.entries(0, 0, threading.Event(), lambda _: None)
        next(rows)
        with self.assertRaisesRegex(ValueError, 'unsupported_hls_feature_discontinuity'):
            next(rows)

    def test_map_requires_a_consumer_that_supports_initialization(self):
        text = playlist(0, 2, True).replace('#EXTINF:', '#EXT-X-MAP:URI="init"\n#EXT-X-KEY:METHOD=NONE\n#EXTINF:', 1)
        source = self.source(read_window(text, 'https://example.test/list'))
        with self.assertRaisesRegex(ValueError, 'unsupported_hls_feature_map'):
            next(source.entries(2, 0, threading.Event(), lambda _: None))
