"""Delayed audio/video playback with a local recognition worker."""
import json
import math
import os
import sys
import threading
import time
import uuid

from live_translate.pipeline import AudioChunks, FrozenTimeline, LocalWorker, RATE, ROOT, WORK, model_path
from live_translate.package_coordination import acquire_playback

from PySide6.QtCore import QObject, Signal, QTimer, QUrl
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QAudioBufferOutput, QAudioFormat, QVideoFrame
from live_translate.media_clock import MediaClock
from live_translate.media_source import content_url, content_kind, content_platform
from live_translate.seekable_hls import PlaylistSource
from live_translate.twitch_recovery import FORMAT_CHANGES, TwitchRecovery
from live_translate import transport as transport
from live_translate.transport import StreamBridge
from live_translate.twitch_ads import TwitchPlaylistSource, TwitchAdTimeout
from live_translate.extended_sources import resolve as resolve_extended, safe_error
from live_translate.file_bridge import FileBridge, FileRelay


class LocalPlayback(QObject):
    changed = Signal(str)
    countdown = Signal(object)
    completed = Signal(dict)
    quality_changed = Signal(object, object, object)
    history_changed = Signal(object, float)
    recovering = Signal()
    advertisement = Signal(object)
    _cleaned = Signal(dict)
    _closed = Signal(dict)
    _resolved = Signal(int, object, object)

    def __init__(self, picture, overlay, parent=None, *, seconds=0, stop_after=0):
        super().__init__(parent)
        self.picture, self.overlay = picture, overlay
        self.seconds, self.stop_after = seconds, stop_after
        self.running = self.finishing = self.playing = False
        self.direct_file = False
        self.generation = 0
        self.volume, self.muted, self.caption_offset = .5, False, 0.0
        self.last_result = None
        self.package_playback = None
        self.timer = QTimer(self)
        self.timer.setInterval(25)
        self.timer.timeout.connect(self.tick)
        self._cleaned.connect(self.cleaned)
        self._closed.connect(self.closed)
        self._resolved.connect(self.source_resolved)
        self.transition_timer = QTimer(self)
        self.transition_timer.setSingleShot(True)
        self.transition_timer.timeout.connect(self.resume_transition)

    def start(self, url, delay, *, device=None, caption_offset=0, quality=(0, 0), asr_model='base.en',
              source_language='en', target_language='es'):
        if self.running:
            return False
        url = content_url(url)
        kind = content_kind(url)
        if kind not in {'broadcast', 'twitch_live', 'kick_live', 'youtube_live', 'facebook'}:
            raise ValueError('unsupported_source')
        if not 1 <= delay <= 30:
            raise ValueError('invalid_delay')
        quality = self.valid_quality(quality)
        if quality is None:
            raise ValueError('invalid_quality')
        from live_translate.language_packages import LANGUAGES, effective_asr_model, required_packages, package_ready
        if source_language not in LANGUAGES or target_language not in LANGUAGES:
            raise ValueError('unsupported_language')
        asr_model = effective_asr_model(asr_model, source_language)
        model_path(asr_model)
        self.package_playback = acquire_playback(WORK)
        try:
            if not all(package_ready(spec) for spec in required_packages(
                    asr_model, source_language, target_language)):
                raise ValueError('language_packages_missing')
            return self._start_locked(url, kind, delay, device, caption_offset, quality,
                                      asr_model, source_language, target_language)
        except BaseException:
            self.package_playback.unlock()
            self.package_playback = None
            raise

    def _start_locked(self, url, kind, delay, device, caption_offset, quality,
                      asr_model, source_language, target_language):
        self.asr_model = asr_model
        self.source_language, self.target_language = source_language, target_language
        self.url, self.kind, self.platform = url, kind, content_platform(url)
        self.delay_s, self.device, self.quality = delay, device, quality
        self.playlist = None
        self.pending = None
        self.retired = self.closing = self.waiting_paused = False
        self.recovery = TwitchRecovery()
        self.transitions = dict(quality=0, seek=0, language=0, recovery=0, advertisement=0)
        self.ad_breaks_detected = 0
        self.ad_wait_s = 0.0
        self.totals = dict(video_frames=0, visible_audio_buffers=0, ahead_audio_buffers=0,
                           published_pages=0, caption_visible_frames=0)
        self.generations = []  # Last eight metric summaries only, never media or text.
        self.cleanup_errors = []
        self.session_started = time.monotonic()
        self.last_result = None
        self.begin_generation(caption_offset=caption_offset)
        return True

    @staticmethod
    def valid_quality(value):
        if (isinstance(value, (tuple, list)) and len(value) == 2
                and type(value[0]) is int and type(value[1]) is int
                and 0 <= value[0] <= 16384 and 0 <= value[1] <= 1000
                and (value[0] or not value[1])):
            return tuple(value)
        return None

    def begin_generation(self, *, caption_offset, position=None, reference=None):
        self.timeline = FrozenTimeline(self.generation+1)
        self.timeline.set_offset(caption_offset)
        self.caption_offset = self.timeline.offset
        self.generation += 1
        self.running, self.finishing, self.playing = True, False, False
        self.retired = False
        url, kind, delay = self.url, self.kind, self.delay_s
        self.clock = MediaClock()
        self.started, self.started_clock = time.monotonic(), self.clock.now()
        self.last_video_clock = self.started_clock
        self.playing_since = None
        self.metrics = dict(generation=self.generation, source=content_platform(url), source_kind=kind,
            requested_media_s=self.seconds, continuous=self.seconds == 0, delay_s=delay, errors=[],
            media_saved=False, transcripts_saved=False, live_captions_used=False,
            advanced_audio_output_created=False, audio_routes_changed=False,
            video_frames=0, visible_audio_buffers=0, ahead_audio_buffers=0, translated_pages=0,
            blocks=0, asr_max_s=0.0, translation_max_s=0.0, maximum_ahead_delivery_lead_s=0.0,
            caption_visible_frames=0, pause_count=0, models_local=True,
            asr_model=self.asr_model, asr_device='cpu', asr_compute_type='int8', asr_threads=4,
            source_language=self.source_language, target_language=self.target_language,
            processed_audio_s=0.0, processing_s=0.0, processing_max_rtf=0.0,
            worker_warnings=0, worker_diagnostics=[], dropped_audio_blocks=0, dropped_audio_s=0.0,
            caption_offset_s=self.caption_offset, integrated_ui=True,
            selected_quality=self.quality, start_position=position)
        self.changed.emit('starting' if not any(self.transitions.values()) else 'preparing')
        self.overlay.set_captions('')
        self.picture.hide()
        self.resources = []
        self.worker = self.ahead = self.bridge = self.player = self.ahead_player = None
        self.connections = []
        self.quality_reported = False
        self.history_at = 0
        self.bridges_started = self.connected = self.ahead_connected = False
        self.first_ahead_pts = self.first_ahead_clock = self.last_ahead_end = None
        self.ahead_flushed = False
        self.direct_file = False
        self.resolved_source = None
        self.resolver_stop = threading.Event()
        self.resolver_thread = None
        if self.platform in {'youtube', 'facebook'}:
            generation, quality, cancel = self.generation, self.quality, self.resolver_stop
            def resolve_source():
                try:
                    selected = resolve_extended(url, *quality, cancel=cancel)
                    self._resolved.emit(generation, selected, (position, reference))
                except Exception as error:
                    self._resolved.emit(generation, {'error': safe_error(error)}, (position, reference))
            self.resolver_thread = threading.Thread(target=resolve_source, daemon=True)
            self.resolver_thread.start()
            return
        self.create_players(position, reference)

    def source_resolved(self, generation, selected, origin):
        if not self.running or self.finishing or generation != self.generation:
            return
        if 'error' in selected:
            self.fail('source_'+selected['error'])
            return
        self.resolved_source = selected
        self.metrics['source_preparation_s'] = round(self.clock.now()-self.started_clock, 3)
        self.started_clock = self.clock.now()
        self.direct_file = selected['transport'] == 'mp4_progressive'
        self.metrics['transport'] = selected['transport']
        self.metrics['media_kind'] = selected['media_kind']
        self.create_players(*origin)

    def create_players(self, position=None, reference=None):
        url, delay = self.url, self.delay_s
        try:
            self.worker = LocalWorker(self.generation, model_name=self.asr_model,
                                      source_language=self.source_language, target_language=self.target_language)
            self.chunks = AudioChunks(self.worker.submit)
            if self.direct_file:
                self.bridge = FileBridge(self.resolved_source, self.clock, start_position=position)
                self.ahead = FileRelay(self.bridge)
                self.direct_positioned = self.direct_ahead_positioned = False
            elif self.playlist is None:
                height, fps = self.quality
                source_type = TwitchPlaylistSource if self.platform == 'twitch' else PlaylistSource
                selected = self.resolved_source
                if selected and selected['transport'] == 'hls_paired':
                    from .paired_hls import PairedPlaylistSource
                    source_type = PairedPlaylistSource
                self.playlist = source_type(lambda: selected or transport.resolve_stream(url, height, fps),
                    transport.fetch_bounded, reference_window=reference,
                    follow_endlist=self.platform == 'kick')
            if not self.direct_file:
                self.ahead = StreamBridge(0, self.seconds, 'relay', clock=self.clock)
                self.bridge = StreamBridge(max(0, delay-3), self.seconds, content_platform(url),
                    ahead=self.ahead, clock=self.clock, content_url=url, playlist_source=self.playlist,
                    start_position=position, with_preview=any(self.transitions.values()))
            self.ahead_player = QMediaPlayer(self)
            self.resources.append(self.ahead_player)
            self.ahead_player.setPlaybackRate(1.0)
            # Deliberately no QAudioOutput on the recognition path.
            fmt = QAudioFormat()
            fmt.setSampleRate(RATE)
            fmt.setChannelCount(1)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            self.ahead_monitor = QAudioBufferOutput(fmt, self)
            self.resources.append(self.ahead_monitor)
            self.ahead_player.setAudioBufferOutput(self.ahead_monitor)
            self.bind(self.ahead_monitor.audioBufferReceived, self.ahead_buffer)
            self.bind(self.ahead_player.errorOccurred, lambda code, text: self.fail('ahead_'+code.name))
            self.bind(self.ahead_player.mediaStatusChanged, self.ahead_status)
            self.player = QMediaPlayer(self)
            self.resources.append(self.player)
            self.audio = QAudioOutput(self.device, self) if self.device is not None else QAudioOutput(self)
            self.resources.append(self.audio)
            self.audio.setVolume(self.volume)
            self.audio.setMuted(self.muted)
            self.player.setAudioOutput(self.audio)
            self.player.setPlaybackRate(1.0)
            self.player.setVideoSink(self.picture.videoSink())
            self.visible_monitor = QAudioBufferOutput(self)
            self.resources.append(self.visible_monitor)
            self.player.setAudioBufferOutput(self.visible_monitor)
            self.bind(self.visible_monitor.audioBufferReceived, self.visible_buffer)
            self.bind(self.player.errorOccurred, lambda code, text: self.fail('visible_'+code.name))
            self.bind(self.player.mediaStatusChanged, self.visible_status)
            self.bind(self.picture.videoSink().videoFrameChanged, self.video_frame)
            self.timer.start()
        except Exception as exc:
            self.fail('local_start_'+type(exc).__name__)

    def bind(self, signal, callback):
        generation = self.generation
        def guarded(*args):
            if self.running and not self.finishing and self.generation == generation:
                callback(*args)
        signal.connect(guarded)
        self.connections.append((signal, guarded))

    def request_quality(self, quality):
        quality = self.valid_quality(quality)
        if not self.running or self.finishing or not self.playing or quality is None:
            return False
        if quality == self.quality:
            return False
        position = None
        if self.direct_file:
            position = self.media_position()
        if self.platform in {'x', 'kick'} and self.playlist is not None and self.playlist.snapshot is not None:
            try:
                position = self.playlist.snapshot.select(self.media_position()).start
            except ValueError:
                self.fail('source_live_window_expired')
                return False
        return self.transition('quality', position=position, quality=quality)

    def media_position(self):
        if self.direct_file:
            if not self.playing:
                return self.bridge.source_origin
            return self.player.position()/1000
        return (self.bridge.source_origin or 0) + self.player.position()/1000

    def history_position(self, snapshot):
        if not self.playing and self.bridge.source_origin is None:
            try:
                return snapshot.select(self.metrics['start_position']).start
            except ValueError:
                return snapshot.start
        return self.media_position()

    def source_details(self):
        return (self.bridge.details if self.direct_file and self.bridge is not None
                else self.playlist.details if self.playlist is not None else {})

    def request_seek(self, position):
        if not self.running or (self.finishing and self.pending is None):
            return False
        if self.pending is not None and self.pending['kind'] in {'recovery', 'advertisement'}:
            return False
        if self.direct_file and self.bridge is not None:
            if (type(position) not in (float, int) or not math.isfinite(position)
                    or not 0 <= position < self.bridge.duration):
                return False
            return self.retarget_or_transition('seek', position)
        if (self.platform not in {'x', 'kick'} or self.playlist is None
                or self.playlist.snapshot is None):
            return False
        try:
            if position != 'live':
                if not isinstance(position, (float, int)) or not math.isfinite(position):
                    return False
                position = self.playlist.snapshot.select(position).start
        except ValueError:
            self.fail('source_live_window_expired')
            return False
        return self.retarget_or_transition('seek', position)

    def retarget_or_transition(self, kind, position):
        if self.pending is not None:
            self.pending['position'] = position
            if self.pending['kind'] != 'quality':
                self.pending['kind'] = kind
            return True
        return self.transition(kind, position=position)

    def request_languages(self, source_language, target_language, asr_model):
        if not self.running or (self.finishing and self.pending is None):
            return False
        from live_translate.language_packages import LANGUAGES, effective_asr_model, required_packages, package_ready
        if source_language not in LANGUAGES or target_language not in LANGUAGES:
            return False
        asr_model = effective_asr_model(asr_model, source_language)
        if any(not package_ready(spec) for spec in required_packages(asr_model, source_language, target_language)):
            return False
        selected = (source_language, target_language, asr_model)
        if self.pending is not None:
            self.pending['languages'] = selected
            return True
        if selected == (self.source_language, self.target_language, self.asr_model):
            return False
        position = (self.metrics['start_position'] if self.bridge is None or
                    not self.playing and self.bridge.source_origin is None
                    else self.media_position())
        if self.direct_file:
            position = min(position or 0, self.bridge.duration - .001)
        elif self.platform in {'x', 'kick'} and self.playlist is not None and self.playlist.snapshot is not None:
            if position is not None and position != 'live':
                try:
                    position = self.playlist.snapshot.select(position).start
                except ValueError:
                    self.fail('source_live_window_expired')
                    return False
        elif not self.direct_file:
            # Twitch and other non-DVR HLS sources do not accept a seek position.
            position = None
        self.pending = dict(kind='language', position=position, quality=None,
                            languages=selected, at=time.monotonic(), reference=None)
        self.waiting_paused = False
        self.retire_generation('transition')
        return True

    def transition(self, kind, *, position=None, quality=None, backoff=0):
        self.pending = dict(kind=kind, position=position, quality=quality,
                            at=time.monotonic()+backoff,
                            reference=self.playlist.snapshot if self.playlist is not None and
                            kind == 'quality' and self.platform in {'x', 'kick'} else None)
        self.waiting_paused = kind in {'recovery', 'advertisement'} and self.clock.is_paused()
        self.retire_generation('transition')
        return True

    def resume_transition(self):
        if self.pending is None or not self.retired:
            return
        if self.pending['kind'] == 'advertisement':
            try:
                status = self.playlist.ad_status()
            except Exception as error:
                # No remote messages, signed URLs or headers enter the metrics/UI.
                code = ('twitch_ad_timeout' if isinstance(error, TwitchAdTimeout)
                        else str(error) if str(error) in {
                            'live_playlist_stalled', 'live_window_expired', 'live_sequence_reset',
                            'live_timeline_changed', 'invalid_live_playlist',
                            'encrypted_hls_not_supported'} else type(error).__name__)
                self.metrics['errors'].append('source_'+code)
                self.finish('error')
                return
            self.advertisement.emit(dict(status, paused=self.waiting_paused))
            if not status['ready'] or self.waiting_paused:
                self.transition_timer.start(250)
                return
            self.ad_wait_s += status['elapsed_s']
        if self.waiting_paused:
            return
        remaining = self.pending['at']-time.monotonic()
        if remaining > 0:
            self.transition_timer.start(max(1, math.ceil(remaining*1000)))
            return
        pending, self.pending = self.pending, None
        self.transitions[pending['kind']] += 1
        if pending['quality'] is not None:
            self.quality = pending['quality']
        if pending.get('languages') is not None:
            self.source_language, self.target_language, self.asr_model = pending['languages']
        self.begin_generation(caption_offset=self.caption_offset,
                              position=pending['position'], reference=pending['reference'])

    def set_volume(self, value):
        self.volume = max(0, min(1, float(value)))
        if self.running and not self.finishing and self.player is not None:
            self.audio.setVolume(self.volume)

    def set_muted(self, value):
        self.muted = bool(value)
        if self.running and not self.finishing and self.player is not None:
            self.audio.setMuted(self.muted)

    def set_caption_offset(self, value):
        if self.running and not self.closing:
            self.timeline.set_offset(value)
            self.caption_offset = self.timeline.offset
            self.metrics['caption_offset_s'] = self.caption_offset

    def fail(self, code):
        if self.running and not self.finishing:
            boundary = getattr(self.bridge, 'boundary_error', None)
            if boundary and code.startswith('ahead_'):
                # Recognition has reached the clean EOF before the viewer.
                # Keep the delayed decoder and already translated cues alive.
                self.ahead_status(QMediaPlayer.MediaStatus.EndOfMedia)
                return
            # Qt can report the transport's EOF before tick sees its HLS error.
            errors = ((self.bridge.errors if self.bridge is not None else [])
                      + (self.ahead.errors if self.ahead is not None else []))
            if boundary:
                errors.append(boundary)
            transport_failure = code.startswith(('source_', 'ahead_', 'visible_'))
            # The inherited bridge sanitizes unknown errors to their class name.
            # A dedicated type keeps ad boundaries distinct without changing it.
            if (transport_failure and self.platform == 'twitch' and errors
                    and all(value == 'source_TwitchAdBreak' for value in errors)):
                self.ad_breaks_detected += 1
                self.transition('advertisement')
                return
            if code == 'source_TwitchAdTimeout':
                code = 'source_twitch_ad_timeout'
            if (transport_failure and self.platform == 'twitch' and errors
                    and all(value in FORMAT_CHANGES for value in errors)):
                backoff = self.recovery.reserve(time.monotonic())
                if backoff is not None:
                    self.metrics['recovered_errors'] = list(errors)
                    self.transition('recovery', backoff=backoff)
                    return
                code = 'source_twitch_recovery_limit'
            self.metrics['errors'].append(code)
            self.finish('error')

    def ahead_buffer(self, buffer):
        if not self.running or self.finishing or not buffer.isValid():
            return
        try:
            import numpy as np
            fmt = buffer.format()
            if (fmt.sampleRate(), fmt.channelCount(), fmt.sampleFormat()) != (
                    RATE, 1, QAudioFormat.SampleFormat.Int16) or buffer.startTime() < 0:
                raise ValueError('invalid_ahead_pcm')
            samples = np.frombuffer(bytes(buffer.data())[:buffer.byteCount()], dtype='<i2').astype(np.float32)/32768
            pts = buffer.startTime()/1_000_000
            if self.first_ahead_pts is None:
                self.first_ahead_pts, self.first_ahead_clock = pts, self.clock.now()
                self.metrics['ahead_first_pts_s'] = pts
            if self.last_ahead_end is not None and abs(pts-self.last_ahead_end) > .1:
                raise ValueError('audio_discontinuity')
            self.last_ahead_end = pts+len(samples)/RATE
            lead = self.last_ahead_end-self.first_ahead_pts-(self.clock.now()-self.first_ahead_clock)
            self.metrics['maximum_ahead_delivery_lead_s'] = max(self.metrics['maximum_ahead_delivery_lead_s'], lead)
            if lead > .5:
                raise ValueError('audio_faster_than_realtime')
            self.metrics['ahead_audio_buffers'] += 1
            self.chunks.feed(samples, pts)
        except Exception as exc:
            self.fail('pcm_'+type(exc).__name__)

    def ahead_status(self, status):
        if (status == QMediaPlayer.MediaStatus.EndOfMedia and self.running and not self.finishing
                and not self.ahead_flushed):
            try:
                self.chunks.flush()
                self.ahead_flushed = True
            except Exception as exc:
                self.fail('pcm_'+type(exc).__name__)

    def visible_buffer(self, buffer):
        if self.playing and not self.finishing and buffer.isValid():
            self.metrics['visible_audio_buffers'] += 1
            if 'visible_first_audio_pts_s' not in self.metrics:
                self.metrics['visible_first_audio_pts_s'] = buffer.startTime()/1_000_000
                self.metrics['observed_first_audio_lead_s'] = round(
                    self.clock.now()-self.first_ahead_clock, 3) if self.first_ahead_clock else None

    def video_frame(self, frame):
        if not self.running or self.finishing or not self.playing or not frame.isValid():
            return
        self.metrics['video_frames'] += 1
        self.last_video_clock = self.clock.now()
        self.metrics['last_video_pts_s'] = frame.startTime()/1_000_000
        if 'first_video_at_s' not in self.metrics:
            self.metrics['first_video_at_s'] = round(self.clock.now()-self.bridge.epoch, 3)
            self.metrics['first_video_pts_s'] = frame.startTime()/1_000_000
            self.metrics['source_origin_s'] = self.bridge.source_origin
        if frame.startTime() < 0:
            self.fail('video_pts_missing')
            return
        self.overlay.set_aspect(frame.width(), frame.height())
        cue = self.timeline.tick(frame.startTime()/1_000_000)
        self.overlay.set_captions(cue.text if cue else '', cue.original if cue else '')
        if cue:
            self.metrics['caption_visible_frames'] += 1

    def visible_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self.running and not self.finishing:
            if self.bridge.errors:
                self.fail(self.bridge.errors[0])
            elif getattr(self.bridge, 'boundary_error', None):
                self.metrics['drained_boundary'] = self.bridge.boundary_error
                self.fail(self.bridge.boundary_error)
            else:
                self.finish('completed')

    def tick(self):
        if self.finishing or not self.running:
            return
        try:
            events = self.worker.poll()
            worker_alive = self.worker.process.is_alive()
            if not worker_alive:
                # A process can exit between the first queue poll and the
                # liveness check. Drain its final diagnostic before classifying
                # an otherwise unexplained native/process exit.
                events.extend(self.worker.poll())
                self.metrics['worker_exitcode'] = getattr(self.worker.process, 'exitcode', None)
            for event in events:
                if event['generation'] != self.generation:
                    continue
                if event.get('bundled_rejected'):
                    from live_translate.language_packages import reject_bundled_package
                    reject_bundled_package()
                    self.metrics['bundled_translation_rejected'] = True
                if event['kind'] == 'ready':
                    self.metrics['models_load_s'] = event['load_s']
                    self.metrics['loaded_asr_model'] = event.get('model')
                    self.bridges_started = True
                    self.ahead.start()
                    self.bridge.start()
                    self.changed.emit('preparing')
                elif event['kind'] in {'warning', 'error'}:
                    if event.get('diagnostic'):
                        self.metrics['worker_diagnostics'].append(event['diagnostic'])
                        self.metrics['worker_diagnostics'][:] = self.metrics['worker_diagnostics'][-8:]
                    if event['kind'] == 'warning':
                        self.metrics['worker_warnings'] += 1
                        continue
                    self.fail(event['code'])
                    return
                elif event['kind'] == 'cues':
                    self.metrics['blocks'] += 1
                    self.metrics['translated_pages'] += len(event['cues'])
                    self.metrics['asr_max_s'] = max(self.metrics['asr_max_s'], event['asr_s'])
                    self.metrics['translation_max_s'] = max(self.metrics['translation_max_s'], event['translation_s'])
                    processing = event['asr_s']+event['translation_s']
                    self.metrics['processed_audio_s'] += event['audio_s']
                    self.metrics['processing_s'] += processing
                    self.metrics['processing_max_rtf'] = max(self.metrics['processing_max_rtf'],
                        processing/max(.001, event['audio_s']))
                    self.timeline.receive(self.generation, event['cues'])
            if not worker_alive:
                self.fail('worker_exited')
                return
            if self.bridge.errors or self.ahead.errors:
                self.fail((self.bridge.errors+self.ahead.errors)[0])
                return
            details = self.source_details()
            if details.get('height') and not self.quality_reported:
                self.quality_reported = True
                actual = (int(details['height']), int(round(details.get('fps') or 0)))
                self.metrics['actual_quality'] = actual
                self.quality_changed.emit(details.get('qualities') or details.get('resolutions') or [],
                                          self.quality, actual)
            if self.platform in {'x', 'kick'} and self.playlist.snapshot is not None:
                if time.monotonic() >= self.history_at:
                    self.history_at = time.monotonic()+.2
                    self.history_changed.emit(self.playlist.snapshot,
                                              self.history_position(self.playlist.snapshot))
            elif self.direct_file and self.player.isSeekable() and time.monotonic() >= self.history_at:
                self.history_at = time.monotonic()+.2
                self.history_changed.emit(self.bridge.snapshot, self.media_position())
            if self.clock.is_paused():
                return
            if self.bridge.preview is not None:
                pixels, width, height = self.bridge.preview
                self.bridge.preview = None
                self.picture.show_preview(QImage(pixels, width, height, width*3,
                                                  QImage.Format.Format_RGB888).copy())
                self.picture.show()
            if self.bridges_started:
                if self.ahead.ready.is_set() and not self.ahead_connected:
                    self.ahead_connected = True
                    self.ahead_player.setSource(QUrl(self.ahead.url))
                    if not self.direct_file:
                        self.ahead_player.play()
                if self.bridge.ready.is_set() and not self.connected:
                    self.connected = True
                    self.player.setSource(QUrl(self.bridge.url))
                if self.direct_file and self.connected and self.ahead_connected and self.bridge.epoch is None:
                    target = round(self.bridge.source_origin*1000)
                    if not self.direct_positioned and self.player.isSeekable():
                        self.player.setPosition(target)
                        self.direct_positioned = abs(self.player.position()-target) <= 50
                    if not self.direct_ahead_positioned and self.ahead_player.isSeekable():
                        self.ahead_player.setPosition(target)
                        self.direct_ahead_positioned = abs(self.ahead_player.position()-target) <= 50
                    if self.direct_positioned and self.direct_ahead_positioned:
                        self.bridge.epoch = self.clock.now()
                        self.ahead_player.play()
                if self.connected and not self.playing and self.bridge.epoch is not None:
                    remaining = self.bridge.epoch+self.delay_s-self.clock.now()
                    self.countdown.emit(max(0, remaining))
                    if remaining <= 0:
                        self.playing = True
                        self.playing_since = self.last_video_clock = self.clock.now()
                        self.picture.show()
                        self.picture.clear_preview()
                        self.overlay.reveal()
                        self.player.play()
                        self.changed.emit('playing')
            elapsed = self.clock.now()-self.started_clock
            if (not self.playing and elapsed > self.delay_s+45
                    or self.seconds and elapsed > self.delay_s+self.seconds+45):
                self.fail('watchdog_timeout')
            elif self.playing and self.clock.now()-self.last_video_clock > 45:
                self.fail('video_stalled')
            elif self.playing and self.stop_after and self.clock.now()-self.playing_since >= self.stop_after:
                self.finish('stopped_by_check')
        except Exception as exc:
            self.fail('pipeline_'+type(exc).__name__)

    def toggle_pause(self):
        if self.running and self.pending is not None and self.pending['kind'] == 'advertisement':
            self.waiting_paused = not self.waiting_paused
            self.changed.emit('paused' if self.waiting_paused else 'preparing')
            self.resume_transition()
            return
        if self.running and self.pending is not None and self.waiting_paused:
            self.waiting_paused = False
            self.changed.emit('preparing')
            self.recovering.emit()
            self.resume_transition()
            return
        if not self.running or self.finishing or not self.playing:
            return
        if self.clock.is_paused():
            self.clock.resume()
            if not self.ahead_flushed:
                self.ahead_player.play()
            self.player.play()
            self.changed.emit('playing')
        else:
            self.clock.pause()
            self.ahead_player.pause()
            self.player.pause()
            self.metrics['pause_count'] += 1
            self.changed.emit('paused')

    def finish(self, reason='stopped'):
        if not self.running or self.closing:
            return
        self.pending = None
        self.transition_timer.stop()
        if self.finishing:
            self.metrics['reason'] = reason
            self.changed.emit('stopping')
            if self.retired:
                self.close_playlist()
            return
        self.retire_generation(reason)

    def retire_generation(self, reason):
        self.finishing = True
        self.resolver_stop.set()
        self.timer.stop()
        self.changed.emit('paused' if self.waiting_paused and self.pending else
                          'preparing' if self.pending else 'stopping')
        if self.pending and self.pending['kind'] == 'recovery' and not self.waiting_paused:
            self.recovering.emit()
        self.metrics.update(reason=reason, published_pages=self.timeline.published,
            late_pages=self.timeline.late, obsolete_pages=self.timeline.obsolete,
            peak_pending_pages=self.timeline.peak_pending, volume=self.volume, muted=self.muted,
            ahead_audio_output_is_none=self.ahead_player is None or self.ahead_player.audioOutput() is None,
            elapsed_s=round(time.monotonic()-self.started, 3))
        for resource in (self.bridge, self.ahead, self.worker):
            if resource is not None:
                resource.stop.set()
        for signal, slot in self.connections:
            signal.disconnect(slot)
        self.connections.clear()
        for media in (self.player, self.ahead_player):
            if media is not None:
                media.stop()
                media.setSource(QUrl())
                media.setVideoSink(None)
        self.picture.clear_preview()
        self.picture.videoSink().setVideoFrame(QVideoFrame())
        self.timeline.clear()
        if hasattr(self, 'chunks'):
            self.chunks.clear()
        self.metrics['dropped_audio_blocks'] = getattr(self.worker, 'dropped_blocks', 0)
        self.metrics['dropped_audio_s'] = round(getattr(self.worker, 'dropped_audio_s', 0.0), 3)
        self.overlay.set_captions('')
        self.picture.show()
        old_playlist = self.playlist if self.pending and self.pending['kind'] == 'quality' else None
        def cleanup():
            result = dict(cleanup_errors=[], worker_stopped=self.worker is None, worker_forced_stop=False)
            if self.resolver_thread is not None:
                self.resolver_thread.join(5)
                if self.resolver_thread.is_alive():
                    result['cleanup_errors'].append('source_resolver_cleanup_failed')
            for resource in (self.bridge, self.ahead, self.worker):
                if resource is not None:
                    try:
                        closed = resource.close()
                        if resource is self.worker:
                            result.update(closed)
                    except Exception as exc:
                        result['cleanup_errors'].append(type(exc).__name__)
            bridges = [x for x in (self.bridge, self.ahead) if x is not None]
            result['transports_stopped'] = all(x.metrics.get('threads_stopped') for x in bridges)
            result['remaining_compressed_bytes'] = sum(x.metrics.get('remaining_bytes', 0) for x in bridges)
            result['peak_compressed_bytes'] = sum(x.metrics.get('peak_compressed_bytes', 0) for x in bridges)
            if old_playlist is not None:
                try:
                    old_playlist.close()
                    if old_playlist.thread.is_alive():
                        result['cleanup_errors'].append('playlist_cleanup_failed')
                except Exception as exc:
                    result['cleanup_errors'].append(type(exc).__name__)
            result['playlist_retired'] = old_playlist is not None
            self._cleaned.emit(result)
        threading.Thread(target=cleanup, daemon=True).start()

    def cleaned(self, result):
        self.metrics.update(result)
        for resource in self.resources:
            resource.deleteLater()
        self.resources.clear()
        self.retired = True
        if result['playlist_retired'] and not result['cleanup_errors']:
            self.playlist = None
        self.cleanup_errors.extend(result['cleanup_errors'])
        if (not result.get('worker_stopped') or result.get('worker_forced_stop')
                or not result['transports_stopped'] or result['remaining_compressed_bytes']):
            self.cleanup_errors.append('generation_cleanup_failed')
        for key in self.totals:
            self.totals[key] += self.metrics[key]
        self.generations.append({key: self.metrics[key] for key in (
            'generation', 'reason', 'video_frames', 'ahead_audio_buffers', 'published_pages',
            'errors', 'worker_stopped', 'worker_forced_stop', 'transports_stopped',
            'remaining_compressed_bytes', 'start_position', 'source_origin_s', 'selected_quality',
            'actual_quality', 'recovered_errors', 'asr_model', 'loaded_asr_model',
            'source_language', 'target_language', 'worker_diagnostics', 'worker_exitcode',
            'worker_warnings', 'dropped_audio_blocks', 'dropped_audio_s',
            'drained_boundary', 'last_video_pts_s') if key in self.metrics})
        self.generations[:] = self.generations[-8:]
        if self.cleanup_errors:
            self.pending = None
        if self.pending is not None:
            self.resume_transition()
        else:
            self.close_playlist()

    def close_playlist(self):
        if self.closing:
            return
        self.closing = True
        playlist = self.playlist
        def close():
            result = dict(playlist_stopped=playlist is None, cleanup_errors=[])
            try:
                if playlist is not None:
                    playlist.close()
                    result['playlist_stopped'] = not playlist.thread.is_alive()
                if not result['playlist_stopped']:
                    result['cleanup_errors'].append('playlist_cleanup_failed')
            except Exception as exc:
                result['cleanup_errors'].append(type(exc).__name__)
            self._closed.emit(result)
        threading.Thread(target=close, daemon=True).start()

    def closed(self, result):
        self.metrics['playlist_stopped'] = result['playlist_stopped']
        self.cleanup_errors.extend(result['cleanup_errors'])
        self.metrics['cleanup_errors'] = self.cleanup_errors
        self.metrics.update(transitions=self.transitions, generations=self.generations,
                            session_totals=self.totals,
                            ad_breaks_detected=self.ad_breaks_detected,
                            completed_ad_wait_s=round(self.ad_wait_s, 3),
                            session_elapsed_s=round(time.monotonic()-self.session_started, 3))
        self.metrics['passed'] = bool(not self.metrics['errors'] and not self.cleanup_errors
            and result['playlist_stopped'] and self.totals['video_frames'] > 0
            and self.totals['visible_audio_buffers'] > 0 and self.totals['caption_visible_frames'] > 0)
        try:
            name = f'local-asr-integrated-{os.getpid()}-{uuid.uuid4().hex}.json'
            (ROOT / 'outputs' / name).write_text(json.dumps(self.metrics, indent=2), encoding='utf-8')
        except (OSError, ValueError, TypeError) as error:
            self.metrics['metrics_write_error'] = type(error).__name__
        finally:
            self.running = self.finishing = self.playing = self.closing = False
            package_playback = getattr(self, 'package_playback', None)
            if package_playback is not None:
                package_playback.unlock()
                self.package_playback = None
        self.last_result = dict(self.metrics)
        self.completed.emit(self.last_result)
