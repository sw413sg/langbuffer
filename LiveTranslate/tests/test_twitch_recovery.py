import threading
import unittest
from unittest.mock import patch

from live_translate.seekable_hls import PlaylistSource, read_window
from live_translate.twitch_recovery import TwitchRecovery
from synthetic import playlist
from live_translate import transport as prototype


class TwitchRecoveryTests(unittest.TestCase):
    def test_retries_back_off_and_are_bounded_in_rolling_window(self):
        policy = TwitchRecovery()
        self.assertEqual([policy.reserve(i) for i in range(6)], [1, 2, 4, 8, 15, 15])
        self.assertIsNone(policy.reserve(179))
        self.assertEqual(policy.reserve(180), 15)
        self.assertIsNone(policy.reserve(180.5))
        self.assertEqual(policy.reserve(361), 1)

    def test_latest_epoch_clips_margin_at_map_or_clock_change(self):
        for tag in ('#EXT-X-MAP:URI="new.mp4"\n', '#EXT-X-DISCONTINUITY\n'):
            text = playlist(100, 10).replace('#EXTINF:2,\n108.ts', tag+'#EXTINF:2,\n108.ts')
            w = read_window(text, 'https://cdn.test/list')
            self.assertEqual(w.select().sequence, 107)
            self.assertEqual(w.select_latest_epoch().sequence, 108)
            source = PlaylistSource(None, None)
            source.snapshot = w
            origin = []
            rows = list(source.entries(None, 4, threading.Event(), origin.append,
                                       with_init=True, latest_epoch=True))
            self.assertEqual(origin, [16])
            self.assertEqual([row[0] for row in rows], [0, 2])

    def test_latest_epoch_uses_final_boundary_and_preserves_margin_when_possible(self):
        text = playlist(100, 10).replace('#EXTINF:2,\n102.ts', '#EXT-X-MAP:URI="a"\n#EXTINF:2,\n102.ts')
        w = read_window(text, 'https://cdn.test/list')
        self.assertEqual(w.select_latest_epoch().sequence, 107)
        text = text.replace('#EXTINF:2,\n108.ts', '#EXT-X-MAP:URI="b"\n#EXTINF:2,\n108.ts')
        text = text.replace('#EXTINF:2,\n109.ts', '#EXT-X-DISCONTINUITY\n#EXTINF:2,\n109.ts')
        self.assertEqual(read_window(text, 'https://cdn.test/list').select_latest_epoch().sequence, 109)

    def test_latest_epoch_does_not_bypass_encryption_or_byte_ranges(self):
        for tag, code in (('#EXT-X-KEY:METHOD=AES-128,URI="secret"', 'encrypted'),
                          ('#EXT-X-BYTERANGE:100@0', 'byterange')):
            text = playlist(100, 10).replace('#EXTINF:2,\n108.ts',
                '#EXT-X-DISCONTINUITY\n'+tag+'\n#EXTINF:2,\n108.ts')
            source = PlaylistSource(None, None)
            source.snapshot = read_window(text, 'https://cdn.test/list')
            rows = source.entries(None, 2, threading.Event(), lambda _: None,
                                  with_init=True, latest_epoch=True)
            with self.assertRaisesRegex(ValueError, code):
                next(rows)

    def test_new_map_encountered_after_start_still_stops_transport(self):
        source = PlaylistSource(None, None)
        source.snapshot = read_window(playlist(100, 3), 'https://cdn.test/list')
        rows = source.entries(None, 0, threading.Event(), lambda _: None,
                              with_init=True, latest_epoch=True)
        next(rows)
        changed = playlist(100, 4).replace('#EXTINF:2,\n101.ts',
                                           '#EXT-X-MAP:URI="new"\n#EXTINF:2,\n101.ts')
        source.snapshot = read_window(changed, 'https://cdn.test/list', source.snapshot)
        with self.assertRaisesRegex(ValueError, 'map_change'):
            next(rows)

    def test_twitch_transport_selects_new_epoch_without_public_dvr(self):
        source = PlaylistSource(None, None)
        text = playlist(100, 10, True).replace('#EXTINF:2,\n108.ts',
                                             '#EXT-X-MAP:URI="new"\n#EXTINF:2,\n108.ts')
        source.snapshot = read_window(text, 'https://cdn.test/list')
        source.start = lambda: None
        bridge = prototype.StreamBridge(0, 4, 'twitch', content_url='https://twitch.tv/catsen',
                                         playlist_source=source)
        self.addCleanup(bridge.close)
        calls = []
        def fetch(url, headers, limit):
            calls.append(url.rsplit('/', 1)[-1])
            return b'init' if url.endswith('/new') else b'media', url
        with patch.object(prototype, 'fetch_bounded', side_effect=fetch), patch.object(bridge.clock, 'wait_until', return_value=False):
            bridge.produce()
        self.assertEqual(calls, ['new', '108.ts', '109.ts'])
        self.assertFalse(bridge.errors)
        self.assertEqual(bridge.buffer.pull().data, b'initmedia')
        self.assertEqual(bridge.source_origin, 16)

    def test_recovery_on_same_playlist_passes_boundary_once_and_keeps_advancing(self):
        source = PlaylistSource(None, None)
        initial = playlist(100, 3).replace('#EXTINF:', '#EXT-X-MAP:URI="old"\n#EXTINF:', 1)
        source.snapshot = read_window(initial, 'https://cdn.test/list')
        cancelled = threading.Event()
        rows = source.entries(None, 0, cancelled, lambda _: None,
                              with_init=True, latest_epoch=True)
        next(rows)
        changed = playlist(100, 5).replace('#EXTINF:', '#EXT-X-MAP:URI="old"\n#EXTINF:', 1)
        changed = changed.replace('#EXTINF:2,\n103.ts',
            '#EXT-X-DISCONTINUITY\n#EXT-X-MAP:URI="new"\n#EXTINF:2,\n103.ts')
        source.snapshot = read_window(changed, 'https://cdn.test/list', source.snapshot)
        next(rows)
        next(rows)
        with self.assertRaisesRegex(ValueError, 'discontinuity'):
            next(rows)
        cancelled.set()  # Cancelar la generación anterior no detiene metadatos.
        self.assertFalse(source.stop.is_set())
        origin = []
        recovered = source.entries(None, 0, threading.Event(), origin.append,
                                   with_init=True, latest_epoch=True)
        self.assertEqual(next(recovered), (0, 2, 'https://cdn.test/103.ts', 'https://cdn.test/new'))
        self.assertEqual(origin, [6])
        # La frontera sale de la ventana; misma línea temporal y cabecera.
        later = playlist(104, 4, True).replace('#EXTINF:', '#EXT-X-MAP:URI="new"\n#EXTINF:', 1)
        source.snapshot = read_window(later, 'https://cdn.test/list', source.snapshot)
        remaining = list(recovered)
        self.assertEqual([row[0] for row in remaining], [2, 4, 6, 8])
        self.assertEqual([row[2].rsplit('/', 1)[1] for row in remaining],
                         ['104.ts', '105.ts', '106.ts', '107.ts'])

    def test_retained_playlist_error_is_not_hidden_by_new_consumer(self):
        source = PlaylistSource(None, None)
        source.snapshot = read_window(playlist(100, 3), 'https://cdn.test/list')
        source.error = ValueError('live_playlist_stalled')
        with self.assertRaisesRegex(ValueError, 'live_playlist_stalled'):
            next(source.entries(None, 0, threading.Event(), lambda _: None,
                                with_init=True, latest_epoch=True))


if __name__ == '__main__':
    unittest.main()
