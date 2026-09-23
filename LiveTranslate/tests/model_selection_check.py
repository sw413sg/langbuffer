"""Brief UI/model selection check without audio, media requests or inference."""
import hashlib
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from live_translate import integrated
from live_translate import playback
from live_translate.pipeline import ROOT, model_path
from synthetic import SyntheticWorker
from PySide6.QtCore import qInstallMessageHandler
from PySide6.QtWidgets import QApplication
from live_translate.settings import DEFAULTS


def main():
    qInstallMessageHandler(lambda *unused: None)
    app = QApplication([])
    args = SimpleNamespace(url=None, delay=10, seconds=0, stop_after=0, muted=True,
                           auto_exit=False, asr_model=None)
    store = integrated.MemorySettings(dict(DEFAULTS, source='Local · inglés', caption_offset=.4))
    window = integrated.LocalWindow(args, store=store)
    assert window.panel.source.currentData() == 'small.en'
    assert window.panel.source.isEnabled()
    assert window.panel.effective_caption_offset() == .4
    window.controller.set_state('preparing')
    # The current manager permits changing the model during a session.
    assert window.panel.source.isEnabled()
    window.controller.set_state('idle')
    window.panel.source.setCurrentIndex(window.panel.source.findData('base.en'))
    window.close()
    reopened = integrated.LocalWindow(args, store=store)
    assert reopened.panel.source.currentData() == 'base.en'
    reopened.close()
    args.asr_model = 'small.en'
    override = integrated.LocalWindow(args, store=store)
    assert override.panel.source.currentData() == 'small.en'
    calls = []
    with patch.object(override.engine, 'start', side_effect=lambda *args, **kwargs: calls.append(kwargs) or True):
        override.controller.start(['--content-url', 'https://x.com/i/broadcasts/1AxRnZbVpjaxl',
                                  '--delay', '10', '--caption-offset', '.4', '--resolution', '0', '--fps', '0'])
    assert calls[0]['asr_model'] == 'small.en' and calls[0]['caption_offset'] == .4
    # Each new session passes its selected model to the worker.
    for name in ('base.en', 'small.en'):
        with patch.object(playback, 'LocalWorker', SyntheticWorker):
            engine = override.engine
            engine.start('https://x.com/i/broadcasts/1AxRnZbVpjaxl', 10, asr_model=name)
            assert engine.asr_model == name and engine.worker.poll()[0]['model'] == name
            engine.finish('model_selection_check')
            import time
            deadline = time.monotonic()+10
            while engine.running and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.01)
            assert not engine.running and not engine.last_result['cleanup_errors']
    override.close()
    try:
        model_path('../outside')
        raise AssertionError('invalid_model_accepted')
    except ValueError:
        pass
    result = dict(passed=True, default_small_en=True, base_en_selectable=True,
                  selection_remembered=True, cli_override=True, worker_model_parameter=True,
                  selection_available_during_playback=True, offset_preserved=True,
                  audio_played=False, real_asr_tested=False)
    (ROOT/'outputs/local-asr-model-selection-check.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
