"""Bounded HTTPS range reads. Media stays in memory; no global HTTP monkeypatches."""
import io
import math
import re
import threading
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

CHUNK = 1024 * 1024


def open_https(url, headers):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.username or parsed.password:
        raise ValueError('https_required')
    response = urlopen(Request(url, headers=headers), timeout=5)
    if urlsplit(response.url).scheme != 'https':
        response.close()
        raise ValueError('https_required')
    return response


def bounded_get(url, headers, limit, stop=None):
    stop = stop or threading.Event()
    with open_https(url, headers) as response:
        chunks, size = [], 0
        while not stop.is_set():
            data = response.read(min(65536, limit+1-size))
            if not data:
                return b''.join(chunks), response.url
            chunks.append(data)
            size += len(data)
            if size > limit:
                raise ValueError('resource_byte_limit')
    raise ValueError('source_cancelled')


class RangeSource:
    def __init__(self, url, headers, stop=None):
        self.url, self.headers = url, dict(headers)
        self.stop = stop or threading.Event()
        self.size = None
        self.validator = None
        self.read_range(0, 0)

    def read_range(self, start, end):
        if self.stop.is_set():
            raise ValueError('source_cancelled')
        if start < 0 or end < start or end-start+1 > CHUNK:
            raise ValueError('invalid_media_range')
        headers = dict(self.headers, Range=f'bytes={start}-{end}', **{'Accept-Encoding': 'identity'})
        with open_https(self.url, headers) as response:
            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
            if response.status != 206 or not match:
                raise ValueError('range_not_supported')
            first, last, total = map(int, match.groups())
            if first != start or total <= 0 or last != min(end, total-1):
                raise ValueError('invalid_media_range')
            if self.size is not None and self.size != total:
                raise ValueError('media_resource_changed')
            validator = response.headers.get('ETag') or response.headers.get('Last-Modified')
            if self.validator and validator != self.validator:
                raise ValueError('media_resource_changed')
            expected = last-first+1
            parts, received = [], 0
            while received < expected and not self.stop.is_set():
                part = response.read(min(65536, expected-received))
                if not part:
                    raise ValueError('incomplete_media_range')
                parts.append(part)
                received += len(part)
            if self.stop.is_set():
                raise ValueError('source_cancelled')
            self.size, self.validator = total, validator
            return b''.join(parts)


class RangeReader(io.RawIOBase):
    """Seekable file view for bounded PyAV metadata/decode inspection."""
    def __init__(self, source, budget=32*1024*1024):
        self.source, self.position, self.budget = source, 0, budget
        self.cache, self.cache_at = b'', 0

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.position

    def seek(self, offset, whence=0):
        position = offset if whence == 0 else self.position+offset if whence == 1 else self.source.size+offset
        if position < 0:
            raise ValueError('invalid_media_range')
        self.position = position
        return position

    def read(self, size=-1):
        if self.position >= self.source.size:
            return b''
        size = min(size if size >= 0 else CHUNK, CHUNK, self.source.size-self.position)
        if not self.cache_at <= self.position < self.cache_at+len(self.cache):
            count = min(CHUNK, self.source.size-self.position)
            if count > self.budget:
                raise ValueError('media_probe_limit')
            self.cache = self.source.read_range(self.position, self.position+count-1)
            self.cache_at = self.position
            self.budget -= len(self.cache)
        offset = self.position-self.cache_at
        result = self.cache[offset:offset+size]
        self.position += len(result)
        return result

    def close(self):
        self.cache = b''
        super().close()


def inspect_av(resource, *, finite=False):
    import av
    with av.open(resource) as container:
        video = next(iter(container.streams.video), None)
        audio = next(iter(container.streams.audio), None)
        if video is None or audio is None:
            raise ValueError('separate_tracks_unimplemented')
        seen = set()
        for index, packet in enumerate(container.demux(video, audio)):
            for frame in packet.decode():
                seen.add(packet.stream.type)
                if frame.pts is None:
                    raise ValueError('media_pts_missing')
            if len(seen) == 2:
                break
            if index >= 255:
                break
        if seen != {'audio', 'video'}:
            raise ValueError('media_decode_failed')
        duration = container.duration/av.time_base if container.duration else None
        if finite and (duration is None or not math.isfinite(duration) or duration <= 0):
            raise ValueError('finite_duration_unverified')
        return dict(height=video.height, width=video.width,
                    fps=round(float(video.average_rate or 0)),
                    vcodec=video.codec_context.name, acodec=audio.codec_context.name,
                    duration=duration)

