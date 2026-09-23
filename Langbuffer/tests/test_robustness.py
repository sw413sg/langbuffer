"""Regression checks for the eight-point audit; no network or real speech."""
import hashlib
import io
import json
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from langbuffer import pipeline, playback, language_packages as packages, seekable_hls, transport
from langbuffer.instance_lock import acquire_instance


class WorkerChecks(unittest.TestCase):
    def run_worker(self, asr, translator, count=2):
        jobs, events, stop = queue.Queue(), queue.Queue(), threading.Event()
        for index in range(count):
            jobs.put((np.zeros(16000, dtype=np.float32).tobytes(), float(index)))
        jobs.put(None)
        with patch('faster_whisper.WhisperModel', return_value=asr), \
                patch('langbuffer.local_translation.LocalTranslation', return_value=translator), \
                patch.object(pipeline, 'model_environment'):
            pipeline.recognize_translate(jobs, events, stop, 7)
        return list(events.queue)

    def test_translation_exception_keeps_good_cues_and_next_block(self):
        segment = lambda text, start: SimpleNamespace(text=text, start=start, end=start+.2)
        asr = SimpleNamespace(transcribe=lambda *a, **k: (
            iter([segment('first', 0), segment('private-failing-text', .3), segment('last', .6)]), None))
        def translate(value):
            if value == 'private-failing-text':
                raise ValueError('private-failing-text must not enter diagnostics')
            return SimpleNamespace(text=value)
        events = self.run_worker(asr, SimpleNamespace(source='en', translate=translate))
        self.assertFalse(any(e['kind'] == 'error' for e in events))
        cues = [e for e in events if e['kind'] == 'cues']
        self.assertEqual([len(e['cues']) for e in cues], [2, 2])
        self.assertEqual([c['id'] for e in cues for c in e['cues']], [0, 1, 2, 3])
        warnings = [e for e in events if e['kind'] == 'warning']
        self.assertEqual(len(warnings), 2)
        self.assertEqual(warnings[0]['diagnostic']['phase'], 'translation')
        self.assertNotIn('private-failing-text', json.dumps(warnings))

    def test_recognition_value_error_recovers_on_next_block(self):
        calls = []
        def recognize(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise ValueError('private input')
            return iter([SimpleNamespace(text='synthetic', start=0, end=.5)]), None
        events = self.run_worker(SimpleNamespace(transcribe=recognize),
            SimpleNamespace(source='en', translate=lambda text: SimpleNamespace(text=text)))
        self.assertFalse(any(e['kind'] == 'error' for e in events))
        self.assertEqual([len(e['cues']) for e in events if e['kind'] == 'cues'], [0, 1])
        self.assertEqual(next(e for e in events if e['kind'] == 'warning')['diagnostic']['phase'], 'recognition')

    def test_repeated_failures_are_bounded(self):
        def recognize(*args, **kwargs):
            raise ValueError('private input')
        events = self.run_worker(SimpleNamespace(transcribe=recognize), SimpleNamespace(source='en'), 5)
        self.assertEqual(sum(e['kind'] == 'warning' for e in events), 3)
        self.assertEqual(events[-1]['code'], 'local_worker_repeated_block_failure')

    def test_load_failure_records_phase_without_message(self):
        events = queue.Queue()
        with patch('faster_whisper.WhisperModel', side_effect=ValueError('private path')), \
                patch.object(pipeline, 'model_environment'):
            pipeline.recognize_translate(queue.Queue(), events, threading.Event(), 3)
        failure = events.get_nowait()
        self.assertEqual(failure['diagnostic']['phase'], 'load_recognition')
        self.assertNotIn('private path', json.dumps(failure))

    def test_full_audio_queue_keeps_bounded_recent_work(self):
        worker = pipeline.LocalWorker.__new__(pipeline.LocalWorker)
        worker.jobs = queue.Queue(maxsize=4)
        worker.dropped_blocks, worker.dropped_audio_s = 0, 0.0
        samples = np.zeros(16000, dtype=np.float32)
        for index in range(20):
            self.assertTrue(worker.submit(samples, index))
        self.assertEqual(worker.jobs.qsize(), 4)
        self.assertEqual([worker.jobs.get_nowait()[1] for _ in range(4)], [16, 17, 18, 19])
        self.assertEqual(worker.dropped_blocks, 16)
        self.assertEqual(worker.dropped_audio_s, 16.0)

    def test_feeder_race_does_not_block_or_raise(self):
        def full(*args): raise queue.Full()
        def empty(): raise queue.Empty()
        worker = pipeline.LocalWorker.__new__(pipeline.LocalWorker)
        worker.jobs = SimpleNamespace(put_nowait=full, get_nowait=empty)
        worker.dropped_blocks, worker.dropped_audio_s = 0, 0.0
        self.assertFalse(worker.submit(np.zeros(16000, dtype=np.float32), 0))
        self.assertEqual(worker.dropped_blocks, 1)

    def test_worker_exit_preserves_exit_code(self):
        errors = []
        engine = SimpleNamespace(finishing=False, running=True, generation=1, metrics={},
            worker=SimpleNamespace(poll=lambda: [], process=SimpleNamespace(is_alive=lambda: False, exitcode=-5)),
            fail=errors.append)
        playback.LocalPlayback.tick(engine)
        self.assertEqual(errors, ['worker_exited'])
        self.assertEqual(engine.metrics['worker_exitcode'], -5)

    def test_exit_race_keeps_final_worker_diagnostic(self):
        errors, polls = [], iter([[], [dict(kind='error', generation=1,
            code='local_worker_ValueError', diagnostic=dict(phase='load_translation'))]])
        engine = SimpleNamespace(finishing=False, running=True, generation=1,
            metrics=dict(worker_diagnostics=[]),
            worker=SimpleNamespace(poll=lambda: next(polls),
                process=SimpleNamespace(is_alive=lambda: False, exitcode=1)), fail=errors.append)
        playback.LocalPlayback.tick(engine)
        self.assertEqual(errors, ['local_worker_ValueError'])
        self.assertEqual(engine.metrics['worker_diagnostics'], [dict(phase='load_translation')])

    def test_warning_preserves_playback_and_bounds_diagnostics(self):
        errors = []
        events = [dict(kind='warning', generation=1, code='local_block_ValueError',
                       diagnostic=dict(phase='translation', block=i)) for i in range(20)]
        engine = SimpleNamespace(finishing=False, running=True, generation=1, platform='twitch',
            direct_file=False, source_details=lambda: {},
            metrics=dict(worker_diagnostics=[], worker_warnings=0),
            worker=SimpleNamespace(poll=lambda: events, process=SimpleNamespace(is_alive=lambda: True)),
            bridge=SimpleNamespace(errors=[]), ahead=SimpleNamespace(errors=[]),
            playlist=SimpleNamespace(details={}), clock=SimpleNamespace(is_paused=lambda: True), fail=errors.append)
        playback.LocalPlayback.tick(engine)
        self.assertEqual(errors, [])
        self.assertEqual(engine.metrics['worker_warnings'], 20)
        self.assertEqual(len(engine.metrics['worker_diagnostics']), 8)


class LifecycleChecks(unittest.TestCase):
    def test_metrics_failure_still_completes_and_unlocks_state(self):
        class Denied:
            def __truediv__(self, value): return self
            def write_text(self, *args, **kwargs): raise PermissionError('simulated')
        completions = []
        engine = SimpleNamespace(metrics={'errors': []}, cleanup_errors=[], transitions={}, generations=[],
            totals=dict(video_frames=1, visible_audio_buffers=1, caption_visible_frames=1),
            ad_breaks_detected=0, ad_wait_s=0, session_started=0,
            running=True, finishing=True, playing=True, closing=True,
            completed=SimpleNamespace(emit=completions.append))
        with patch.object(playback, 'ROOT', Denied()):
            playback.LocalPlayback.closed(engine, dict(playlist_stopped=True, cleanup_errors=[]))
        self.assertFalse(any((engine.running, engine.finishing, engine.playing, engine.closing)))
        self.assertEqual(len(completions), 1)
        self.assertEqual(completions[0]['metrics_write_error'], 'PermissionError')

    def test_legacy_instance_lock_is_released_and_recovers_after_crash(self):
        with tempfile.TemporaryDirectory(dir=packages.WORK, prefix='lock-check-') as temporary:
            root = Path(temporary).resolve()
            self.assertTrue(root.is_relative_to(packages.WORK.resolve()))
            lock = acquire_instance(root)
            self.assertIsNotNone(lock)
            code = ('from langbuffer.instance_lock import acquire_instance; import sys; '
                    'lock=acquire_instance(sys.argv[1]); print(lock is not None)')
            def child():
                return subprocess.check_output([sys.executable, '-B', '-c', code, str(root)],
                                               timeout=10, text=True).strip()
            try:
                self.assertEqual(child(), 'False')
            finally:
                lock.unlock()
            self.assertEqual(child(), 'True')
            self.assertEqual(child(), 'True')
            # A process killed without unlocking must not permanently lock the app.
            crash = ('from langbuffer.instance_lock import acquire_instance; import os,sys; '
                     'lock=acquire_instance(sys.argv[1]); os._exit(0 if lock else 1)')
            subprocess.run([sys.executable, '-B', '-c', crash, str(root)], timeout=10, check=True)
            self.assertEqual(child(), 'True')


class HlsChecks(unittest.TestCase):
    def test_kick_retains_final_dvr_and_can_resume_growth(self):
        now = [0.0]
        final_entries, resumed = [], []
        def manifest(count):
            return ('#EXTM3U\n#EXT-X-TARGETDURATION:10\n'+
                    ''.join(f'#EXTINF:10,\n{i}.ts\n' for i in range(count))+'#EXT-X-ENDLIST\n').encode()
        class ClockStop:
            def is_set(self): return now[0] >= 40
            def wait(self, seconds):
                if now[0] == 30:
                    self_outer.assertFalse(source.snapshot.live)
                    final_entries.extend(source.entries(0, 0, threading.Event(), lambda v: None))
                if now[0] == 34:
                    resumed.append(source.snapshot.live)
                now[0] += seconds
                return self.is_set()
        self_outer = self
        source = seekable_hls.PlaylistSource(lambda: {'url': 'memory'},
            lambda *args: (manifest(360 if now[0] < 34 else 361), 'https://fixture.invalid/'), follow_endlist=True)
        source.stop = ClockStop()
        with patch.object(seekable_hls.time, 'monotonic', side_effect=lambda: now[0]):
            source.run()
        self.assertIsNone(source.error)
        self.assertEqual(len(final_entries), 360)
        self.assertEqual(final_entries[-1][0], 3590)
        self.assertEqual(resumed, [True])

    def test_format_boundary_finishes_both_buffers_without_aborting(self):
        text = ('#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\na.ts\n'
                '#EXTINF:2,\nb.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:2,\nc.ts\n#EXT-X-ENDLIST\n')
        source = seekable_hls.PlaylistSource(None, None)
        source.snapshot = seekable_hls.read_window(text, 'https://fixture.invalid/')
        source.start = lambda: None
        clock = SimpleNamespace(now=lambda: 0, wait_until=lambda *a: False)
        ahead = transport.StreamBridge(0, 0, 'relay', clock=clock)
        bridge = transport.StreamBridge(7, 0, 'x', ahead=ahead, clock=clock,
            content_url='https://x.com/i/broadcasts/1AxRnZbVpjaxl', playlist_source=source)
        fetched = []
        def fetch(url, *args):
            fetched.append(url)
            return b'synthetic transport payload', url
        try:
            with patch.object(transport, 'fetch_bounded', side_effect=fetch):
                bridge.produce()
            self.assertFalse(bridge.stop.is_set())
            self.assertEqual(bridge.errors, [])
            self.assertEqual(bridge.boundary_error, 'source_unsupported_hls_feature_discontinuity')
            self.assertEqual(len(fetched), 2)
            for stream in (ahead, bridge):
                self.assertEqual([stream.buffer.pull(.01).start for _ in range(2)], [0, 2])
                self.assertIsNone(stream.buffer.pull(.01))
        finally:
            bridge.close()
            ahead.close()


class PackageChecks(unittest.TestCase):
    def test_trickling_download_checks_cancel_after_one_raw_read(self):
        cancel = threading.Event()
        class Response(io.BytesIO):
            status, headers = 200, {'Content-Length': '2000000'}
            def geturl(self): return 'https://fixture.invalid/'
            def read(self, *args): raise AssertionError('blocking read must not be used')
            def read1(self, size):
                cancel.set()
                return b'x'
        with tempfile.TemporaryDirectory(dir=packages.WORK, prefix='download-check-') as temporary:
            self.assertTrue(Path(temporary).resolve().is_relative_to(packages.WORK.resolve()))
            target = Path(temporary)/'partial'
            with patch.object(packages.urllib.request, 'urlopen', return_value=Response()):
                with self.assertRaises(packages.DownloadCancelled):
                    packages._download('https://fixture.invalid/', target, 2000000, '', 'sha256', cancel, lambda n: None)
            self.assertEqual(target.stat().st_size, 0)

    def test_bundled_truncation_and_same_size_corruption_use_fallback(self):
        pin = {name: dict(bytes=3, sha256=hashlib.sha256(b'abc').hexdigest())
               for name in packages.BUNDLED_OPUS_FILES}
        with tempfile.TemporaryDirectory(dir=packages.WORK, prefix='opus-check-') as temporary:
            root = Path(temporary).resolve()
            self.assertTrue(root.is_relative_to(packages.WORK.resolve()))
            path = root/'translation/opus-en-es'
            path.mkdir(parents=True)
            with patch.object(packages, 'WORK', root), patch.object(packages, 'BUNDLED_OPUS_FILES', pin), \
                    patch.object(packages, '_rejected_bundled_signature', None):
                for name in pin:
                    (path/name).write_bytes(b'')
                self.assertIsNone(packages._legacy_translation())
                self.assertEqual(packages.translation_route('en', 'es')[0]['engine'], 'argos')
                for name in pin:
                    (path/name).write_bytes(b'abc')
                spec = packages._legacy_translation()
                self.assertTrue(packages.verify_bundled_package(spec))
                (path/'model.bin').write_bytes(b'bad')
                self.assertEqual(packages.translation_route('en', 'es', verify=True)[0]['engine'], 'argos')
                self.assertFalse(packages.package_ready(spec))
                self.assertEqual(packages.translation_route('en', 'es')[0]['engine'], 'argos')
                (path/'model.bin').write_bytes(b'abc')
                self.assertEqual(packages.translation_route('en', 'es', verify=True)[0]['engine'], 'opus')


if __name__ == '__main__':
    unittest.main()
