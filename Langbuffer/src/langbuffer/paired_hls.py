"""Paired HLS tracks: verify common wall clock, restore packed AAC timestamps, remux in RAM."""
from contextlib import ExitStack
from datetime import datetime
from fractions import Fraction
import io
import threading
import time
from urllib.parse import urljoin

from .seekable_hls import PlaylistSource, read_window
from .range_media import bounded_get
from .source_extension.format_policy import _headers

LIMIT = 16 * 1024 * 1024


def program_times(text, base):
    result, stamp, duration = {}, None, None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith('#EXT-X-PROGRAM-DATE-TIME:'):
            value = datetime.fromisoformat(line.split(':', 1)[1].replace('Z', '+00:00'))
            if value.utcoffset() is None:
                raise ValueError('paired_clock_unverified')
            stamp = value.timestamp()
        elif line.startswith('#EXTINF:'):
            duration = float(line.split(':', 1)[1].split(',')[0])
        elif line and not line.startswith('#'):
            if stamp is not None and duration is not None:
                result[urljoin(base, line)] = stamp
                stamp += duration
            duration = None
    return result


def packed_timestamp(data):
    """RFC 8216 packed audio PRIV timestamp, in 90 kHz ticks (33 bits)."""
    if data[:3] != b'ID3' or len(data) < 10 or data[3] not in (3, 4) or data[5] != 0:
        raise ValueError('paired_clock_unverified')
    def syncsafe(value):
        if len(value) != 4 or any(x & 128 for x in value):
            raise ValueError('paired_clock_unverified')
        return sum(byte << (7*(3-index)) for index, byte in enumerate(value))
    limit = 10+syncsafe(data[6:10])
    if limit > len(data):
        raise ValueError('paired_clock_unverified')
    cursor = 10
    while cursor+10 <= limit:
        name = data[cursor:cursor+4]
        size = syncsafe(data[cursor+4:cursor+8]) if data[3] == 4 else int.from_bytes(data[cursor+4:cursor+8], 'big')
        if not size or cursor+10+size > limit:
            break
        body = data[cursor+10:cursor+10+size]
        if name == b'PRIV' and data[cursor+8:cursor+10] == b'\0\0':
            owner, separator, value = body.partition(b'\0')
            if separator and owner == b'com.apple.streaming.transportStreamTimestamp' and len(value) == 8:
                return int.from_bytes(value, 'big') & ((1 << 33)-1)
        cursor += 10+size
    raise ValueError('paired_clock_unverified')


class BoundedBytes(io.BytesIO):
    def write(self, data):
        if self.tell()+len(data) > LIMIT:
            raise ValueError('resource_byte_limit')
        return super().write(data)


def remux_pair(video_data, audio_data, *, media_start=0):
    import av
    if len(video_data)+len(audio_data) > LIMIT:
        raise ValueError('resource_byte_limit')
    with ExitStack() as stack:
        video = stack.enter_context(av.open(io.BytesIO(video_data)))
        audio = stack.enter_context(av.open(io.BytesIO(audio_data)))
        vs = next(iter(video.streams.video), None)
        aus = next(iter(audio.streams.audio), None)
        if vs is None or aus is None or vs.codec_context.name != 'h264' or aus.codec_context.name != 'aac':
            raise ValueError('paired_codec_unsupported')
        vpackets = [p for p in video.demux(vs) if p.dts is not None and p.size]
        apackets = [p for p in audio.demux(aus) if p.dts is not None and p.size]
        if not vpackets or not apackets:
            raise ValueError('media_decode_failed')
        vfirst = Fraction(vpackets[0].dts)*vpackets[0].time_base
        afirst = Fraction(apackets[0].dts)*apackets[0].time_base
        packed = str(audio.format.name).split(',')[0] == 'aac'
        audio_offset = Fraction(packed_timestamp(audio_data), 90000)-afirst if packed else Fraction(0)
        # Lift the 33-bit packed timestamp to the same MPEG timestamp epoch.
        if packed:
            wrap = Fraction(1 << 33, 90000)
            audio_offset += round((vfirst-afirst-audio_offset)/wrap)*wrap
        if abs(float(afirst+audio_offset-vfirst)) > .15:
            raise ValueError('paired_clock_unverified')
        output = BoundedBytes()
        mux = stack.enter_context(av.open(output, 'w', format='mpegts',
                                         options={'mpegts_copyts': '1'}))
        outv = mux.add_stream_from_template(vs)
        outa = mux.add_stream_from_template(aus)
        # Common normalization; retain measured audio/video offset. One segment at
        # a time, no transcoding and no faster-than-realtime delivery to ASR.
        offset = Fraction(str(media_start))+1-vfirst
        packets = [(p, outv, Fraction(0)) for p in vpackets]+[(p, outa, audio_offset) for p in apackets]
        packets.sort(key=lambda item: Fraction(item[0].dts)*item[0].time_base+item[2])
        for packet, target, extra in packets:
            shift = round((offset+extra)/packet.time_base)
            packet.dts += shift
            if packet.pts is not None:
                packet.pts += shift
            packet.stream = target
            mux.mux(packet)
        mux.close()
        return output.getvalue()


def select_tracks(info, height=0, fps=0):
    formats = info.get('formats') or ()
    videos = [f for f in formats if f.get('protocol') in ('m3u8', 'm3u8_native')
              and f.get('acodec') == 'none' and (f.get('vcodec') or '').startswith(('avc1', 'h264'))
              and f.get('height')]
    audios = [f for f in formats if f.get('protocol') in ('m3u8', 'm3u8_native')
              and f.get('vcodec') == 'none' and f.get('acodec') != 'none']
    qualities = sorted({(int(f['height']), round(f.get('fps') or 0)) for f in videos}, reverse=True)
    matching = [f for f in videos if (not height or int(f['height']) == height)
                and (not fps or round(f.get('fps') or 0) == fps)]
    if not videos or not audios:
        raise ValueError('separate_tracks_unimplemented')
    if not matching:
        raise ValueError('quality_unavailable')
    video = max(matching, key=lambda f: (f['height'], f.get('fps') or 0, f.get('tbr') or 0))
    audio = max(audios, key=lambda f: (f.get('abr') or f.get('tbr') or 0, f.get('format_id') or ''))
    if video.get('has_drm') or audio.get('has_drm'):
        raise ValueError('drm_unsupported')
    return dict(url=video['url'], audio_url=audio['url'],
                http_headers=dict(_headers(info, video)), audio_headers=dict(_headers(info, audio)),
                height=video['height'], fps=round(video.get('fps') or 0), width=video.get('width'),
                qualities=[dict(height=h, fps=f) for h, f in qualities],
                resolutions=sorted({h for h, _ in qualities}, reverse=True),
                transport='hls_paired', media_kind='live', duration=None)


class PairedPlaylistSource(PlaylistSource):
    def __init__(self, resolve, fetch, **kwargs):
        self.video_times, self.audio_times = {}, {}
        self.audio_source = None
        self.selected = None
        def video_fetch(url, headers, limit):
            data, base = fetch(url, headers, limit)
            self.video_times = program_times(data.decode('utf-8-sig'), base)
            return data, base
        def paired_resolve():
            selected = resolve()
            if self.stop.is_set():
                raise ValueError('source_cancelled')
            self.selected = selected
            def audio_fetch(url, headers, limit):
                data, base = fetch(url, headers, limit)
                self.audio_times = program_times(data.decode('utf-8-sig'), base)
                return data, base
            self.audio_source = PlaylistSource(
                lambda: dict(url=selected['audio_url'], http_headers=selected['audio_headers']), audio_fetch)
            self.audio_source.start()
            return selected
        super().__init__(paired_resolve, video_fetch, **kwargs)
        self._media_fetch = fetch

    def entries(self, *args, **kwargs):
        for entry in super().entries(*args, **kwargs):
            start, duration, video_url, init = entry
            deadline = time.monotonic()+20
            audio_entry = None
            while not self.stop.is_set() and time.monotonic() < deadline:
                stamp = self.video_times.get(video_url)
                window = self.audio_source.window(self.stop)
                if window is None:
                    return
                if stamp is None:
                    raise ValueError('paired_clock_unverified')
                audio_entry = next((item for item in window.segments
                    if abs(self.audio_times.get(item.url, -1)-stamp) < .02), None)
                if audio_entry is not None:
                    break
                if self.stop.wait(.1):
                    return
            if audio_entry is None or abs(audio_entry.duration-duration) > .06:
                raise ValueError('paired_clock_unverified')
            if audio_entry.error:
                raise ValueError(audio_entry.error)
            if audio_entry.boundary or init or audio_entry.init_url:
                raise ValueError('paired_codec_unsupported')
            yield (*entry, audio_entry.url)

    def load_pair(self, entry):
        video, _ = self._media_fetch(entry[2], self.headers, 8*1024*1024)
        audio, _ = self._media_fetch(entry[4], self.selected['audio_headers'], 2*1024*1024)
        return remux_pair(video, audio, media_start=entry[0])

    def close(self):
        self.stop.set()
        # Join the owner before looking up audio_source: it may still be created
        # while a stop races the initial resolver.
        super().close()
        if self.audio_source is not None:
            self.audio_source.close()
        if self.audio_source is not None and self.audio_source.thread.is_alive():
            raise ValueError('paired_cleanup_failed')
