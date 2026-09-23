import io
import unittest
from fractions import Fraction
import av

from synthetic import fragment
from live_translate.paired_hls import remux_pair, packed_timestamp


def packed_pair(offset=0, timestamp_shift=0):
    init, media = fragment(offset, 180)
    video_data, audio_data = io.BytesIO(), io.BytesIO()
    first_audio = None
    with av.open(io.BytesIO(init+media)) as source, \
            av.open(video_data, 'w', format='mpegts', options={'mpegts_copyts': '1'}) as video, \
            av.open(audio_data, 'w', format='adts') as audio:
        vs, aus = source.streams.video[0], source.streams.audio[0]
        outv, outa = video.add_stream_from_template(vs), audio.add_stream_from_template(aus)
        for packet in source.demux(vs, aus):
            if packet.dts is None:
                continue
            if packet.stream.type == 'video':
                packet.stream = outv
                video.mux(packet)
            else:
                # fragment() encodes every fixture independently and flushes a
                # tail past its two-second slot. Real HLS partitions one encoder
                # stream; keep only this fixture's declared slot when splitting.
                if Fraction(packet.pts)*packet.time_base >= offset+2:
                    continue
                if first_audio is None:
                    first_audio = Fraction(packet.dts)*packet.time_base
                packet.stream = outa
                audio.mux(packet)
    ticks = round((first_audio+timestamp_shift)*90000) & ((1 << 33)-1)
    body = b'com.apple.streaming.transportStreamTimestamp\0'+ticks.to_bytes(8, 'big')
    sync = lambda n: bytes((n >> 21 & 127, n >> 14 & 127, n >> 7 & 127, n & 127))
    frame = b'PRIV'+sync(len(body))+b'\0\0'+body
    tag = b'ID3\x04\0\0'+sync(len(frame))+frame
    return video_data.getvalue(), tag+audio_data.getvalue()


class PairedMediaTests(unittest.TestCase):
    def test_three_segments_decode_with_monotonic_audio_and_video(self):
        joined = b''.join(remux_pair(*packed_pair(offset), media_start=offset) for offset in (0, 2, 4))
        stamps = {'audio': [], 'video': []}
        with av.open(io.BytesIO(joined)) as source:
            for packet in source.demux():
                for frame in packet.decode():
                    stamps[packet.stream.type].append(float(frame.pts*frame.time_base))
        self.assertGreater(len(stamps['video']), 100)
        self.assertGreater(len(stamps['audio']), 150)
        for values in stamps.values():
            self.assertGreater(values[-1]-values[0], 5)
            self.assertTrue(all(a < b for a, b in zip(values, values[1:])))
            self.assertLess(max(b-a for a, b in zip(values, values[1:])), .12)
        self.assertLess(abs(stamps['audio'][0]-stamps['video'][0]), .15)

    def test_mismatched_source_clocks_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'paired_clock_unverified'):
            remux_pair(*packed_pair(timestamp_shift=2))


if __name__ == '__main__':
    unittest.main()
