"""Cola circular de segmentos comprimidos; independiente del proveedor y de Qt."""
from collections import deque
from dataclasses import dataclass
import math
import threading
import time


@dataclass(frozen=True)
class MediaChunk:
    start: float
    duration: float
    data: bytes


class CompressedBuffer:
    def __init__(self, max_bytes, max_seconds):
        if type(max_bytes) is not int or max_bytes <= 0 or not math.isfinite(max_seconds) or max_seconds <= 0:
            raise ValueError('invalid_capacity')
        self.max_bytes, self.max_seconds = max_bytes, max_seconds
        self.chunks = deque()
        self.bytes = 0
        self.seconds = 0.0
        self.peak_bytes = self.peak_chunks = 0
        self.retired = 0
        self.finished = self.stopped = False
        self.end = 0.0
        self.condition = threading.Condition()

    def _validate(self, chunk):
        if (not math.isfinite(chunk.start) or not math.isfinite(chunk.duration)
                or chunk.duration <= 0 or not chunk.data or not isinstance(chunk.data, bytes)
                or abs(chunk.start - self.end) > .001):
            raise ValueError('invalid_chunk_timeline')
        if len(chunk.data) > self.max_bytes or chunk.duration > self.max_seconds:
            raise BufferError('compressed_buffer_full')

    def _append(self, chunk):
        self.chunks.append(chunk)
        self.bytes += len(chunk.data)
        self.seconds += chunk.duration
        self.end = chunk.start + chunk.duration
        self.peak_bytes = max(self.peak_bytes, self.bytes)
        self.peak_chunks = max(self.peak_chunks, len(self.chunks))
        self.condition.notify_all()

    def push(self, chunk):
        with self.condition:
            if self.finished or self.stopped:
                raise RuntimeError('buffer_closed')
            self._validate(chunk)
            if self.bytes + len(chunk.data) > self.max_bytes or self.seconds + chunk.duration > self.max_seconds:
                raise BufferError('compressed_buffer_full')
            self._append(chunk)

    def wait_and_push(self, chunk, cancel):
        """Espera espacio acotado para una reserva comprimida; nunca crece la cola."""
        with self.condition:
            if self.finished or self.stopped:
                raise RuntimeError('buffer_closed')
            self._validate(chunk)
            while (self.bytes + len(chunk.data) > self.max_bytes
                   or self.seconds + chunk.duration > self.max_seconds):
                if cancel.is_set():
                    return False
                self.condition.wait(.1)
                if self.finished or self.stopped:
                    return False
            if cancel.is_set():
                return False
            self._append(chunk)
            return True

    def pull(self, timeout=1):
        deadline = time.monotonic() + timeout
        with self.condition:
            while not self.chunks and not self.finished and not self.stopped:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('waiting_for_segment')
                self.condition.wait(remaining)
            if self.stopped or not self.chunks:
                return None
            chunk = self.chunks.popleft()
            self.bytes -= len(chunk.data)
            self.seconds = max(0, self.seconds - chunk.duration)
            self.retired += 1
            self.condition.notify_all()
            return chunk

    def finish(self):
        with self.condition:
            self.finished = True
            self.condition.notify_all()

    def clear(self):
        with self.condition:
            self.stopped = True
            self.chunks.clear()
            self.bytes = 0
            self.seconds = 0
            self.condition.notify_all()
