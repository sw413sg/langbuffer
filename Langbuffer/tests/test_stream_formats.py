import unittest
from langbuffer.stream_formats import qualities, resolutions, select_hls_format, select_progressive_format
from langbuffer.seekable_hls import PlaylistSource, read_window
import threading


class StreamFormatsTests(unittest.TestCase):
    def formats(self, progressive=False):
        return {'formats': [dict(url=f'https://cdn.test/{height}/{fps}', height=height, fps=fps,
                                 protocol='https' if progressive else 'm3u8_native', ext='mp4',
                                 vcodec='h264', acodec='aac', tbr=bitrate)
                            for height, fps, bitrate in ((360, 30, 400), (720, 30, 2200),
                                                        (720, 60, 2000), (1080, 60, 4500))]}

    def test_catalog_deduplicates_heights_and_excludes_separate_tracks(self):
        info = self.formats()
        info['formats'] += [dict(info['formats'][-1], height=2160, acodec='none'),
                            dict(info['formats'][-1], height=1440, protocol='https'),
                            dict(info['formats'][-1], height=None, vcodec='none')]
        self.assertEqual(resolutions(info), [1080, 720, 360])
        self.assertEqual(resolutions(info, True), [1440])
        self.assertEqual(qualities(info), [dict(height=1080, fps=60), dict(height=720, fps=60),
                                           dict(height=720, fps=30), dict(height=360, fps=30)])

    def test_explicit_resolution_uses_best_fps_at_exact_height_for_both_transports(self):
        for progressive, select in ((False, select_hls_format), (True, select_progressive_format)):
            with self.subTest(progressive=progressive):
                selected = select(self.formats(progressive), 720)
                self.assertEqual(selected['height'], 720)
                self.assertEqual(selected['fps'], 60)
                self.assertEqual(selected['resolutions'], [1080, 720, 360])
                self.assertEqual(select(self.formats(progressive))['height'], 1080)

    def test_exact_fps_can_be_selected_within_one_resolution(self):
        selected = select_hls_format(self.formats(), 720, 30)
        self.assertEqual((selected['height'], selected['fps']), (720, 30))
        self.assertEqual(selected['qualities'][0], dict(height=1080, fps=60))
        with self.assertRaisesRegex(ValueError, '^quality_unavailable$'):
            select_hls_format(self.formats(), 720, 50)

    def test_missing_resolution_is_not_silently_replaced(self):
        for progressive, select in ((False, select_hls_format), (True, select_progressive_format)):
            with self.assertRaisesRegex(ValueError, '^resolution_unavailable$'):
                select(self.formats(progressive), 480)


    def test_switching_hls_preserves_anchor_and_rejects_incompatible_timing(self):
        first = '#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:10\n#EXTINF:2,\na.ts\n#EXTINF:2,\nb.ts\n'
        old = read_window(first, 'https://cdn.test/old/')
        for duration, error in ((2, None), (3, 'live_timeline_changed')):
            new = f'#EXTM3U\n#EXT-X-TARGETDURATION:3\n#EXT-X-MEDIA-SEQUENCE:11\n#EXTINF:{duration},\nc.ts\n#EXT-X-ENDLIST\n'
            source = PlaylistSource(lambda: dict(url='https://cdn.test/new', height=360, resolutions=[720, 360]),
                                    lambda *args: (new.encode(), args[0]), reference_window=old)
            try:
                source.start()
                if error:
                    with self.assertRaisesRegex(ValueError, error):
                        source.window(threading.Event())
                else:
                    window = source.window(threading.Event())
                    self.assertEqual(window.start, 2)
                    self.assertEqual(source.details['height'], 360)
                    self.assertEqual(source.details['resolutions'], [720, 360])
            finally:
                source.close()


if __name__ == '__main__':
    unittest.main()
