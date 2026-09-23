"""Offline runtime, copied assets and spawned model-worker integration."""
import hashlib
import importlib
import json
from pathlib import Path
import sys
import time

from langbuffer.pipeline import ROOT, LocalWorker
from langbuffer.language_packages import catalog_packages, package_ready, required_packages


def main():
    import numpy as np
    import ctranslate2
    from langbuffer.local_translation import LocalTranslation

    assert Path(sys.executable).resolve().is_relative_to(ROOT)
    assert Path(sys.base_prefix).resolve().is_relative_to(ROOT)
    assert all(Path(path).resolve().is_relative_to(ROOT) for path in sys.path)
    for name in ('ssl', 'numpy', 'av', 'ctranslate2', 'faster_whisper', 'onnxruntime',
                 'sentencepiece', 'tokenizers', 'yt_dlp', 'PySide6.QtMultimedia'):
        module = importlib.import_module(name)
        assert Path(module.__file__).resolve().is_relative_to(ROOT), name
    inventory = json.loads((ROOT/'outputs/migration-models.json').read_text(encoding='utf-8'))
    for item in inventory['files']:
        path = ROOT/item['path']
        with path.open('rb') as stream:
            assert hashlib.file_digest(stream, 'sha256').hexdigest() == item['sha256'], item['path']
    installed = [spec for spec in catalog_packages() if package_ready(spec)]
    assert len(installed) == 8
    workers = []
    for index, (model, source, target) in enumerate((('small.en', 'en', 'es'),
                                                   ('base.en', 'en', 'pt'),
                                                   ('small', 'es', 'ja'))):
        assert all(package_ready(spec) for spec in required_packages(model, source, target))
        worker = LocalWorker(index+1, model, source, target)
        ready = processed = False
        started = time.monotonic()
        try:
            while time.monotonic()-started < 90 and not processed:
                for event in worker.poll():
                    assert event['kind'] != 'error', event.get('code')
                    if event['kind'] == 'ready':
                        ready = True
                        worker.submit(np.zeros(16000, dtype=np.float32), 0)
                    elif event['kind'] == 'cues':
                        processed = True
                time.sleep(.025)
            assert ready and processed, model
        finally:
            cleanup = worker.close()
            assert cleanup['worker_stopped'] and not cleanup['worker_forced_stop']
        workers.append(dict(model=model, loaded=True, silence_processed=True, cleanup=cleanup))
    # Exercise each installed translator with public fixture text; persist only success.
    pairs = [('en', 'es'), ('en', 'pt'), ('en', 'ja'), ('es', 'en'), ('fr', 'en')]
    texts = {'en': 'This is a test.', 'es': 'Esta es una prueba.', 'fr': 'Ceci est un test.'}
    for source, target in pairs:
        result = LocalTranslation(source, target).translate(texts[source])
        assert result.text.strip()
    report = dict(passed=True, standalone_paths=True, model_files=len(inventory['files']),
                  installed_packages=len(installed), workers=workers, translation_pairs=pairs,
                  remote_media=False, saved_audio=False, saved_transcripts=False)
    (ROOT/'outputs/runtime-check.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))

if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    main()
