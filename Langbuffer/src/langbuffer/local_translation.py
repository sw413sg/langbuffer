"""Bounded local translation using installed language packages; no network."""
from dataclasses import dataclass
import time


@dataclass(frozen=True)
class Translation:
    text: str
    elapsed_ms: float


class LocalTranslation:
    def __init__(self, source, target, threads=2):
        from langbuffer.language_packages import translation_route, package_ready
        self.source, self.target = source, target
        self.steps = []
        for spec in translation_route(source, target, verify=True):
            if not package_ready(spec):
                raise ValueError('translation_package_missing')
            if (spec['source'], spec['target']) == ('en', 'es') and spec.get('engine') == 'opus':
                from langbuffer.translation.opus import OpusTranslator
                self.steps.append(('legacy', OpusTranslator(spec['model_path'], threads=threads)))
                continue
            import ctranslate2
            import sentencepiece
            source_tokenizer = sentencepiece.SentencePieceProcessor(model_file=str(spec['tokenizer_path']))
            target_tokenizer = sentencepiece.SentencePieceProcessor(
                model_file=str(spec.get('target_tokenizer_path') or spec['tokenizer_path']))
            translator = ctranslate2.Translator(str(spec['model_path']), device='cpu', compute_type='int8',
                                                inter_threads=1, intra_threads=threads)
            self.steps.append(('argos', (translator, source_tokenizer, target_tokenizer,
                                         spec.get('target_prefix') or '')))

    def translate(self, text):
        if not text.strip():
            raise ValueError('empty_translation_input')
        started = time.perf_counter()
        value = text.strip()
        for kind, model in self.steps:
            if kind == 'legacy':
                from langbuffer.translation.opus import Request
                value = model.translate(Request(value)).text
                continue
            translator, source_tokenizer, target_tokenizer, prefix = model
            tokens = source_tokenizer.encode(value, out_type=str)
            if len(tokens) > 256:
                raise ValueError('translation_input_too_long')
            result = translator.translate_batch([tokens], beam_size=4, num_hypotheses=1,
                replace_unknowns=True, length_penalty=.2, max_input_length=256,
                max_decoding_length=256, target_prefix=[[prefix]] if prefix else None)[0]
            output = result.hypotheses[0]
            if len(output) >= 256:
                raise RuntimeError('translation_output_truncated')
            value = target_tokenizer.decode(output).replace('▁', ' ').replace('_', ' ').strip()
            if prefix and value.startswith(prefix):
                value = value[len(prefix):].lstrip()
            if not value:
                raise ValueError('empty_translation_output')
        return Translation(value, (time.perf_counter()-started)*1000)
