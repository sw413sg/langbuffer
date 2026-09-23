"""Silent Qt validation for new source transports; real URL only with --real-url."""
import argparse
from contextlib import ExitStack
from fractions import Fraction
import io
import json
import os
import sys
from pathlib import Path
import time
import threading
from types import SimpleNamespace
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_translate import integrated, playback, transport, file_bridge
from live_translate.pipeline import ROOT
from live_translate.settings import DEFAULTS
from live_translate.resolution_selector import ResolutionSelector
from synthetic import SyntheticWorker, synthetic
from PySide6.QtCore import qInstallMessageHandler
from PySide6.QtWidgets import QApplication


def mp4(seconds=20):
    import av
    import numpy as np
    memory = io.BytesIO()
    with av.open(memory, 'w', format='mp4') as mux:
        video = mux.add_stream('libx264', rate=10)
        video.width, video.height, video.pix_fmt = 320, 180, 'yuv420p'
        video.options = {'preset': 'ultrafast', 'tune': 'zerolatency', 'g': '10'}
        audio = mux.add_stream('aac', rate=16000)
        audio.layout = 'mono'
        sample = 0
        for index in range(seconds*10):
            pixels = np.full((180, 320, 3), 40, dtype=np.uint8)
            pixels[:, index*3 % 280:index*3 % 280+30] = (10, 180, 90)
            frame = av.VideoFrame.from_ndarray(pixels, format='rgb24')
            frame.pts, frame.time_base = index, Fraction(1, 10)
            for packet in video.encode(frame): mux.mux(packet)
            while sample < (index+1)*1600:
                wave = np.sin(2*np.pi*440*np.arange(sample, sample+1024)/16000).astype(np.float32)*.01
                frame = av.AudioFrame.from_ndarray(wave.reshape(1, -1), format='fltp', layout='mono')
                frame.sample_rate, frame.pts, frame.time_base = 16000, sample, Fraction(1, 16000)
                sample += 1024
                for packet in audio.encode(frame): mux.mux(packet)
        for stream in (video, audio):
            for packet in stream.encode(None): mux.mux(packet)
    return memory.getvalue()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--real-url')
    args = parser.parse_args()
    qInstallMessageHandler(lambda *unused: None)
    app = QApplication([])
    config = SimpleNamespace(url=None, delay=10, seconds=0, stop_after=12 if args.real_url else 0,
                             muted=True, auto_exit=False)
    results = []
    with ExitStack() as stack:
        stack.enter_context(patch.object(ResolutionSelector, 'query', lambda self: None))
        window = integrated.LocalWindow(config, store=integrated.MemorySettings(DEFAULTS))
        window.show()
        engine = window.engine
        engine.completed.connect(lambda result: results.append(dict(result)))
        if not args.real_url:
            stack.enter_context(patch.object(playback, 'LocalWorker', SyntheticWorker))
            payload = mp4()
            class MemoryRange:
                def __init__(self, url, headers, stop=None):
                    self.url, self.headers, self.stop = url, dict(headers), stop
                    self.size = len(payload)
                def read_range(self, start, end):
                    assert end-start+1 <= 1024*1024
                    if self.stop.is_set():
                        raise ValueError('source_cancelled')
                    return payload[start:end+1]
            stack.enter_context(patch.object(file_bridge, 'RangeSource', MemoryRange))
            selected = dict(url='https://synthetic.invalid/file', http_headers={}, media_kind='video',
                            transport='mp4_progressive', width=320, height=180, fps=10, duration=20,
                            qualities=[dict(height=180, fps=10)], resolutions=[180])
            stack.enter_context(patch.object(playback, 'resolve_extended', return_value=selected))

        def until(predicate, timeout=85):
            deadline = time.monotonic()+timeout
            while not predicate() and time.monotonic() < deadline:
                app.processEvents()
                if engine.running and engine.metrics['errors']:
                    raise AssertionError(engine.metrics['errors'])
                time.sleep(.01)
            assert predicate(), 'source_playback_timeout'

        try:
            url = args.real_url or 'https://www.facebook.com/watch/?v=123456'
            assert engine.start(url, 10, asr_model='small.en')
            until(lambda: engine.playing and engine.metrics['video_frames'] >= 8)
            assert engine.ahead_player.audioOutput() is None
            assert engine.audio.isMuted()
            if args.real_url:
                until(lambda: not engine.running)
            else:
                assert engine.direct_file
                assert engine.player.isSeekable() and window._timeline_active
                assert engine.metrics['visible_audio_buffers'] > 0
                engine.toggle_pause()
                before = engine.player.position()
                until(lambda: engine.clock.is_paused())
                deadline = time.monotonic()+.35
                while time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(.01)
                assert abs(engine.player.position()-before) <= 100
                engine.toggle_pause()
                generation = engine.generation
                assert engine.request_seek(6)
                until(lambda: engine.generation == generation+1 and engine.playing
                      and engine.metrics['video_frames'] >= 8)
                assert engine.media_position() >= 6
                assert engine.metrics['ahead_first_pts_s'] >= 5.8
                assert engine.metrics['first_video_pts_s'] >= 5.8
                engine.finish()
                until(lambda: not engine.running)
            assert results
            result = results[-1]
            assert not result['errors'] and not result['cleanup_errors'], result
            assert result['transports_stopped'] and result['remaining_compressed_bytes'] == 0
            assert result['session_totals']['video_frames'] > 0
            assert result['session_totals']['visible_audio_buffers'] > 0
            assert result['session_totals']['ahead_audio_buffers'] > 0
            if not args.real_url:
                selected.update(transport='hls_combined', media_kind='live', duration=None,
                                fps=30, qualities=[dict(height=180, fps=30)])
                live_epoch = time.monotonic()
                def fetch(url, headers, limit):
                    if url == selected['url']:
                        count = 20+int((time.monotonic()-live_epoch)/2)
                        text = '#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n'
                        text += ''.join(f'#EXTINF:2,\n{i}.ts\n' for i in range(count))
                        return text.encode(), 'https://synthetic.invalid/list'
                    index = int(url.rsplit('/', 1)[1].split('.')[0])
                    return synthetic(2, offset=index*2, height=180)[0].data, url
                with patch.object(transport, 'fetch_bounded', side_effect=fetch):
                    assert engine.start(url, 10, asr_model='small.en')
                    until(lambda: engine.playing and engine.metrics['video_frames'] >= 8)
                    assert not engine.direct_file and not engine.request_seek(0)
                    engine.finish()
                    until(lambda: not engine.running)
                assert not results[-1]['errors'] and not results[-1]['cleanup_errors']
                entered = threading.Event()
                def blocked(url, height, fps, cancel):
                    entered.set()
                    cancel.wait(10)
                    raise ValueError('source_cancelled')
                with patch.object(playback, 'resolve_extended', side_effect=blocked):
                    assert engine.start('https://www.youtube.com/watch?v=AbCdEf123_-', 10, asr_model='small.en')
                    until(entered.is_set)
                    engine.finish()
                    until(lambda: not engine.running)
                assert not results[-1]['errors'] and not results[-1]['cleanup_errors']
                assert results[-1]['worker_stopped'] and not engine.resolver_thread.is_alive()
            report = dict(passed=True, real=bool(args.real_url), muted=True,
                          synthetic_asr=not bool(args.real_url), session=results[0],
                          additional_sessions=results[1:] if not args.real_url else [])
            destination = ROOT/'outputs'/('youtube-live-real.json' if args.real_url else 'facebook-mp4-synthetic.json')
            destination.write_text(json.dumps(report, indent=2), encoding='utf-8')
            print(json.dumps(report, indent=2))
        finally:
            if engine.running:
                engine.finish('check_cleanup')
                deadline = time.monotonic()+20
                while engine.running and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(.01)
            window.close()


if __name__ == '__main__':
    main()
