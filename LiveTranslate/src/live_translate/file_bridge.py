"""Ephemeral bounded MP4 range proxy shared by the two Qt decoders."""
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .range_media import RangeSource, CHUNK
from .seekable_hls import Segment, Window
from .extended_sources import safe_error


def finite_window(duration):
    half = duration/2
    return Window((Segment(0, 0, half, ''), Segment(1, half, duration-half, '')), 1, False)


def requested_range(header, size):
    if header is None:
        return 0, size-1, False
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', header)
    if not match or not any(match.groups()):
        raise ValueError('invalid_media_range')
    first, last = match.groups()
    start = int(first) if first else max(0, size-int(last))
    end = min(size-1, int(last)) if first and last else size-1
    if start >= size or end < start:
        raise ValueError('invalid_media_range')
    return start, end, True


class FileBridge:
    def __init__(self, selected, clock, *, start_position=None):
        self.selected, self.clock = selected, clock
        self.details = {key: selected.get(key) for key in ('height', 'fps', 'width', 'qualities', 'resolutions')}
        self.duration = selected['duration']
        self.snapshot = finite_window(self.duration)
        self.source_origin = float(start_position or 0)
        if not 0 <= self.source_origin < self.duration:
            raise ValueError('invalid_seek_position')
        self.epoch = None
        self.preview = None
        self.boundary_error = None
        self.stop, self.ready = threading.Event(), threading.Event()
        self.errors, self.metrics = [], {}
        self.remote = None
        self._lock = threading.Lock()
        self._active = 0
        self._inflight_bytes = 0
        self.path = '/'+secrets.token_urlsafe(24)+'/media'
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args): pass
            def do_HEAD(self): self.handle_media(head=True)
            def do_GET(self): self.handle_media(head=False)
            def handle_media(self, head):
                if self.path != bridge.path or not bridge.ready.is_set():
                    self.send_error(404)
                    return
                with bridge._lock:
                    if bridge._active >= 4:
                        self.send_error(503)
                        return
                    bridge._active += 1
                try:
                    self.connection.settimeout(5)
                    try:
                        start, end, partial = requested_range(self.headers.get('Range'), bridge.remote.size)
                    except ValueError:
                        self.send_response(416)
                        self.send_header('Content-Range', f'bytes */{bridge.remote.size}')
                        self.send_header('Content-Length', '0')
                        self.end_headers()
                        return
                    self.send_response(206 if partial else 200)
                    self.send_header('Content-Type', 'video/mp4')
                    self.send_header('Accept-Ranges', 'bytes')
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Content-Length', str(end-start+1))
                    self.send_header('Connection', 'close')
                    if partial:
                        self.send_header('Content-Range', f'bytes {start}-{end}/{bridge.remote.size}')
                    self.end_headers()
                    if head:
                        return
                    while start <= end and not bridge.stop.is_set():
                        data = bridge.remote.read_range(start, min(end, start+CHUNK-1))
                        size = len(data)
                        with bridge._lock:
                            bridge._inflight_bytes += size
                            bridge.metrics['peak_proxy_bytes'] = max(bridge.metrics.get('peak_proxy_bytes', 0),
                                                                      bridge._inflight_bytes)
                        try:
                            self.wfile.write(data)
                            start += size
                        finally:
                            data = None
                            with bridge._lock:
                                bridge._inflight_bytes -= size
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    pass  # Qt routinely cancels old range requests on seek/stop.
                except Exception as error:
                    if not bridge.stop.is_set():
                        bridge.errors.append('source_'+safe_error(error))
                        bridge.stop.set()
                finally:
                    self.close_connection = True
                    with bridge._lock:
                        bridge._active -= 1

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.server.timeout = .1
        self.url = f'http://127.0.0.1:{self.server.server_port}{self.path}'
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.metrics.update(remaining_bytes=0, peak_compressed_bytes=0,
                            range_chunk_limit_bytes=CHUNK, max_concurrent_requests=4)

    def run(self):
        try:
            self.remote = RangeSource(self.selected['url'], self.selected['http_headers'], self.stop)
            self.ready.set()
            while not self.stop.is_set():
                self.server.handle_request()
        except Exception as error:
            if not self.stop.is_set():
                self.errors.append('source_'+safe_error(error))
                self.stop.set()

    def start(self):
        self.thread.start()

    def close(self):
        import time
        self.stop.set()
        if self.thread.ident:
            self.thread.join(6)
        self.server.server_close()
        deadline = time.monotonic()+6
        while self._active and time.monotonic() < deadline:
            time.sleep(.02)
        if self.remote:
            self.remote.url = ''
            self.remote.headers.clear()
        self.selected = {}
        self.url = ''
        self.metrics.update(threads_stopped=not self.thread.is_alive() and self._active == 0,
                            remaining_bytes=0, peak_compressed_bytes=0)


class FileRelay:
    """Recognition shares the proxy, not a second network producer."""
    def __init__(self, bridge):
        self.bridge = bridge
        self.stop = threading.Event()
        self.errors = []
        self.metrics = {}
    @property
    def ready(self): return self.bridge.ready
    @property
    def url(self): return self.bridge.url
    def start(self): pass
    def close(self):
        self.stop.set()
        self.metrics.update(threads_stopped=True, remaining_bytes=0, peak_compressed_bytes=0)
