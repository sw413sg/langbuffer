"""OPUS-MT local; no descarga modelos ni envía texto por red."""
from dataclasses import dataclass
from pathlib import Path
import time


@dataclass(frozen=True)
class Request:
    text: str
    source: str = 'en'
    target: str = 'es'
    session: str = 'default'
    segment: str = '0'
    revision: int = 0
    context: str | None = None


@dataclass(frozen=True)
class Result:
    request: Request
    text: str
    elapsed_ms: float
    engine: str = 'opus-eng-spa-2021-02-19-int8'


class OpusTranslator:
    def __init__(self, model_dir, beam_size=4, threads=4):
        import ctranslate2
        import sentencepiece
        root = Path(model_dir)
        self.source = sentencepiece.SentencePieceProcessor(model_file=str(root/'source.spm'))
        self.target = sentencepiece.SentencePieceProcessor(model_file=str(root/'target.spm'))
        self.model = ctranslate2.Translator(str(root), device='cpu', compute_type='int8',
                                           inter_threads=1, intra_threads=threads)
        self.beam_size = beam_size

    def translate(self, request):
        if (request.source, request.target) != ('en', 'es'):
            raise ValueError('Este modelo solo admite inglés a español.')
        if request.context:
            raise ValueError('Este motor aún no admite contexto separado; enviar una unidad completa.')
        if not request.text.strip():
            raise ValueError('empty_translation_input')
        started = time.perf_counter()
        tokens = self.source.encode(request.text.strip(), out_type=str)
        if len(tokens) > 256:
            raise ValueError('translation_input_too_long')
        result = self.model.translate_batch([tokens], beam_size=self.beam_size,
                    max_input_length=256, max_decoding_length=256)[0]
        output = result.hypotheses[0]
        if len(output) >= 256:
            raise RuntimeError('translation_output_truncated')
        return Result(request, self.target.decode(output), (time.perf_counter()-started)*1000)
