"""Synthetic fixtures; no remote media or stored speech."""
from collections import deque
from types import SimpleNamespace
import threading
from fractions import Fraction
import io
import math
from live_translate.media_buffer import MediaChunk

class SyntheticWorker:
    """Known cues isolate session control from recognition quality on a sine wave."""
    def __init__(self, generation, model_name='base.en', source_language='en', target_language='es'):
        self.generation, self.next_id = generation, 0
        self.stop = threading.Event()
        self.process = SimpleNamespace(is_alive=lambda: not self.stop.is_set())
        self.events = deque([dict(kind='ready', generation=generation, model=model_name, load_s=0)])

    def submit(self, samples, origin):
        self.events.append(dict(kind='cues', generation=self.generation,
            audio_s=len(samples)/16000, asr_s=0, translation_s=0,
            cues=[dict(id=self.next_id, start=origin, end=origin+len(samples)/16000,
                       text='Subtítulo sintético de control.', original='Synthetic control caption.')]))
        self.next_id += 1

    def poll(self):
        events = list(self.events)
        self.events.clear()
        return events

    def close(self):
        self.stop.set()
        self.events.clear()
        return dict(worker_stopped=True, worker_forced_stop=False)

def fragment(offset, height):
    import av
    import numpy as np
    memory = io.BytesIO()
    with av.open(memory, 'w', format='mp4', options={
            'movflags': 'frag_keyframe+empty_moov+default_base_moof'}) as mux:
        video = mux.add_stream('libx264', rate=30)
        video.width, video.height, video.pix_fmt = height*16//9, height, 'yuv420p'
        video.options = {'preset': 'ultrafast', 'tune': 'zerolatency'}
        video.gop_size = 60
        audio = mux.add_stream('aac', rate=48000)
        audio.layout = 'stereo'
        sample = offset*48000
        for i in range(60):
            pixels = np.zeros((height, video.width, 3), dtype=np.uint8)
            pixels[:, :, 2] = 70
            x = i*5 % (video.width-40)
            pixels[height//3:height*2//3, x:x+40] = (30, 200, 180)
            frame = av.VideoFrame.from_ndarray(pixels, format='rgb24')
            frame.pts, frame.time_base = offset*30+i, Fraction(1, 30)
            for packet in video.encode(frame):
                mux.mux(packet)
            while sample/48000 < offset+(i+1)/30:
                wave = (np.sin(2*np.pi*440*np.arange(sample, sample+1024)/48000)*.02).astype(np.float32)
                sound = av.AudioFrame.from_ndarray(np.tile(wave, (2, 1)), format='fltp', layout='stereo')
                sound.sample_rate, sound.pts, sound.time_base = 48000, sample, Fraction(1, 48000)
                for packet in audio.encode(sound):
                    mux.mux(packet)
                sample += 1024
        for stream in (video, audio):
            for packet in stream.encode(None):
                mux.mux(packet)
    data = memory.getvalue()
    index, first = 0, None
    while index < len(data):
        size = int.from_bytes(data[index:index+4], 'big')
        assert size >= 8
        kind = data[index+4:index+8]
        if kind == b'moof' and first is None:
            first = index
        if kind == b'mfra':
            data = data[:index]
            break
        index += size
    assert first is not None
    # Cada mux independiente normaliza tfdt a cero aunque los frames tengan
    # PTS absolutos. Dar tiempos consecutivos a estos fragmentos sintéticos:
    # sin esto Qt muestra el primer fragmento y descarta los que retroceden.
    initialization = data[:first]
    media = bytearray(data[first:])
    with av.open(io.BytesIO(data)) as demux:
        scales = {stream.id: stream.time_base for stream in demux.streams}
    def boxes(start, end):
        while start < end:
            size = int.from_bytes(media[start:start+4], 'big')
            assert size >= 8 and start+size <= end
            yield media[start+4:start+8], start, start+size
            start += size
    for kind, start, end in boxes(0, len(media)):
        if kind != b'moof':
            continue
        for child, first_child, last_child in boxes(start+8, end):
            if child == b'mfhd':
                media[first_child+12:first_child+16] = (offset//2+1).to_bytes(4, 'big')
            elif child == b'traf':
                contents = list(boxes(first_child+8, last_child))
                tfhd = next(pos for name, pos, _ in contents if name == b'tfhd')
                track = int.from_bytes(media[tfhd+12:tfhd+16], 'big')
                tfdt = next(pos for name, pos, _ in contents if name == b'tfdt')
                assert media[tfdt+8] == 1
                original = int.from_bytes(media[tfdt+12:tfdt+20], 'big')
                media[tfdt+12:tfdt+20] = (original+int(offset/scales[track])).to_bytes(8, 'big')
    return initialization, bytes(media)

def synthetic(seconds, offset=0, height=360):
    """Genera video con movimiento y tono en RAM; sin archivos de medios."""
    import av
    import numpy as np
    from fractions import Fraction
    memory = io.BytesIO()
    with av.open(memory, 'w', format='mpegts') as mux:
        video = mux.add_stream('mpeg2video', rate=30)
        width = height*16//9
        video.width, video.height, video.pix_fmt = width, height, 'yuv420p'
        audio = mux.add_stream('mp2', rate=48000)
        audio.layout = 'stereo'
        sample = offset*48000
        for index in range(seconds * 30):
            pixels = np.zeros((height, width, 3), dtype=np.uint8)
            pixels[:, :, 2] = 70
            x = ((index+offset*30) * 5) % (width-width//6)
            pixels[height//3:height*2//3, x:x+width//6] = (30, 200, 180)
            frame = av.VideoFrame.from_ndarray(pixels, format='rgb24')
            frame.pts, frame.time_base = index+offset*30, Fraction(1, 30)
            for packet in video.encode(frame):
                mux.mux(packet)
            while sample / 48000 < offset+(index + 1) / 30:
                phase = np.arange(sample, sample + 1152) / 48000
                wave = (np.sin(2 * np.pi * 440 * phase) * 1000).astype(np.int16)
                sound = av.AudioFrame.from_ndarray(np.tile(wave, (2, 1)), format='s16p', layout='stereo')
                sound.sample_rate, sound.pts, sound.time_base = 48000, sample, Fraction(1, 48000)
                for packet in audio.encode(sound):
                    mux.mux(packet)
                sample += 1152
        for stream in (video, audio):
            for packet in stream.encode(None):
                mux.mux(packet)
    data = memory.getvalue()
    count = math.ceil(seconds / 2)
    packets = len(data) // 188
    chunks = []
    for index in range(count):
        first = (packets * index // count) * 188
        last = (packets * (index + 1) // count) * 188
        chunks.append(MediaChunk(index * 2, min(2, seconds - index * 2), data[first:last]))
    return chunks

def playlist(sequence, count=3, end=False):
    return ('#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:'+str(sequence)+'\n'
            + ''.join(f'#EXTINF:2,\n{n}.ts\n' for n in range(sequence, sequence+count))
            + ('#EXT-X-ENDLIST\n' if end else ''))
