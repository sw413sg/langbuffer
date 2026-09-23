"""Local recognition, translation and bounded subtitle presentation."""
from collections import deque
from dataclasses import dataclass
import multiprocessing
import math
import os
from pathlib import Path
from queue import Empty, Full
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'data'
RATE = 16000
ASR_MODELS = ('small.en', 'base.en', 'small')
MAX_BLOCK_FAILURES = 3


def worker_diagnostic(error, phase, block=0, audio_s=0):
    """Only fixed codes and numbers; exception messages may contain user text."""
    known = {'translation_input_too_long', 'translation_output_truncated',
             'empty_translation_input', 'empty_translation_output', 'translation_package_missing'}
    return dict(phase=phase, block=block, audio_s=round(audio_s, 3),
                exception=type(error).__name__,
                reason=str(error) if str(error) in known else 'unspecified')


def process_block(asr, translator, pcm, origin, cue_id, stop, report):
    """Isolate malformed recognition/translation units, preserving earlier cues."""
    from langbuffer.readability import caption_pages
    started = time.monotonic()
    try:
        segments, _ = asr.transcribe(pcm, language=translator.source, task='transcribe', beam_size=1,
            temperature=0, condition_on_previous_text=False, vad_filter=False, word_timestamps=True)
        segments = list(segments)
    except ValueError as error:
        report(error, 'recognition')
        return [], cue_id, time.monotonic()-started, 0.0, True
    except Exception as error:
        report(error, 'recognition')
        raise
    asr_s, translation_s, failed = time.monotonic()-started, 0.0, False
    cues = []
    for segment in segments:
        if stop.is_set():
            break
        original = segment.text.strip()
        if not original:
            continue
        started = time.monotonic()
        try:
            result = translator.translate(original)
        except (ValueError, RuntimeError) as error:
            report(error, 'translation')
            failed = True
            continue
        finally:
            translation_s += time.monotonic()-started
        start = origin + max(0, segment.start)
        end = origin + min(len(pcm)/RATE, segment.end)
        if end <= start:
            continue
        pages = caption_pages(result.text)
        total, page_start = sum(len(page) for page in pages), start
        for page in pages:
            page_end = page_start+(end-start)*len(page)/max(1, total)
            cues.append(dict(id=cue_id, start=page_start, end=page_end, text=page, original=original))
            cue_id += 1
            page_start = page_end
    return cues, cue_id, asr_s, translation_s, failed


def model_path(name):
    if name not in ASR_MODELS:
        raise ValueError('unsupported_local_asr_model')
    return WORK / 'models' / name


def model_environment():
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['HF_HOME'] = str(WORK / 'hf-cache')


def recognize_translate(jobs, events, stop, generation, model_name='base.en',
                        source_language='en', target_language='es'):
    model_environment()

    def emit(event):
        event['generation'] = generation
        if not stop.is_set():
            events.put(event, timeout=1)

    phase, block, audio_s = 'imports', 0, 0.0
    try:
        import numpy as np
        from faster_whisper import WhisperModel
        from langbuffer.local_translation import LocalTranslation
        from langbuffer.language_packages import bundled_package_rejected
        started = time.monotonic()
        phase = 'load_recognition'
        asr = WhisperModel(str(model_path(model_name)), device='cpu', compute_type='int8',
                           cpu_threads=4, num_workers=1, local_files_only=True)
        phase = 'load_translation'
        translator = LocalTranslation(source_language, target_language, threads=2)
        emit(dict(kind='ready', model=model_name, source_language=source_language,
                  target_language=target_language, bundled_rejected=bundled_package_rejected(),
                  load_s=round(time.monotonic()-started, 3)))
        cue_id, failures = 0, 0
        while not stop.is_set():
            try:
                job = jobs.get(timeout=.1)
            except Empty:
                continue
            if job is None:
                break
            payload, origin = job
            pcm = np.frombuffer(payload, dtype=np.float32)
            block += 1
            audio_s = len(pcm)/RATE
            phase = 'process_block'
            def report(error, step):
                emit(dict(kind='warning', code='local_block_'+type(error).__name__,
                          diagnostic=worker_diagnostic(error, step, block, audio_s)))
            cues, cue_id, asr_s, translation_s, failed = process_block(
                asr, translator, pcm, origin, cue_id, stop, report)
            emit(dict(kind='cues', cues=cues, audio_s=len(pcm)/RATE,
                      asr_s=round(asr_s, 3), translation_s=round(translation_s, 3)))
            failures = failures+1 if failed else 0
            if failures >= MAX_BLOCK_FAILURES:
                emit(dict(kind='error', code='local_worker_repeated_block_failure',
                          diagnostic=dict(phase=phase, block=block, audio_s=audio_s)))
                return
    except Exception as exc:
        try:
            rejected = locals().get('bundled_package_rejected', lambda: False)
            emit(dict(kind='error', code='local_worker_'+type(exc).__name__,
                      bundled_rejected=rejected(),
                      diagnostic=worker_diagnostic(exc, phase, block, audio_s)))
        except Full:
            pass


class LocalWorker:
    def __init__(self, generation, model_name='base.en', source_language='en', target_language='es'):
        from langbuffer.language_packages import effective_asr_model, LANGUAGES
        if source_language not in LANGUAGES or target_language not in LANGUAGES:
            raise ValueError('unsupported_language')
        model_name = effective_asr_model(model_name, source_language)
        model_path(model_name)
        context = multiprocessing.get_context('spawn')
        self.jobs = context.Queue(maxsize=4)
        self.events = context.Queue(maxsize=48)
        self.stop = context.Event()
        self.dropped_blocks = 0
        self.dropped_audio_s = 0.0
        self.process = context.Process(target=recognize_translate,
            args=(self.jobs, self.events, self.stop, generation, model_name,
                  source_language, target_language), daemon=True)
        self.process.start()

    def submit(self, samples, origin):
        job = (samples.tobytes(), origin)
        try:
            self.jobs.put_nowait(job)
        except Full:
            # Prefer current audio to an old backlog, without blocking Qt or
            # increasing memory. A multiprocessing feeder may not yet expose
            # the oldest item; in that race drop this new block instead.
            try:
                discarded, _ = self.jobs.get_nowait()
            except Empty:
                self._dropped(job[0])
                return False
            self._dropped(discarded)
            try:
                self.jobs.put_nowait(job)
            except Full:
                self._dropped(job[0])
                return False
        return True

    def _dropped(self, payload):
        self.dropped_blocks += 1
        self.dropped_audio_s += len(payload)/(4*RATE)

    def poll(self):
        values = []
        for _ in range(48):
            try:
                values.append(self.events.get_nowait())
            except Empty:
                break
        return values

    def close(self):
        self.stop.set()
        deadline = time.monotonic()+8
        while self.process.is_alive() and time.monotonic() < deadline:
            self.poll()  # Drain the result feeder so process shutdown cannot deadlock.
            self.process.join(.05)
        forced = self.process.is_alive()
        if forced:
            self.process.terminate()
            self.process.join(2)
        for channel in (self.jobs, self.events):
            channel.cancel_join_thread()
            channel.close()
        return dict(worker_stopped=not self.process.is_alive(), worker_forced_stop=forced)


class AudioChunks:
    """Provisional energy segmentation: 600 ms silence or about four seconds."""
    def __init__(self, submit):
        self.submit = submit
        self.parts = []
        self.count = self.silence = 0
        self.origin = 0.0

    def feed(self, samples, pts):
        import numpy as np
        for offset in range(0, len(samples), 320):
            frame = samples[offset:offset+320]
            voiced = float(np.sqrt(np.mean(frame*frame))) > .003
            if not self.parts and not voiced:
                continue
            if not self.parts:
                self.origin = pts+offset/RATE
            self.parts.append(frame.copy())
            self.count += len(frame)
            self.silence = 0 if voiced else self.silence+len(frame)
            if self.silence >= RATE*.6 or self.count >= RATE*4:
                self.flush()

    def flush(self):
        if self.parts:
            import numpy as np
            self.submit(np.concatenate(self.parts), self.origin)
        self.clear()

    def clear(self):
        self.parts = []
        self.count = self.silence = 0


@dataclass(frozen=True)
class Cue:
    id: int
    start: float
    end: float
    text: str
    original: str


class FrozenTimeline:
    def __init__(self, generation):
        self.generation = generation
        self.pending = deque()
        self.active = None
        self.last_id = -1
        self.now = -1.0
        self.published = self.late = self.obsolete = 0
        self.peak_pending = 0
        self.offset = 0.0
        self.active_until = None
        self.read_until = None

    def set_offset(self, value):
        value = float(value)
        if not math.isfinite(value) or not -5 <= value <= 5:
            raise ValueError('invalid_caption_offset')
        # Keep the published page and its deadline fixed; apply to pending pages.
        self.offset = round(value, 1)

    def receive(self, generation, cues):
        if generation != self.generation:
            self.obsolete += len(cues)
            return
        for data in cues:
            cue = Cue(**data)
            if cue.id <= self.last_id:
                self.obsolete += 1
                continue
            self.last_id = cue.id
            if (cue.start+self.offset < self.now
                    or self.active and cue.start+self.offset < self.active_until):
                self.late += 1
                continue
            if len(self.pending) >= 48:
                raise BufferError('caption_queue_full')
            self.pending.append(cue)
            self.peak_pending = max(self.peak_pending, len(self.pending))

    def tick(self, media_time):
        self.now = media_time
        if self.active and media_time < self.active_until:
            return self.active
        # Keep a finished utterance readable through a gap, but let the next
        # scheduled page replace it on time. Never turn this tail into a queue
        # of delayed captions or reject new cues because they overlap the tail.
        next_due = self.pending and self.pending[0].start+self.offset <= media_time
        if self.active and not next_due and media_time < self.read_until:
            return self.active
        self.active = None
        while self.pending and self.pending[0].end+self.offset <= media_time:
            self.pending.popleft()
            self.late += 1
        if self.pending and self.pending[0].start+self.offset <= media_time:
            self.active = self.pending.popleft()
            self.active_until = self.active.end+self.offset
            characters = len(' '.join(self.active.text.split()))
            reading_seconds = max(2.0, min(6.0, characters/17.0))
            self.read_until = max(self.active_until, self.active.start+self.offset+reading_seconds)
            self.published += 1
        return self.active

    def clear(self):
        self.pending.clear()
        self.active = None
        self.active_until = None
        self.read_until = None
