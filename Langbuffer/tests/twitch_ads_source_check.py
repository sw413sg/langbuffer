"""Offline metadata/iterator checks: no Qt, networking, media, or saved content."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from langbuffer.twitch_ads import TwitchAdBreak, TwitchAdTimeout, TwitchPlaylistSource


BASE = 'https://invalid.example/playlist.m3u8'
EPOCH = datetime(2026, 9, 22, tzinfo=timezone.utc)


def date(sequence):
    return (EPOCH+timedelta(seconds=sequence*2)).isoformat()


def ad_range(first, count, *, identifier='pod', classname='twitch-stitched-ad'):
    return (f'#EXT-X-DATERANGE:ID="{identifier}",CLASS="{classname}",'
            f'START-DATE="{date(first)}",DURATION={count*2}')


def manifest(first, count, *, ads=(), ranges=(), pdt=True, boundaries=(), maps=None,
             errors=None, live=False):
    lines = ['#EXTM3U', '#EXT-X-TARGETDURATION:2', f'#EXT-X-MEDIA-SEQUENCE:{first}', *ranges]
    for sequence in range(first, first+count):
        if sequence in boundaries:
            lines.append('#EXT-X-DISCONTINUITY')
        if maps and sequence in maps:
            lines.append(f'#EXT-X-MAP:URI="{maps[sequence]}"')
        if errors and sequence in errors:
            lines.append(errors[sequence])
        if pdt:
            lines.append(f'#EXT-X-PROGRAM-DATE-TIME:{date(sequence)}')
        lines.extend([f'#EXTINF:2,{"Amazon advertising" if sequence in ads else "live"}',
                      f'segment-{sequence}.m4s'])
    if not live:
        lines.append('#EXT-X-ENDLIST')
    return '\n'.join(lines)


class Clock:
    value = 0.0

    def __call__(self):
        return self.value


class TwitchAdsSourceTests(unittest.TestCase):
    def source(self, text, **kwargs):
        source = TwitchPlaylistSource(lambda: {'url': BASE}, lambda *_: (text.encode(), BASE), **kwargs)
        source._update(text, BASE)
        self.addCleanup(source.close)
        return source

    def entries(self, source, *, cancel=None, latest_epoch=False):
        return source.entries(None, 0, cancel or threading.Event(), lambda _: None,
                              with_init=True, latest_epoch=latest_epoch)

    def begin_ad(self, source):
        with self.assertRaises(TwitchAdBreak):
            next(self.entries(source))
        self.assertTrue(source.ad_status()['waiting'])

    def test_preroll_never_yields_ad_and_resumes_once(self):
        source = self.source(manifest(0, 4, ads={0, 1}))
        self.begin_ad(source)
        self.assertTrue(source.ad_status()['ready'])
        self.assertEqual(source.ad_status()['resume_sequence'], 2)
        output = list(self.entries(source))
        self.assertEqual([row[2].rsplit('/', 1)[1] for row in output], ['segment-2.m4s', 'segment-3.m4s'])
        self.assertFalse(source.ad_status()['waiting'])

    def test_midroll_ad_precedes_map_and_discontinuity(self):
        source = self.source(manifest(0, 6, ads={2, 3}, boundaries={2, 4},
                                      maps={0: 'content.mp4', 2: 'ad.mp4', 4: 'return.mp4'}))
        iterator = self.entries(source)
        self.assertTrue(next(iterator)[2].endswith('segment-0.m4s'))
        self.assertTrue(next(iterator)[2].endswith('segment-1.m4s'))
        with self.assertRaises(TwitchAdBreak):
            next(iterator)
        self.assertEqual(source.ad_status()['resume_sequence'], 4)
        self.assertTrue(next(self.entries(source))[2].endswith('segment-4.m4s'))

    def test_discontinuity_without_marker_is_not_an_ad(self):
        source = self.source(manifest(0, 3, boundaries={1}))
        iterator = self.entries(source)
        next(iterator)
        with self.assertRaisesRegex(ValueError, 'unsupported_hls_feature_discontinuity'):
            next(iterator)
        self.assertFalse(source.ad_status()['waiting'])

    def test_class_and_id_ranges_detect_without_title(self):
        for marker in (ad_range(0, 2), ad_range(0, 2, identifier='stitched-ad-one', classname='other')):
            with self.subTest(marker=marker):
                source = self.source(manifest(0, 4, ranges=[marker]), monotonic=Clock())
                self.begin_ad(source)
                self.assertEqual(source.ad_status()['resume_sequence'], 2)
                self.assertEqual(source.ad_status()['remaining_s'], 4)

    def test_trailing_range_and_timezone_equivalence(self):
        marker = ad_range(0, 2).replace(date(0), '2026-09-21T21:00:00-03:00')
        source = self.source(manifest(0, 4)+'\n'+marker)
        self.begin_ad(source)
        self.assertEqual(source.ad_status()['resume_sequence'], 2)

    def test_invalid_or_absent_dates_do_not_guess(self):
        for marker in (ad_range(0, 2).replace(date(0), 'invalid'),
                       ad_range(0, 2).replace('+00:00', ''), ad_range(0, 2)):
            source = self.source(manifest(0, 3, ranges=[marker], pdt=False))
            self.assertEqual(len(list(self.entries(source))), 3)
            self.assertFalse(source.ad_status()['waiting'])

    def test_title_detection_does_not_require_dates(self):
        source = self.source(manifest(0, 3, ads={0}, pdt=False))
        self.begin_ad(source)
        self.assertIsNone(source.ad_status()['remaining_s'])

    def test_unrelated_range_and_unanchored_map_change(self):
        source = self.source(manifest(0, 3, ranges=[ad_range(0, 3, classname='chapter')]))
        self.assertEqual(len(list(self.entries(source))), 3)
        source = self.source(manifest(0, 2, ranges=[ad_range(0, 4)], maps={0: 'ad.mp4'}))
        self.begin_ad(source)
        source._update(manifest(2, 2, pdt=False, maps={2: 'different.mp4'}), BASE)
        self.assertFalse(source.ad_status()['ready'])

    def test_range_end_date_update_and_partial_segment(self):
        marker = (f'#EXT-X-DATERANGE:ID="stitched-ad-pod",START-DATE="{date(0)}",'
                  f'END-DATE="{date(2)}"')
        source = self.source(manifest(0, 4, ranges=[marker]))
        self.begin_ad(source)
        self.assertEqual(source.ad_status()['resume_sequence'], 2)
        # A segment partly overlapping the explicit interval is withheld whole.
        marker = ad_range(.5, 1)
        source = self.source(manifest(0, 4, ranges=[marker]))
        self.begin_ad(source)
        self.assertEqual(source.ad_status()['resume_sequence'], 2)

    def test_multiple_ads_need_two_content_segments_after_latest(self):
        source = self.source(manifest(0, 2, ads={0, 1}))
        self.begin_ad(source)
        source._update(manifest(0, 4, ads={0, 1, 3}), BASE)
        self.assertFalse(source.ad_status()['ready'])
        source._update(manifest(0, 5, ads={0, 1, 3}), BASE)
        self.assertFalse(source.ad_status()['ready'])
        source._update(manifest(0, 6, ads={0, 1, 3}), BASE)
        self.assertEqual(source.ad_status()['resume_sequence'], 4)

    def test_existing_sequence_keeps_ad_when_marker_disappears(self):
        source = self.source(manifest(0, 2, ads={0, 1}))
        self.begin_ad(source)
        source._update(manifest(1, 2), BASE)
        self.assertFalse(source.ad_status()['ready'])
        source._update(manifest(1, 3), BASE)
        self.assertEqual(source.ad_status()['resume_sequence'], 2)

    def test_finite_range_survives_missing_tag_and_no_overlap(self):
        source = self.source(manifest(0, 2, ranges=[ad_range(0, 4)]))
        self.begin_ad(source)
        source._update(manifest(2, 2, pdt=False), BASE)
        self.assertFalse(source.ad_status()['ready'])
        self.assertEqual(source._ads, {2, 3})
        source._update(manifest(4, 2, pdt=False), BASE)
        self.assertEqual(source.ad_status()['resume_sequence'], 4)

    def test_unknown_dates_across_boundary_do_not_release_known_ad(self):
        source = self.source(manifest(0, 2, ranges=[ad_range(0, 4)]))
        self.begin_ad(source)
        source._update(manifest(2, 2, pdt=False, boundaries={2}), BASE)
        self.assertFalse(source.ad_status()['ready'])
        source._update(manifest(4, 2), BASE)
        self.assertTrue(source.ad_status()['ready'])

    def test_errors_have_priority_over_ad_and_remain_fatal_during_wait(self):
        source = self.source(manifest(0, 2, ads={0, 1},
                                      errors={0: '#EXT-X-KEY:METHOD=AES-128,URI="key"'}))
        with self.assertRaisesRegex(ValueError, 'encrypted_hls_not_supported'):
            next(self.entries(source))
        self.assertFalse(source.ad_status()['waiting'])
        source = self.source(manifest(0, 2, ads={0, 1}))
        self.begin_ad(source)
        source._update(manifest(2, 2, ads={2, 3}, errors={2: '#EXT-X-GAP'}), BASE)
        with self.assertRaisesRegex(ValueError, 'unsupported_hls_feature_gap'):
            source.ad_status()

    def test_cancel_before_read_and_while_waiting(self):
        source = self.source(manifest(0, 2, ads={0, 1}))
        cancel = threading.Event()
        cancel.set()
        self.assertEqual(list(self.entries(source, cancel=cancel)), [])
        self.assertFalse(source.ad_status()['waiting'])
        self.begin_ad(source)
        self.assertEqual(list(self.entries(source, cancel=cancel)), [])
        source.close()
        self.assertEqual(list(self.entries(source)), [])

    def test_ad_timeout_and_ready_pause_window_rollover(self):
        clock = Clock()
        source = self.source(manifest(0, 2, ads={0, 1}), monotonic=clock)
        self.begin_ad(source)
        clock.value = 359
        self.assertFalse(source.ad_status()['ready'])
        clock.value = 360
        with self.assertRaises(TwitchAdTimeout):
            source.ad_status()
        clock = Clock()
        source = self.source(manifest(0, 4, ads={0, 1}), monotonic=clock)
        self.begin_ad(source)
        clock.value = 900
        source._update(manifest(4, 2), BASE)
        self.assertEqual(source.ad_status()['resume_sequence'], 4)
        self.assertTrue(next(self.entries(source))[2].endswith('segment-4.m4s'))

    def test_run_keeps_one_resolution_and_ad_wait_replaces_stall_timeout(self):
        clock, calls = Clock(), []
        text = manifest(0, 2, ads={0, 1}, live=True)
        def resolve():
            calls.append('resolve')
            return {'url': BASE}
        def fetch(*_):
            clock.value += 10
            return text.encode(), BASE
        source = TwitchPlaylistSource(resolve, fetch, monotonic=clock, poll_interval=.001)
        self.addCleanup(source.close)
        source._update(text, BASE)
        self.begin_ad(source)
        source.start()
        source.thread.join(3)
        self.assertFalse(source.thread.is_alive())
        self.assertEqual(calls, ['resolve'])
        self.assertIsInstance(source.error, TwitchAdTimeout)
        self.assertEqual(clock.value, 360)

    def test_network_and_normal_stall_are_not_ad_retries(self):
        clock = Clock()
        text = manifest(0, 2, live=True)
        def fetch(*_):
            clock.value += 10
            return text.encode(), BASE
        source = TwitchPlaylistSource(lambda: {'url': BASE}, fetch, monotonic=clock, poll_interval=.001)
        self.addCleanup(source.close)
        source.start()
        source.thread.join(3)
        self.assertEqual(str(source.error), 'live_playlist_stalled')
        def failed(*_):
            raise OSError('offline fixture')
        source = TwitchPlaylistSource(lambda: {'url': BASE}, failed)
        self.addCleanup(source.close)
        source.start()
        source.thread.join(3)
        with self.assertRaises(OSError):
            source.ad_status()

    def test_range_metadata_is_bounded_and_seek_stays_disabled(self):
        source = self.source(manifest(0, 2, ranges=[ad_range(100+i, 1, identifier=f'pod-{i}')
                                                   for i in range(100)]))
        self.assertLessEqual(len(source._ranges), 64)
        with self.assertRaisesRegex(ValueError, 'invalid_seek_position'):
            next(source.entries('live', 0, threading.Event(), lambda _: None))


if __name__ == '__main__':
    unittest.main(verbosity=2)
