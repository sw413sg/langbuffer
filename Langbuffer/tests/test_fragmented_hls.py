import threading
import unittest
from unittest.mock import patch
from langbuffer.seekable_hls import read_window, PlaylistSource
from langbuffer import transport as prototype


def playlist(extra=''):
    return ('#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MAP:URI="init.mp4"\n'
            '#EXTINF:2,\na.m4s\n'+extra+'#EXTINF:2,\nb.m4s\n#EXT-X-ENDLIST\n')


class FragmentedHlsTests(unittest.TestCase):
    def rows(self, text, position=None):
        source = PlaylistSource(None, None)
        source.snapshot = read_window(text, 'https://cdn.test/list')
        return source.entries(position, 4, threading.Event(), lambda _: None, with_init=True)

    def test_initialization_url_follows_each_selected_segment(self):
        rows = list(self.rows(playlist()))
        self.assertEqual(rows, [(0, 2, 'https://cdn.test/a.m4s', 'https://cdn.test/init.mp4'),
                                (2, 2, 'https://cdn.test/b.m4s', 'https://cdn.test/init.mp4')])
        self.assertEqual(list(self.rows(playlist(), 2))[0][3], 'https://cdn.test/init.mp4')

    def test_map_change_requires_new_transport(self):
        text = playlist('#EXT-X-MAP:URI="other.mp4"\n')
        rows = self.rows(text)
        next(rows)
        with self.assertRaisesRegex(ValueError, 'unsupported_hls_feature_map_change'):
            next(rows)
        # Un salto explícito sí crea otra generación con la nueva cabecera.
        self.assertEqual(list(self.rows(text, 2))[0][3], 'https://cdn.test/other.mp4')

    def test_initialization_does_not_bypass_encryption_or_discontinuity(self):
        for extra, code in (('#EXT-X-KEY:METHOD=AES-128,URI="secret"\n', 'encrypted_hls_not_supported'),
                            ('#EXT-X-DISCONTINUITY\n', 'unsupported_hls_feature_discontinuity')):
            rows = self.rows(playlist(extra))
            next(rows)
            with self.assertRaisesRegex(ValueError, code):
                next(rows)

    def test_map_ranges_and_unknown_attributes_remain_unsupported(self):
        for value in ('URI="init.mp4",BYTERANGE="100@0"', 'BYTERANGE="100",URI="init.mp4"',
                      'URI=init.mp4', 'URI="init.mp4",UNKNOWN=1'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'unsupported_hls_feature_map'):
                list(self.rows(playlist().replace('URI="init.mp4"', value)))

    def test_one_bounded_initialization_precedes_fragments_in_both_buffers(self):
        calls = []
        def fetch(url, headers, limit):
            calls.append((url.rsplit('/', 1)[-1], limit))
            payload = {'list': playlist().encode(), 'init.mp4': b'init', 'a.m4s': b'first', 'b.m4s': b'second'}
            return payload[url.rsplit('/', 1)[-1]], url
        source = PlaylistSource(lambda: dict(url='https://cdn.test/list'), fetch)
        ahead = prototype.StreamBridge(0, 4, 'relay')
        bridge = prototype.StreamBridge(0, 4, 'x', ahead=ahead, playlist_source=source,
                                        content_url='https://x.com/i/broadcasts/fixture')
        try:
            with patch.object(prototype, 'fetch_bounded', side_effect=fetch), patch.object(bridge.clock, 'wait_until', return_value=False) as wait:
                bridge.produce()
            self.assertFalse(bridge.errors)
            self.assertEqual(bridge.content_type, 'video/mp4')
            self.assertEqual(ahead.content_type, 'video/mp4')
            self.assertEqual(calls.count(('init.mp4', 1024*1024)), 1)
            self.assertEqual(wait.call_count, 1)  # Segundo fragmento conserva su espera a 1×.
            for transport in (bridge, ahead):
                self.assertEqual(transport.buffer.pull().data, b'initfirst')
                self.assertEqual(transport.buffer.pull().data, b'second')
                self.assertIsNone(transport.buffer.pull())
        finally:
            bridge.close()
            ahead.close()
            source.close()


if __name__ == '__main__':
    unittest.main()
