from synthetic import synthetic
"""Brief silent Qt transitions with synthetic media/captions; no remote playback."""
import hashlib
import json
import os
import time
from types import SimpleNamespace
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from live_translate import integrated
from live_translate import playback
from live_translate import transport as transport
from live_translate.pipeline import ROOT
from synthetic import SyntheticWorker
from PySide6.QtCore import qInstallMessageHandler
from PySide6.QtWidgets import QApplication
from live_translate.settings import DEFAULTS
from live_translate.twitch_recovery import TwitchRecovery


def main():
    qInstallMessageHandler(lambda *unused: None)
    app = QApplication([])
    args = SimpleNamespace(url=None, delay=5, seconds=0, stop_after=0, muted=True, auto_exit=False)
    window = integrated.LocalWindow(args, store=integrated.MemorySettings(DEFAULTS))
    window.show()
    engine, panel = window.engine, window.panel
    resolutions = []
    source = dict(platform='x', epoch=time.monotonic())

    def resolve(url, height=0, fps=0):
        height, fps = height or 240, fps or 30
        resolutions.append((height, fps))
        return dict(url=f'https://synthetic.test/{height}/list', height=height, fps=fps,
                    qualities=[dict(height=h, fps=30) for h in (240, 180)])

    def fetch(url, headers, limit):
        height = int(url.split('/')[-2])
        if url.endswith('/list'):
            count = 24 if source['platform'] == 'x' else 24+int((time.monotonic()-source['epoch'])/2)
            text = '#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n'
            text += ''.join(f'#EXTINF:2,\n{i}.ts\n' for i in range(count))
            if source['platform'] != 'twitch':
                text += '#EXT-X-ENDLIST\n'
            return text.encode(), url
        index = int(url.rsplit('/', 1)[1].split('.')[0])
        return synthetic(2, offset=index*2, height=height)[0].data, url

    def until(predicate, timeout=25):
        deadline = time.monotonic()+timeout
        while not predicate() and time.monotonic() < deadline:
            app.processEvents()
            if engine.running and engine.metrics['errors']:
                raise AssertionError(engine.metrics['errors'])
            time.sleep(.01)
        assert predicate(), 'transition_check_timeout'

    def playing(generation):
        until(lambda: engine.generation == generation and not engine.finishing
              and engine.metrics['caption_visible_frames'] > 0)
        assert window.controller.state == 'playing'
        assert engine.ahead_player.audioOutput() is None
        assert window.resolution_action.isEnabled()

    def start(platform, url):
        source.update(platform=platform, epoch=time.monotonic())
        panel.content_url.setText(url)
        panel.start.click()
        playing(engine.generation)

    results = []
    engine.completed.connect(lambda result: results.append(dict(result)))
    with patch.object(playback, 'LocalWorker', SyntheticWorker), \
            patch.object(transport, 'resolve_stream', side_effect=resolve), \
            patch.object(transport, 'fetch_bounded', side_effect=fetch):
        try:
            source.update(platform='x', epoch=time.monotonic())
            panel.content_url.setText('https://x.com/i/broadcasts/1AxRnZbVpjaxl')
            panel.start.click()
            until(lambda: window._timeline_active and window.playback_timeline.slider.isEnabled()
                  and not engine.playing)
            loading_generation = engine.generation
            slider = window.playback_timeline.slider
            slider.setSliderDown(True)
            slider.setValue(2500)
            slider.setSliderDown(False)
            assert engine.finishing and engine.pending['kind'] == 'seek'
            window.playback_timeline.seek_requested.emit(12.5)
            assert engine.pending['position'] == 12.0
            playing(loading_generation+1)
            assert engine.bridge.source_origin == 12.0
            assert engine.metrics['first_video_at_s'] >= 5
            assert engine.transitions['seek'] == 1

            window.languages.activate('output', 'es')
            assert engine.finishing and engine.pending['kind'] == 'language'
            playing(engine.generation+1)
            assert engine.target_language == 'es' and engine.source_language == 'en'
            assert engine.metrics['first_video_at_s'] >= 5
            window.languages.activate('input', 'es')
            assert engine.finishing and engine.pending['kind'] == 'language'
            playing(engine.generation+1)
            assert engine.target_language == engine.source_language == 'es'
            assert engine.asr_model == 'small' and engine.transitions['language'] == 2
            assert engine.metrics['first_video_at_s'] >= 5
            generation = engine.generation
            old_playlist, old_worker = engine.playlist, engine.worker
            old_callback = engine.connections[1][1]
            old_timeline = engine.timeline
            position = engine.playlist.snapshot.select(engine.media_position()).start
            window.pause_button.click()
            panel.caption_offset.setValue(5)
            panel.resolution.combo.setCurrentIndex(panel.resolution.index_of((180, 30)))
            assert engine.finishing and old_timeline.active is None and not old_timeline.pending
            playing(generation+1)
            assert old_worker.stop.is_set() and old_playlist.stop.is_set()
            assert engine.playlist is not old_playlist
            assert engine.quality == (180, 30) and engine.bridge.source_origin == position
            assert engine.metrics['actual_quality'] == (180, 30)
            assert engine.timeline.offset == .5
            old_callback(SimpleNamespace(name='ResourceError'), 'synthetic stale callback')
            engine.worker.events.append(dict(kind='error', generation=generation, code='stale_worker'))
            engine.tick()
            assert not engine.metrics['errors'] and not engine.finishing
            playlist = engine.playlist
            window.playback_timeline.seek_requested.emit(12.5)
            playing(generation+2)
            assert engine.playlist is playlist and engine.bridge.source_origin == 12.0
            assert engine.metrics['first_video_at_s'] >= 5
            window.playback_timeline.seek_requested.emit(2.5)
            playing(generation+3)
            assert engine.playlist is playlist and engine.bridge.source_origin == 2.0
            engine.finish()
            until(lambda: not engine.running)

            start('kick', 'https://kick.com/cravoo')
            generation, playlist = engine.generation, engine.playlist
            assert window._timeline_active
            window.playback_timeline.seek_requested.emit(0)
            playing(generation+1)
            assert engine.bridge.source_origin == 0 and engine.playlist is playlist
            window.playback_timeline.live_button.click()
            playing(generation+2)
            assert engine.bridge.source_origin > 0 and engine.metrics['start_position'] == 'live'
            engine.finish()
            until(lambda: not engine.running)

            start('twitch', 'https://www.twitch.tv/hasanabi')
            generation, playlist = engine.generation, engine.playlist
            resolve_count = len(resolutions)
            assert not window._timeline_active and not engine.request_seek(0)
            window.languages.activate('input', 'en')
            assert engine.pending['kind'] == 'language' and engine.pending['position'] is None
            playing(generation+1)
            assert engine.source_language == 'en' and engine.playlist is playlist
            generation = engine.generation
            window.pause_button.click()
            engine.bridge.errors.append('source_unsupported_hls_feature_map_change')
            engine.tick()
            until(lambda: engine.retired)
            assert engine.waiting_paused and engine.generation == generation
            window.pause_button.click()
            playing(generation+1)
            assert engine.playlist is playlist and not playlist.stop.is_set()
            assert len(resolutions) == resolve_count
            assert engine.transitions['recovery'] == 1
            engine.recovery.delays = (10,)*6
            engine.bridge.errors.append('source_unsupported_hls_feature_discontinuity')
            engine.tick()
            until(lambda: engine.retired)
            assert engine.transition_timer.isActive()
            window.stop_button.click()
            until(lambda: not engine.running)
            assert engine.generation == generation+1 and not engine.transition_timer.isActive()

            # Stop while old transports are still being retired; no later generation may start.
            panel.start.click()
            until(lambda: engine.bridge is not None and engine.bridge.ready.is_set())
            generation = engine.generation
            engine.bridge.errors.append('source_unsupported_hls_feature_discontinuity')
            engine.tick()
            assert engine.finishing
            window.stop_button.click()
            until(lambda: not engine.running)
            assert engine.generation == generation
        finally:
            if engine.running:
                engine.finish('check_cleanup')
                deadline = time.monotonic()+20
                while engine.running and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(.01)
            window.close()
    assert len(results) == 4 and all(value['passed'] for value in results[:3])
    assert all(not value['errors'] and not value['cleanup_errors']
               and value['playlist_stopped'] and value['remaining_compressed_bytes'] == 0 for value in results)
    limiter = TwitchRecovery()
    assert [limiter.reserve(10) for _ in range(7)] == [1, 2, 4, 8, 15, 15, None]
    report = dict(passed=True, synthetic_media=True, synthetic_asr_translation=True,
        quality_from_pause=True, x_forward_rewind=True, kick_rewind_live=True,
        seek_during_loading=True, latest_loading_seek_wins=True,
        input_output_change_restarts_countdown=True, twitch_language_without_seek=True,
        twitch_format_recovery_same_playlist=True, recovery_waits_for_resume=True,
        stop_cancels_backoff_and_cleanup=True, stale_callbacks_rejected=True,
        sessions=results)
    (ROOT / 'outputs/local-asr-transitions-check.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
