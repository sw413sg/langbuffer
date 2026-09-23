"""Silent Qt ad-boundary checks: generated fMP4 in RAM, synthetic ASR, no network.

Exercises the separate local prototype, including its original window/controller.
No preference, media, transcript, or result files are written by this verifier.
"""
from datetime import datetime, timedelta, timezone
import json
import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from live_translate import integrated
from live_translate import playback
from live_translate import transport as transport
from synthetic import SyntheticWorker
from synthetic import fragment
from PySide6.QtCore import qInstallMessageHandler
from PySide6.QtMultimedia import QAudioOutput
from PySide6.QtWidgets import QApplication
from live_translate.settings import DEFAULTS


class MemoryMetrics:
    """Intercept the backend's normal metrics output without touching the disk."""
    def __init__(self):
        self.values = []

    def __truediv__(self, unused):
        return self

    def write_text(self, text, **unused):
        self.values.append(json.loads(text))
        return len(text)


class SyntheticSource:
    """Append-only metadata with conspicuous advertisement URLs and map changes."""
    def __init__(self):
        self.lock = threading.Lock()
        self.rows = []
        self.resolutions = 0
        self.downloads = []
        self.payloads = {}
        self.headers = {height: fragment(0, height)[0] for height in (180, 240)}

    def reset(self):
        with self.lock:
            self.rows.clear()

    def append(self, count, *, advertisement=False, group='live-a'):
        with self.lock:
            for _ in range(count):
                sequence = len(self.rows)
                # Each commercial has its own initialization and DATERANGE.
                name = f'ad-{sequence}' if advertisement else group
                self.rows.append((sequence, advertisement, name))

    def resolve(self, *unused):
        self.resolutions += 1
        return dict(url='https://synthetic.test/list', height=180, fps=30,
                    qualities=[dict(height=180, fps=30), dict(height=240, fps=30)])

    def fetch(self, url, headers, limit):
        assert url.startswith('https://synthetic.test/'), 'unexpected_remote_request'
        if url.endswith('/list'):
            with self.lock:
                rows = tuple(self.rows)
            lines = ['#EXTM3U', '#EXT-X-TARGETDURATION:2', '#EXT-X-MEDIA-SEQUENCE:0']
            previous = None
            for sequence, advertisement, group in rows:
                if previous != group:
                    if previous is not None:
                        lines.append('#EXT-X-DISCONTINUITY')
                    lines.append(f'#EXT-X-MAP:URI="https://synthetic.test/{group}/init"')
                when = datetime(2026, 1, 1, tzinfo=timezone.utc)+timedelta(seconds=sequence*2)
                stamp = when.isoformat().replace('+00:00', 'Z')
                lines.append(f'#EXT-X-PROGRAM-DATE-TIME:{stamp}')
                if advertisement:
                    lines.append(f'#EXT-X-DATERANGE:ID="stitched-ad-{sequence}",'
                                 f'CLASS="twitch-stitched-ad",START-DATE="{stamp}",DURATION=2.0')
                # Alternating title markers also exercise DATERANGE-only ads.
                title = 'Amazon' if advertisement and sequence % 2 == 0 else 'live'
                lines.extend((f'#EXTINF:2,{title}', f'https://synthetic.test/{group}/{sequence}.m4s'))
                previous = group
            return ('\n'.join(lines)+'\n').encode(), url
        group, name = url.split('/')[-2:]
        self.downloads.append((group, name))
        assert not group.startswith('ad-'), 'advertisement_bytes_requested'
        height = 180 if group == 'live-a' else 240
        if name == 'init':
            return self.headers[height], url
        sequence = int(name.split('.')[0])
        key = (sequence, height)
        if key not in self.payloads:
            self.payloads[key] = fragment(sequence*2, height)[1]
        data = self.payloads[key]
        assert len(data) <= limit
        return data, url


def main():
    from live_translate.twitch_ads import TwitchAdTimeout, TwitchPlaylistSource

    qInstallMessageHandler(lambda *unused: None)
    app = QApplication([])
    args = SimpleNamespace(url=None, delay=5, seconds=0, stop_after=0,
                           muted=True, auto_exit=False)
    window = integrated.LocalWindow(args, store=integrated.MemorySettings(DEFAULTS))
    window.show()
    engine = window.engine
    source, metrics = SyntheticSource(), MemoryMetrics()
    results, notices, workers, playlists, outputs = [], [], [], [], []
    engine.completed.connect(lambda value: results.append(dict(value)))
    engine.advertisement.connect(lambda value: notices.append(dict(value)))

    class FastSource(TwitchPlaylistSource):
        def __init__(self, *args, **kwargs):
            kwargs['poll_interval'] = .05
            super().__init__(*args, **kwargs)
            playlists.append(self)

    class TrackedWorker(SyntheticWorker):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            workers.append(self)

    class MutedAudio(QAudioOutput):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            super().setMuted(True)
            outputs.append(self)

        def setMuted(self, unused):
            super().setMuted(True)

    test_started = time.monotonic()

    def until(predicate, timeout=12, expected_errors=()):
        deadline = min(time.monotonic()+timeout, test_started+58)
        while not predicate() and time.monotonic() < deadline:
            app.processEvents()
            if engine.running and any(code not in expected_errors for code in engine.metrics['errors']):
                raise AssertionError(engine.metrics['errors'])
            time.sleep(.01)
        assert predicate(), ('ad_playback_check_timeout', window.controller.state,
                             engine.generation, engine.pending, engine.metrics.get('errors'))

    def settle(seconds=.2):
        deadline = time.monotonic()+seconds
        until(lambda: time.monotonic() >= deadline, timeout=seconds+1)

    def waiting():
        return (engine.running and engine.retired and engine.pending is not None
                and engine.pending['kind'] == 'advertisement')

    def captions_playing(generation):
        until(lambda: engine.generation == generation and not engine.finishing
              and engine.metrics['caption_visible_frames'] > 0)
        assert window.controller.state == 'playing'
        assert engine.ahead_player.audioOutput() is None and engine.audio.isMuted()
        assert engine.metrics['first_video_at_s'] >= engine.delay_s
        first_pts = engine.metrics['last_video_pts_s']
        until(lambda: engine.metrics.get('last_video_pts_s', first_pts)-first_pts >= .5)

    window.panel.resolution.timer.timeout.disconnect()
    window.panel.resolution.timer.timeout.connect(lambda: None)
    with patch.object(playback, 'LocalWorker', TrackedWorker), \
            patch.object(playback, 'TwitchPlaylistSource', FastSource), \
            patch.object(playback, 'QAudioOutput', MutedAudio), \
            patch.object(playback, 'ROOT', metrics), \
            patch.object(transport, 'resolve_stream', side_effect=source.resolve), \
            patch.object(transport, 'fetch_bounded', side_effect=source.fetch):
        try:
            # Several preroll commercials/maps must cause one waiting episode.
            source.append(2, advertisement=True)
            assert engine.start('https://www.twitch.tv/hasanabi', 5)
            first_generation = engine.generation
            first_timeline, first_worker = engine.timeline, engine.worker
            first_callback = engine.connections[1][1]
            until(waiting)
            playlist = engine.playlist
            assert not source.downloads and engine.metrics['video_frames'] == 0
            assert first_worker.stop.is_set() and not first_timeline.pending
            assert first_timeline.active is None and not window.caption_overlay.translation.text()
            assert window.controller.state == 'preparing' and window.wait_label.text()
            assert len(engine.recovery.attempts) == 0

            source.append(1)
            settle()
            assert engine.generation == first_generation and waiting()
            assert not playlist.ad_status()['ready'] and not source.downloads
            source.append(1)
            until(lambda: engine.generation == first_generation+1 and not engine.finishing)
            # Enough original material for actual Qt decoding and synthetic cues.
            source.append(3)
            captions_playing(first_generation+1)
            assert engine.playlist is playlist and source.resolutions == 1
            assert engine.bridge.source_origin == 4.0
            assert engine.transitions['advertisement'] == 1
            first_callback(SimpleNamespace(name='ResourceError'), 'obsolete callback')
            before_pages = engine.metrics['translated_pages']
            engine.worker.events.append(dict(kind='cues', generation=first_generation,
                cues=[dict(id=999, start=0, end=999, text='STALE', original='STALE')],
                audio_s=1, asr_s=0, translation_s=0))
            engine.tick()
            assert engine.metrics['translated_pages'] == before_pages
            assert 'STALE' not in window.caption_overlay.translation.text()

            # Midroll: three commercials with changing maps/discontinuities.
            old_worker, old_timeline = engine.worker, engine.timeline
            source.append(3, advertisement=True)
            until(waiting)
            # Five clean two-second segments followed the preroll. The future
            # midroll must not discard any of those ten delayed seconds.
            assert engine.metrics['drained_boundary'] == 'source_TwitchAdBreak'
            assert engine.metrics['last_video_pts_s'] >= 9.9
            assert old_worker.stop.is_set() and not old_timeline.pending and old_timeline.active is None
            assert not window.caption_overlay.translation.text()
            assert engine.playlist is playlist and not playlist.stop.is_set()
            assert engine.transitions['recovery'] == 0 and not engine.recovery.attempts
            current_generation = engine.generation
            assert window.pause_button.isEnabled()
            window.pause_button.click()
            assert engine.waiting_paused and window.controller.state == 'paused'
            source.append(1, group='live-b')
            settle()
            assert not playlist.ad_status()['ready']
            source.append(1, group='live-b')
            until(lambda: playlist.ad_status()['ready'])
            settle(.4)
            assert engine.generation == current_generation and engine.waiting_paused
            assert window.pause_button.isEnabled()
            window.pause_button.click()
            until(lambda: engine.generation == current_generation+1 and not engine.finishing)
            source.append(3, group='live-b')
            captions_playing(current_generation+1)
            assert engine.picture.videoSink().videoFrame().height() == 240
            assert engine.playlist is playlist and source.resolutions == 1
            assert engine.bridge.source_origin == 20.0
            assert engine.transitions['advertisement'] == 2
            assert engine.transitions['recovery'] == 0 and not engine.recovery.attempts
            assert window.stop_button.isEnabled()
            window.stop_button.click()
            until(lambda: not engine.running)
            assert results[-1]['passed']

            # Cancellation during a preroll must never create another generation.
            source.reset()
            source.append(3, advertisement=True)
            assert engine.start('https://www.twitch.tv/hasanabi', 5)
            until(waiting)
            cancelled_generation = engine.generation
            assert window.stop_button.isEnabled()
            window.stop_button.click()
            until(lambda: not engine.running)
            source.append(2)
            settle()
            assert engine.generation == cancelled_generation
            assert not engine.transition_timer.isActive()

            # The source can fail while the decoder/worker generation is retired.
            source.reset()
            source.append(2, advertisement=True)
            assert engine.start('https://www.twitch.tv/hasanabi', 5)
            until(waiting)
            failed_generation = engine.generation
            with engine.playlist.condition:
                engine.playlist.error = TwitchAdTimeout()
                engine.playlist.condition.notify_all()
            until(lambda: not engine.running, expected_errors=('source_twitch_ad_timeout',))
            assert engine.generation == failed_generation
            assert results[-1]['errors'] == ['source_twitch_ad_timeout']
            assert results[-1]['reason'] == 'error'
            assert not engine.transition_timer.isActive()
            assert 'source_twitch_ad_timeout' not in window.panel.status.text()

            # Check the actual label setters, including the inherited error path.
            # Changing display language here must not change saved preferences.
            preferences_before = dict(window.panel.preferences)
            store_before = dict(window.panel.store.values)
            expected_messages = {
                'en': (
                    'Twitch ad break · waiting for the stream to return…',
                    'Twitch ad break · about 7 s remaining',
                    'Twitch ad break · paused. Press Resume to continue.',
                    'Twitch did not resume after the ad break. Press Restart to try again.'),
                'es': (
                    'Publicidad de Twitch · esperando que vuelva la emisión…',
                    'Publicidad de Twitch · quedan unos 7 s',
                    'Publicidad de Twitch · en pausa. Pulsa Reanudar para continuar.',
                    'Twitch no reanudó la emisión después de la publicidad. Pulsa Reiniciar para reintentar.'),
                'pt': (
                    'Publicidade da Twitch · aguardando o retorno da transmissão…',
                    'Publicidade da Twitch · faltam cerca de 7 s',
                    'Publicidade da Twitch · em pausa. Pressione Retomar para continuar.',
                    'A Twitch não retomou a transmissão após a publicidade. Pressione Reiniciar para tentar novamente.'),
                'fr': (
                    'Publicité Twitch · en attente du retour du direct…',
                    'Publicité Twitch · environ 7 s restantes',
                    'Publicité Twitch · en pause. Appuyez sur Reprendre pour continuer.',
                    'Twitch n’a pas repris le direct après la publicité. Appuyez sur Redémarrer pour réessayer.'),
            }
            for language, expected in expected_messages.items():
                window.ui_language.set_language(language)
                for index, status in enumerate((
                        dict(waiting=True, ready=False, remaining_s=None, elapsed_s=0),
                        dict(waiting=True, ready=False, remaining_s=6.2, elapsed_s=1),
                        dict(waiting=True, ready=True, remaining_s=0, elapsed_s=2, paused=True))):
                    window.show_advertisement(status)
                    assert window.wait_label.text() == expected[index], (language, window.wait_label.text())
                window.panel.on_error('source_twitch_ad_timeout')
                prefix = {'en': 'Error: ', 'es': 'Error: ', 'pt': 'Erro: ', 'fr': 'Erreur : '}[language]
                assert window.panel.status.text() == prefix+expected[3], (language, window.panel.status.text())
                if language != 'en':
                    assert window.wait_label.text() != expected_messages['en'][2]
                    assert window.panel.status.text() != expected_messages['en'][3]
            assert window.panel.preferences == preferences_before
            assert window.panel.store.values == store_before
        finally:
            if engine.running:
                engine.finish('check_cleanup')
                deadline = time.monotonic()+15
                while engine.running and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(.01)
            window.close()

    assert len(results) == 3 and len(metrics.values) == 3
    assert all(not result['errors'] for result in results[:2])
    assert all(not result['cleanup_errors']
               and result['playlist_stopped'] and result['remaining_compressed_bytes'] == 0
               for result in results)
    assert all(worker.stop.is_set() for worker in workers)
    assert all(not playlist.thread.is_alive() for playlist in playlists)
    assert len(playlists) == 3 and source.resolutions == 3
    assert len(outputs) == len(workers)
    assert not any(group.startswith('ad-') for group, _ in source.downloads)
    assert any(notice['waiting'] for notice in notices)
    assert any(notice['ready'] for notice in notices)
    report = dict(passed=True, synthetic_media=True, synthetic_asr_translation=True,
        remote_media=False, real_ad_observed=False, audible=False, media_saved=False,
        preroll_no_media_downloads=True, waits_for_two_clean_segments=True,
        midroll_maps_not_recovery=True, pause_preserved=True, stop_cancels_wait=True,
        valid_content_drained_before_midroll=True,
        source_timeout_closes_wait=True, localized_ad_messages=list(expected_messages),
        stale_callbacks_and_cues_rejected=True, advertisements_downloaded=0,
        playlist_sessions=len(playlists), worker_generations=len(workers),
        elapsed_s=round(time.monotonic()-test_started, 3), sessions=results)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
