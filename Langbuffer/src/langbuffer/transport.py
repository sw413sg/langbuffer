"""Bounded HLS transport in RAM for local ASR and delayed playback."""
import io
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from .media_clock import MediaClock
from .media_buffer import CompressedBuffer, MediaChunk
from .media_source import content_url as normalize_content_url
from .seekable_hls import PlaylistSource
from .stream_formats import resolve_stream

def fetch_bounded(url, headers, limit):
    if urlparse(url).scheme != 'https':
        raise ValueError('https_required')
    with urlopen(Request(url, headers=headers), timeout=10) as response:
        body = response.read(limit + 1)
        if len(body) > limit:
            raise ValueError('resource_byte_limit')
        return body, response.url

def preview_frame(payload):
    """Un fotograma del segmento seleccionado; decodificación acotada en RAM."""
    import av
    with av.open(io.BytesIO(payload)) as container:
        stream = next((stream for stream in container.streams if stream.type == 'video'), None)
        if stream is not None:
            for packet in container.demux(stream):
                for frame in packet.decode():
                    width = min(1280, frame.width)
                    frame = frame.reformat(width=width, height=max(1, round(frame.height*width/frame.width)), format='rgb24')
                    return frame.to_ndarray().tobytes(), frame.width, frame.height
    raise ValueError('preview_unavailable')

class StreamBridge:
    def __init__(self, delay, seconds, source, ahead=None, clock=None, content_url=None,
                 playlist_source=None, start_position=None, with_preview=False):
        self.delay, self.seconds, self.source = delay, seconds, source
        self.content_url = normalize_content_url(content_url) if source in {'x', 'twitch', 'kick', 'youtube', 'facebook'} else None
        self.ahead = ahead
        self.playlist_source = playlist_source
        self.owns_playlist = False
        self.start_position = start_position
        self.with_preview = with_preview
        self.preview = None
        self.source_origin = None
        self.clock = clock or MediaClock()
        # La repetición activa de Kick publica normalmente unos 30 s de medios
        # de una vez. Reservarlos comprimidos en RAM evita que Qt llegue a una
        # conexión temporalmente vacía; el Handler conserva la entrega a 1×.
        reserve = 48 if source == 'kick' else 16
        self.buffer = CompressedBuffer(64 * 1024 * 1024, delay + reserve)
        if source == 'kick' and ahead is not None:
            # El relay se construye antes de conocer el proveedor y aún no ha
            # arrancado, por lo que puede adoptar la misma reserva con seguridad.
            ahead.buffer = CompressedBuffer(64 * 1024 * 1024, reserve)
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.epoch = None
        self.errors = []
        self.boundary_error = None
        self.metrics = dict(downloaded_segments=0, downloaded_bytes=0, served_segments=0,
                            served_bytes=0, first_delivery_s=None, deliveries=[], downloads=[])
        self.path = '/' + secrets.token_urlsafe(24) + '/stream'
        self.content_type = 'video/mp2t'
        self.claimed = False
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path != bridge.path:
                    self.send_error(404)
                    return
                if bridge.claimed:
                    self.send_error(409)
                    return
                bridge.claimed = True
                self.connection.settimeout(2)
                self.send_response(200)
                self.send_header('Content-Type', bridge.content_type)
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Accept-Ranges', 'none')
                self.send_header('Connection', 'close')
                self.send_header('Transfer-Encoding', 'chunked')
                self.end_headers()
                try:
                    while not bridge.stop.is_set():
                        try:
                            chunk = bridge.buffer.pull(.25)
                        except TimeoutError:
                            continue
                        if chunk is None:
                            break
                        if bridge.clock.wait_until(bridge.epoch + bridge.delay + chunk.start, bridge.stop):
                            break
                        self.wfile.write(f'{len(chunk.data):X}\r\n'.encode('ascii'))
                        self.wfile.write(chunk.data)
                        self.wfile.write(b'\r\n')
                        self.wfile.flush()
                        if len(bridge.metrics['deliveries']) < 32:
                            bridge.metrics['deliveries'].append(dict(
                                media_start_s=chunk.start,
                                at_s=round(bridge.clock.now()-bridge.epoch, 3),
                                lateness_ms=round(max(0, bridge.clock.now()-bridge.epoch-bridge.delay-chunk.start)*1000, 3)))
                        bridge.metrics['served_segments'] += 1
                        bridge.metrics['served_bytes'] += len(chunk.data)
                        if bridge.metrics['first_delivery_s'] is None:
                            bridge.metrics['first_delivery_s'] = round(bridge.clock.now() - bridge.epoch, 3)
                        chunk = None
                    if not bridge.stop.is_set():
                        self.wfile.write(b'0\r\n\r\n')
                        self.wfile.flush()
                except (OSError, ValueError):
                    if not bridge.stop.is_set():
                        bridge.errors.append('local_stream_disconnected')
                        bridge.stop.set()
                finally:
                    self.close_connection = True

        self.server = HTTPServer(('127.0.0.1', 0), Handler)
        self.server.timeout = .25
        self.url = f'http://127.0.0.1:{self.server.server_port}{self.path}'
        self.http_thread = threading.Thread(target=self.serve, daemon=True)
        self.producer = threading.Thread(target=self.produce, daemon=True)

    def serve(self):
        while not self.stop.is_set():
            self.server.handle_request()

    def produce(self):
        try:
            if self.source in {'x', 'twitch', 'kick', 'youtube', 'facebook'}:
                if self.source == 'twitch' and self.start_position is not None:
                    raise ValueError('invalid_seek_position')
                if self.playlist_source is None:
                    self.owns_playlist = True
                    self.playlist_source = PlaylistSource(lambda: resolve_stream(self.content_url), fetch_bounded,
                                                          follow_endlist=self.source == 'kick')
                self.playlist_source.start()
                snapshot = self.playlist_source.window(self.stop)
                if snapshot is None:
                    return
                self.metrics['dynamic_playlist'] = snapshot.live
                self.metrics.update({'source_'+key: value for key, value in self.playlist_source.details.items()})
                entries = self.playlist_source.entries(self.start_position, self.seconds, self.stop,
                                                       lambda origin: setattr(self, 'source_origin', origin),
                                                       with_init=True, latest_epoch=self.source == 'twitch')
                first_fragment = True
                def load(entry):
                    nonlocal first_fragment
                    if hasattr(self.playlist_source, 'load_pair'):
                        payload = self.playlist_source.load_pair(entry)
                        first_fragment = False
                        return MediaChunk(entry[0], entry[1], payload)
                    start, duration, url, init_url = entry
                    initialization = b''
                    if first_fragment and init_url:
                        initialization, _ = fetch_bounded(init_url, self.playlist_source.headers, 1024 * 1024)
                        self.content_type = 'video/mp4'
                        if self.ahead is not None:
                            self.ahead.content_type = self.content_type
                        self.metrics['initialization_bytes'] = len(initialization)
                    # Kick combina hasta 1080p60/8 Mbps en segmentos cercanos a
                    # 10 s; 8 MiB no alcanzan. El límite ampliado sigue siendo
                    # por segmento y la cola total permanece en 64 MiB.
                    limit = (16 if self.source == 'kick' else 8) * 1024 * 1024
                    payload, _ = fetch_bounded(url, self.playlist_source.headers, limit)
                    first_fragment = False
                    return MediaChunk(start, duration, initialization + payload)
            else:
                raise ValueError('unsupported_source')
            for entry in entries:
                start = entry.start if isinstance(entry, MediaChunk) else entry[0]
                if (self.source != 'kick' and self.epoch is not None
                        and self.clock.wait_until(self.epoch + start, self.stop)):
                    break
                if self.stop.is_set():
                    break
                requested = time.monotonic()
                chunk = load(entry)
                if self.stop.is_set():
                    break
                if self.epoch is None:
                    if self.with_preview:
                        self.preview = preview_frame(chunk.data)
                    self.epoch = self.clock.now()
                if len(self.metrics['downloads']) < 32:
                    self.metrics['downloads'].append(dict(media_start_s=chunk.start,
                        duration_ms=round((time.monotonic()-requested)*1000, 3),
                        ready_s=round(self.clock.now()-self.epoch, 3)))
                if self.source == 'kick':
                    if not self.buffer.wait_and_push(chunk, self.stop):
                        break
                else:
                    self.buffer.push(chunk)
                if self.ahead is not None:
                    self.ahead.epoch = self.epoch
                    if self.source == 'kick':
                        if not self.ahead.buffer.wait_and_push(chunk, self.stop):
                            break
                    else:
                        self.ahead.buffer.push(chunk)
                    self.ahead.ready.set()
                self.metrics['downloaded_segments'] += 1
                self.metrics['downloaded_bytes'] += len(chunk.data)
                self.ready.set()
                chunk = None
        except Exception as error:
            # No registrar excepciones libres, manifiestos, URLs temporales ni medios.
            code = str(error)
            allowed = {'unsupported_hls_feature', 'encrypted_hls_not_supported',
                       'unsupported_hls_feature_map', 'unsupported_hls_feature_byterange',
                       'unsupported_hls_feature_map_change',
                       'unsupported_hls_feature_master', 'unsupported_hls_feature_gap',
                       'unsupported_hls_feature_discontinuity',
                       'invalid_segment_duration', 'continuous_mode_requires_complete_replay_playlist',
                       'invalid_seek_position', 'live_timeline_changed', 'preview_unavailable',
                       'live_sequence_reset', 'live_window_expired', 'live_playlist_stalled', 'invalid_live_playlist',
                       'twitch_offline', 'twitch_unavailable', 'kick_offline', 'kick_unavailable',
                       'kick_dvr_unavailable', 'no_hls_video', 'resolution_unavailable',
                       'quality_unavailable', 'resource_byte_limit',
                       'paired_clock_unverified', 'paired_codec_unsupported', 'paired_cleanup_failed'}
            if not self.stop.is_set():
                safe_code = 'source_' + (code if code in allowed else type(error).__name__)
                if (self.metrics['downloaded_segments'] and safe_code in {
                        'source_unsupported_hls_feature_discontinuity',
                        'source_unsupported_hls_feature_map_change', 'source_TwitchAdBreak'}):
                    # No bytes from the next epoch/ad were downloaded. End the
                    # current HTTP streams normally and let the viewer drain
                    # the valid buffered epoch before recovering.
                    self.boundary_error = safe_code
                else:
                    self.errors.append(safe_code)
                    self.stop.set()
        finally:
            self.buffer.finish()
            if self.ahead is not None:
                self.ahead.buffer.finish()

    def start(self):
        self.http_thread.start()
        if self.source != "relay":
            self.producer.start()

    def close(self):
        self.stop.set()
        self.buffer.clear()
        self.preview = None
        if self.owns_playlist and self.playlist_source is not None:
            self.playlist_source.close()
        if self.http_thread.ident is not None:
            self.http_thread.join(3)
        if self.producer.ident is not None:
            self.producer.join(12)
        self.server.server_close()
        self.metrics.update(peak_compressed_bytes=self.buffer.peak_bytes,
                            peak_segments=self.buffer.peak_chunks, retired_segments=self.buffer.retired,
                            remaining_bytes=self.buffer.bytes,
                            threads_stopped=not (self.producer.is_alive() or self.http_thread.is_alive()))
        if self.playlist_source is not None:
            self.metrics['playlist_refreshes'] = self.playlist_source.refreshes
