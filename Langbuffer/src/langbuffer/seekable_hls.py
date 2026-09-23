"""Ventana HLS disponible en el servidor. Solo metadatos; medios en el transporte."""
from dataclasses import dataclass
import math
import re
import threading
import time
from urllib.parse import urljoin


@dataclass(frozen=True)
class Segment:
    sequence: int
    start: float
    duration: float
    url: str
    error: str | None = None
    boundary: bool = False
    init_url: str | None = None


@dataclass(frozen=True)
class Window:
    segments: tuple
    target: float
    live: bool

    @property
    def start(self):
        return self.segments[0].start

    @property
    def end(self):
        last = self.segments[-1]
        return last.start + last.duration

    @property
    def live_start(self):
        index = len(self.segments)-1
        while index > 0 and self.end-self.segments[index].start < 3*self.target:
            index -= 1
        return self.segments[index].start

    def select(self, position=None):
        if position == 'live':
            position = self.live_start if self.live else self.segments[-1].start
        elif position is None:
            position = self.live_start if self.live else self.start
        if not isinstance(position, (int, float)) or not math.isfinite(position):
            raise ValueError('invalid_seek_position')
        if position < self.start-.001 or position > self.end+.001:
            raise ValueError('live_window_expired')
        return next((entry for entry in reversed(self.segments) if entry.start <= position+.001), self.segments[0])

    def select_latest_epoch(self):
        """Inicio de Twitch: margen de directo limitado al último formato/reloj.

        No interpretar un cambio como publicidad ni omitir errores de segmentos.
        Evita volver a cruzar una frontera antigua al reconstruir el transporte.
        """
        selected = self.select()
        for previous, entry in zip(self.segments, self.segments[1:]):
            if (entry.boundary or entry.init_url != previous.init_url) and entry.start > selected.start:
                selected = entry
        return selected


def read_window(text, base, previous=None):
    if not text.lstrip().startswith('#EXTM3U'):
        raise ValueError('not_hls')
    lines = [line.strip() for line in text.splitlines()]
    def integer(prefix, default):
        values = [line[len(prefix):] for line in lines if line.startswith(prefix)]
        if not values:
            return default
        if len(values) != 1 or not values[0].isdigit():
            raise ValueError('invalid_live_playlist')
        return int(values[0])
    sequence = integer('#EXT-X-MEDIA-SEQUENCE:', 0)
    # Inspeccionar metadatos de todo el historial sin exigir que todas las épocas
    # puedan concatenarse. La validación de medios corresponde al tramo elegido.
    rows, annotations = [], []
    duration, start, key_error, map_error, segment_error, boundary = None, 0.0, None, None, None, False
    init_url = None
    for line in lines:
        tag = line.split(':', 1)[0]
        if tag == '#EXT-X-STREAM-INF':
            raise ValueError('unsupported_hls_feature_master')
        if tag == '#EXT-X-KEY':
            key_error = None if line == '#EXT-X-KEY:METHOD=NONE' else 'encrypted_hls_not_supported'
        elif tag == '#EXT-X-MAP':
            # Inicialización completa fMP4. Los mapas por rangos siguen excluidos.
            match = re.fullmatch(r'#EXT-X-MAP:URI="([^"\r\n]+)"', line)
            init_url = urljoin(base, match[1]) if match else None
            map_error = None if match else 'unsupported_hls_feature_map'
        elif tag in ('#EXT-X-GAP', '#EXT-X-BYTERANGE'):
            segment_error = 'unsupported_hls_feature_'+tag.rsplit('-', 1)[1].lower()
        elif tag == '#EXT-X-DISCONTINUITY':
            boundary = True
        elif tag == '#EXTINF':
            duration = float(line.split(':', 1)[1].split(',')[0])
        elif line and not line.startswith('#'):
            if duration is None or not math.isfinite(duration) or duration <= 0:
                raise ValueError('invalid_segment_duration')
            rows.append((start, duration, urljoin(base, line)))
            # Kick publica segmentos nominales de 10 s que ocasionalmente llegan
            # a unos 12,5 s. El tope sigue acotado y rechaza tramos anómalos.
            annotations.append((key_error or map_error or segment_error or ('invalid_segment_duration' if duration > 15 else None), boundary, init_url))
            start += duration
            duration, segment_error, boundary = None, None, False
    if not rows:
        raise ValueError('no_segments')
    target = integer('#EXT-X-TARGETDURATION:', math.ceil(max(row[1] for row in rows)))
    if not 1 <= target <= 60:
        raise ValueError('invalid_live_playlist')
    offset = 0.0
    if previous is not None:
        if sequence < previous.segments[0].sequence:
            raise ValueError('live_sequence_reset')
        old = {entry.sequence: entry for entry in previous.segments}
        anchor = next(((i, row) for i, row in enumerate(rows) if sequence+i in old), None)
        if anchor is None:
            if sequence != previous.segments[-1].sequence+1:
                raise ValueError('live_window_expired')
            offset = previous.end
        else:
            index, row = anchor
            offset = old[sequence+index].start-row[0]
        for index, (start, duration, _) in enumerate(rows):
            existing = old.get(sequence+index)
            if existing and (abs(existing.start-(offset+start)) > .02 or abs(existing.duration-duration) > .02):
                raise ValueError('live_timeline_changed')
    entries = tuple(Segment(sequence+i, offset+start, duration, url, *annotations[i])
                    for i, (start, duration, url) in enumerate(rows))
    return Window(entries, target, '#EXT-X-ENDLIST' not in lines)


class PlaylistSource:
    """Renueva incluso en pausa/DVR. Una sola instantánea, sin historial creciente."""
    def __init__(self, resolve, fetch, reference_window=None, follow_endlist=False):
        self.resolve, self.fetch = resolve, fetch
        self.reference_window = reference_window
        self.follow_endlist = follow_endlist
        self.condition = threading.Condition()
        self.stop = threading.Event()
        self.snapshot = None
        self.error = None
        self.headers = {}
        self.details = {}
        self.refreshes = 0
        self.thread = threading.Thread(target=self.run, daemon=True)
        self._started = False

    def start(self):
        with self.condition:
            if not self._started:
                self._started = True
                self.thread.start()

    def run(self):
        try:
            selected = self.resolve()
            self.headers = selected.get('http_headers') or {}
            self.details = {key: selected.get(key) for key in
                            ('width', 'height', 'fps', 'resolutions', 'qualities')}
            last_progress = time.monotonic()
            previous_text = None
            while not self.stop.is_set():
                data, base = self.fetch(selected['url'], self.headers, 2*1024*1024)
                text = data.decode('utf-8-sig')
                current = read_window(text, base, self.snapshot or self.reference_window)
                endlist = not current.live
                if self.follow_endlist and not current.live:
                    # La repetición activa de Kick cierra cada instantánea con
                    # ENDLIST aunque la misma URL siga añadiendo segmentos.
                    current = Window(current.segments, current.target, True)
                self.reference_window = None
                if self.snapshot is None or current.end > self.snapshot.end:
                    last_progress = time.monotonic()
                elif current.live and time.monotonic()-last_progress >= max(30, 3*current.target):
                    if self.follow_endlist and endlist:
                        # Kick uses ENDLIST for both a growing replay and its
                        # final snapshot. Keep all existing DVR entries usable;
                        # at the edge, an unchanged ENDLIST can finish normally.
                        # Continue metadata polling in case growth resumes while
                        # a delayed/paused viewer is still consuming the replay.
                        current = Window(current.segments, current.target, False)
                    else:
                        raise ValueError('live_playlist_stalled')
                with self.condition:
                    self.snapshot = current
                    self.condition.notify_all()
                if not current.live and not self.follow_endlist:
                    return
                # Kick publica la repetición activa en tandas de varios segmentos.
                # Consultar solo cada TARGETDURATION puede descubrir la tanda demasiado
                # tarde para un consumidor continuo; la consulta sigue siendo solo de
                # metadatos y permanece acotada.
                interval = (min(2.0, current.target/2) if self.follow_endlist else
                            current.target/2 if text == previous_text else current.target)
                previous_text = text
                if self.stop.wait(interval):
                    return
                self.refreshes += 1
        except Exception as error:
            # Solo códigos conocidos deben salir del transporte, nunca URLs ni texto.
            with self.condition:
                self.error = error
                self.condition.notify_all()

    def window(self, cancel):
        with self.condition:
            while self.snapshot is None and self.error is None and not self.stop.is_set() and not cancel.is_set():
                self.condition.wait(.1)
            if self.error is not None:
                raise self.error
            return None if cancel.is_set() or self.stop.is_set() else self.snapshot

    def entries(self, position, seconds, cancel, origin, with_init=False, latest_epoch=False):
        current = self.window(cancel)
        if current is None:
            return
        selected = current.select_latest_epoch() if latest_epoch else current.select(position)
        origin(selected.start)
        next_sequence, media_time = selected.sequence, 0.0
        initial_url = selected.init_url
        while not cancel.is_set() and not self.stop.is_set():
            current = self.window(cancel)
            if current is None:
                return
            if next_sequence < current.segments[0].sequence:
                raise ValueError('live_window_expired')
            index = next_sequence-current.segments[0].sequence
            if index < len(current.segments):
                entry = current.segments[index]
                if entry.sequence != next_sequence:
                    raise ValueError('live_window_expired')
                if entry.error:
                    raise ValueError(entry.error)
                if entry.boundary and media_time > 0:
                    raise ValueError('unsupported_hls_feature_discontinuity')
                if entry.init_url != initial_url:
                    raise ValueError('unsupported_hls_feature_map_change')
                if with_init:
                    yield media_time, entry.duration, entry.url, entry.init_url
                else:
                    if entry.init_url:
                        raise ValueError('unsupported_hls_feature_map')
                    yield media_time, entry.duration, entry.url
                media_time += entry.duration
                next_sequence += 1
                if seconds and media_time >= seconds:
                    return
            elif not current.live:
                return
            else:
                with self.condition:
                    self.condition.wait(.1)

    def close(self):
        self.stop.set()
        with self.condition:
            self.condition.notify_all()
        if self.thread.ident is not None:
            self.thread.join(12)
