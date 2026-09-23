import io
import json
import threading
import unittest
from unittest.mock import patch

from langbuffer.media_source import content_url, content_kind, content_platform
from langbuffer.source_extension.contracts import Quality
from langbuffer.source_extension.adapters import YouTubeLiveAdapter
from langbuffer.source_extension.registry import normalize_url
from langbuffer.extended_sources import resolve_metadata, runtime_options, safe_error, resolve
from langbuffer.paired_hls import program_times, packed_timestamp, select_tracks
from langbuffer.range_media import RangeSource
from langbuffer.file_bridge import requested_range

YT = 'https://www.youtube.com/watch?v=AbCdEf123_-'
FB = 'https://www.facebook.com/watch/?v=123456'


def formats():
    return [dict(url='https://cdn.invalid/combined', protocol='m3u8_native',
                 vcodec='avc1', acodec='mp4a', height=720, fps=30, ext='mp4')]


class SourceTests(unittest.TestCase):
    def test_extended_urls_and_existing_sources(self):
        for value, platform, kind in (
            ('https://youtu.be/AbCdEf123_-?t=3', 'youtube', 'youtube_live'),
            ('https://facebook.com/page/videos/123456/', 'facebook', 'facebook'),
            ('https://www.twitch.tv/hasanabi', 'twitch', 'twitch_live'),
            ('https://kick.com/cravoo', 'kick', 'kick_live')):
            self.assertEqual(content_platform(value), platform)
            self.assertEqual(content_kind(value), kind)
        self.assertIn('/watch/live/', content_url('https://facebook.com/watch/live/?v=123456'))

    def test_reject_aliases_and_impostors(self):
        for url in (YT+'&list=123', YT+'&v=other', 'http://youtu.be/AbCdEf123_-',
                    'https://facebook.com.evil.invalid/watch/?v=123456',
                    'https://facebook.com/share/v/abc', 'https://youtu.be/@foo/live'):
            with self.assertRaises(ValueError):
                content_url(url)

    def test_youtube_states_before_probe(self):
        for state in ('not_live', 'was_live', 'post_live', 'is_upcoming', None):
            with patch('langbuffer.extended_sources.inspect_hls') as probe:
                with self.assertRaises(ValueError):
                    resolve_metadata(YT, dict(id='AbCdEf123_-', live_status=state, formats=formats()))
                probe.assert_not_called()

    def test_identity_before_network(self):
        with patch('langbuffer.extended_sources.inspect_hls') as probe:
            with self.assertRaisesRegex(ValueError, 'content_identity_changed'):
                resolve_metadata(FB, dict(id='other', formats=formats()))
            probe.assert_not_called()

    def test_facebook_hls_requires_inspection(self):
        from langbuffer.source_extension.contracts import Kind
        with patch('langbuffer.extended_sources.inspect_hls',
                   return_value=(Kind.LIVE, dict(height=720, width=1280, fps=30, vcodec='h264', acodec='aac'))):
            selected = resolve_metadata(FB, dict(id='123456', formats=formats()))
        self.assertEqual(selected['media_kind'], 'live')
        self.assertEqual(selected['transport'], 'hls_combined')

    def test_facebook_live_hint_cannot_fallback_to_file(self):
        item = dict(formats()[0], protocol='https')
        with self.assertRaisesRegex(ValueError, 'no_supported_combined_format'):
            resolve_metadata(FB.replace('/watch/', '/watch/live/'), dict(id='123456', formats=[item]))

    def test_facebook_dash_is_explicit_error(self):
        with self.assertRaisesRegex(ValueError, 'dash_transport_unimplemented'):
            resolve_metadata(FB, dict(id='123456', formats=[dict(formats()[0], protocol='http_dash_segments')]))

    def test_paired_catalog_exact_profile(self):
        info = dict(formats=[dict(formats()[0], acodec='none', height=h, fps=f)
                            for h, f in ((720, 30), (720, 60), (1080, 60))]+[
                        dict(formats()[0], vcodec='none', acodec='aac', height=None)])
        selected = select_tracks(info, 720, 60)
        self.assertEqual((selected['height'], selected['fps']), (720, 60))
        self.assertEqual(len(selected['qualities']), 3)
        with self.assertRaisesRegex(ValueError, 'quality_unavailable'):
            select_tracks(info, 720, 25)

    def test_error_does_not_leak(self):
        self.assertEqual(safe_error(ValueError('https://signed.invalid/secret')), 'source_unavailable')
        self.assertEqual(safe_error(ValueError('source_cancelled')), 'source_cancelled')

    def test_cancel_before_worker_spawn(self):
        cancel = threading.Event()
        cancel.set()
        with patch('subprocess.Popen') as child:
            with self.assertRaisesRegex(ValueError, 'source_cancelled'):
                resolve(YT, cancel=cancel)
            child.assert_not_called()

    def test_runtime_is_bundled_and_remote_scripts_disabled(self):
        options = runtime_options()
        self.assertTrue(options['js_runtimes']['node']['path'].endswith('runtime\\javascript\\node.exe'))
        self.assertEqual(options['remote_components'], [])

    def test_clock_explicit_zone_and_relative_segments(self):
        times = program_times('#EXTM3U\n#EXT-X-PROGRAM-DATE-TIME:2026-09-22T10:00:00Z\n'
                              '#EXTINF:2,\na.ts\n#EXTINF:2,\nb.ts\n', 'https://cdn.invalid/list')
        self.assertAlmostEqual(times['https://cdn.invalid/b.ts']-times['https://cdn.invalid/a.ts'], 2)
        with self.assertRaises(ValueError):
            program_times('#EXT-X-PROGRAM-DATE-TIME:2026-09-22T10:00:00\n', '')

    def test_packed_audio_timestamp_and_bad_tags(self):
        ticks = 987654321
        body = b'com.apple.streaming.transportStreamTimestamp\0'+ticks.to_bytes(8, 'big')
        sync = lambda n: bytes((n >> 21 & 127, n >> 14 & 127, n >> 7 & 127, n & 127))
        frame = b'PRIV'+sync(len(body))+b'\0\0'+body
        tag = b'ID3\x04\x00\x00'+sync(len(frame))+frame
        self.assertEqual(packed_timestamp(tag), ticks)
        for value in (b'not id3', tag[:20], tag.replace(b'PRIV', b'TXXX')):
            with self.assertRaises(ValueError):
                packed_timestamp(value)

    def test_proxy_range_semantics(self):
        self.assertEqual(requested_range('bytes=10-20', 100), (10, 20, True))
        self.assertEqual(requested_range('bytes=98-', 100), (98, 99, True))
        self.assertEqual(requested_range('bytes=-5', 100), (95, 99, True))
        self.assertEqual(requested_range(None, 100), (0, 99, False))
        for value in ('bytes=100-', 'bytes=10-1', 'bytes=0-1,4-5', 'bytes=-0'):
            with self.assertRaises(ValueError):
                requested_range(value, 100)

    def test_remote_range_requires_206_and_resource_identity(self):
        class Response(io.BytesIO):
            status = 206
            url = 'https://cdn.invalid/file'
        responses = []
        def response(data, content_range, status=206, etag='first'):
            value = Response(data)
            value.status = status
            value.headers = {'Content-Range': content_range, 'ETag': etag}
            responses.append(value)
        response(b'a', 'bytes 0-0/100')
        response(b'ab', 'bytes 1-2/100', etag='changed')
        with patch('langbuffer.range_media.open_https', side_effect=responses):
            source = RangeSource('https://cdn.invalid/file', {})
            with self.assertRaisesRegex(ValueError, 'media_resource_changed'):
                source.read_range(1, 2)
        responses.clear()
        response(b'a', 'bytes 0-0/100', status=200)
        with patch('langbuffer.range_media.open_https', side_effect=responses):
            with self.assertRaisesRegex(ValueError, 'range_not_supported'):
                RangeSource('https://cdn.invalid/file', {})

    def test_unverified_codec_requires_decode(self):
        plan = YouTubeLiveAdapter().plan(normalize_url(YT),
            dict(id='AbCdEf123_-', live_status='is_live', formats=[dict(formats()[0], acodec=None)]))
        self.assertIn('probe_unknown_codecs', plan.requirements)


if __name__ == '__main__':
    unittest.main()

