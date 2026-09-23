import unittest
from live_translate.media_source import (broadcast_url, x_content_kind, x_content_url,
                                         content_url, content_kind, content_platform)


class MediaSourceTests(unittest.TestCase):
    def test_kick_channel_normalizes_and_rejects_non_channels(self):
        for value in (' https://kick.com/Cravoo/?ref=share#live ',
                      'https://www.kick.com/cravoo'):
            self.assertEqual(content_url(value), 'https://kick.com/cravoo')
            self.assertEqual(content_kind(value), 'kick_live')
            self.assertEqual(content_platform(value), 'kick')
        for value in ('https://kick.com/categories', 'https://kick.com/search',
                      'https://kick.com/cravoo/videos/123', 'http://kick.com/cravoo',
                      'https://kick.com.evil.test/cravoo', 'https://user@kick.com/cravoo'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                content_url(value)

    def test_twitch_channel_normalizes_without_tracking_or_position(self):
        for value in (' https://www.twitch.tv/HasanAbi/?referrer=foo#bar ',
                      'https://m.twitch.tv/hasanabi', 'https://twitch.tv/hasanabi'):
            self.assertEqual(content_url(value), 'https://www.twitch.tv/hasanabi')
            self.assertEqual(content_kind(value), 'twitch_live')
            self.assertEqual(content_platform(value), 'twitch')
        self.assertEqual(content_platform('https://x.com/name/status/123'), 'x')
        self.assertEqual(content_kind('https://x.com/name/status/123'), 'status')
        self.assertEqual(content_kind('https://x.com/i/broadcasts/Ab123'), 'broadcast')

    def test_twitch_rejects_vods_clips_pages_and_unsafe_urls(self):
        for value in ('https://www.twitch.tv/videos/123', 'https://clips.twitch.tv/Clip',
                      'https://twitch.tv/hasanabi/clip/Clip', 'https://twitch.tv/hasanabi/videos',
                      'https://twitch.tv/directory', 'https://twitch.tv/LOGIN',
                      'https://twitch.tv', 'https://twitch.tv/',
                      'http://twitch.tv/hasanabi', 'https://twitch.tv.evil.test/hasanabi',
                      'https://user@twitch.tv/hasanabi', 'https://twitch.tv:8443/hasanabi',
                      'https://twitch.tv/a%2fb', 'https://twitch.tv/'+('a'*26)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                content_url(value)

    def test_normalizes_shared_link_without_tracking(self):
        self.assertEqual(broadcast_url(' https://www.twitter.com/i/broadcasts/Ab123/?s=20#fragment '),
                         'https://x.com/i/broadcasts/Ab123')

    def test_rejects_unsupported_pages_and_external_addresses(self):
        for value in ('', 'http://x.com/i/broadcasts/Ab123',
                      'https://x.com/user/status/123', 'https://x.com.evil.test/i/broadcasts/a',
                      'https://user@x.com/i/broadcasts/a', 'https://127.0.0.1/video',
                      'https://x.com/i/broadcasts/a/other'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                broadcast_url(value)

    def test_accepts_and_normalizes_x_status_video_pages(self):
        selected = x_content_url(
            ' https://www.twitter.com/zodchiii/status/2100183972517151017/?s=20#video ')
        self.assertEqual(selected, 'https://x.com/zodchiii/status/2100183972517151017')
        self.assertEqual(x_content_kind(selected), 'status')
        self.assertEqual(x_content_kind('https://x.com/i/broadcasts/Ab123'), 'broadcast')

    def test_x_content_rejects_non_status_pages_and_unsafe_urls(self):
        for value in ('https://x.com/zodchiii', 'https://x.com/zodchiii/status/not-a-number',
                      'https://x.com.evil.test/user/status/123',
                      'https://user@x.com/name/status/123', 'http://x.com/name/status/123',
                      'https://x.com/name/status/123/video/1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                x_content_url(value)
