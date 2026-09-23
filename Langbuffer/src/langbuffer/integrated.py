"""Langbuffer: local recognition, translation and delayed playback."""
import argparse
import json
import math
import os
from pathlib import Path
import tempfile
from types import MethodType
import sys

os.environ['QT_MEDIA_BACKEND'] = 'ffmpeg'
from langbuffer.pipeline import ROOT, WORK, ASR_MODELS, model_path
from PySide6.QtCore import QObject, Signal, QTimer, QProcess, qInstallMessageHandler
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import QApplication
from langbuffer.integrated_window import IntegratedWindow
from langbuffer.settings import SettingsStore, DEFAULTS, validated
from langbuffer.media_source import content_url, content_kind
from langbuffer.playback import LocalPlayback
from langbuffer.language_packages import LANGUAGES
from langbuffer.language_controls import LanguageControls
from langbuffer.ui_localization import UiLocalization


def local_preferences(data):
    values = validated(data)
    for name, fallback in (('input_language', 'en'), ('output_language', 'en')):
        value = data.get(name) if isinstance(data, dict) else None
        values[name] = value if isinstance(value, str) and value in LANGUAGES else fallback
    # Store model identifiers independently of the translated display labels.
    model = data.get('asr_model') if isinstance(data, dict) else None
    if model not in ('small.en', 'base.en'):
        legacy = data.get('source', '') if isinstance(data, dict) else ''
        model = 'base.en' if isinstance(legacy, str) and legacy.endswith('base.en') else 'small.en'
    values['asr_model'] = model
    return values


class AppSettings(SettingsStore):
    def __init__(self):
        super().__init__(WORK / 'preferences.json')

    def load(self):
        if self.path.exists():
            values, warning = super().load()
            try:
                if self.path.stat().st_size <= 16384:
                    raw = json.loads(self.path.read_text(encoding='utf-8'))
                    extra = local_preferences(raw)
                    values.update({name: extra[name] for name in ('input_language', 'output_language', 'asr_model')})
            except (OSError, ValueError, UnicodeError):
                pass
            return local_preferences(values), warning
        return local_preferences(DEFAULTS), None

    def save(self, values):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                             prefix='preferences-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(local_preferences(values), stream, ensure_ascii=False, indent=2)
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


class MemorySettings:
    def __init__(self, values):
        self.values = local_preferences(values)

    def load(self):
        return dict(self.values), None

    def save(self, values):
        self.values = local_preferences(values)






def validate_local_start(panel):
    try:
        url = content_url(panel.content_url.text())
        if content_kind(url) not in {'broadcast', 'twitch_live', 'kick_live', 'youtube_live', 'facebook'}:
            raise ValueError('Langbuffer supports X broadcasts and Twitch/Kick channels.')
        languages = panel.controller.window.languages
        if languages.busy or not languages.ready():
            raise ValueError('Download the selected language packages before starting.')
        panel.controller.window.listening_device()
        return url
    except ValueError as exc:
        panel.on_error(str(exc))
        return None


class LocalController(QObject):
    """Launcher protocol without QProcess, Live Captions or audio routing."""
    changed = Signal(str)
    completed = Signal(dict)
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.state = 'idle'
        self.window = None
        self.stop_requested = False

    @property
    def running(self):
        return self.window is not None and self.window.engine.running

    def set_state(self, value):
        self.state = value
        self.changed.emit(value)

    def start(self, arguments):
        if self.running:
            return False
        def option(flag):
            return arguments[arguments.index(flag)+1]
        self.stop_requested = False
        try:
            url = option('--content-url')
            quality = (int(option('--resolution')), int(option('--fps')))
            if self.window.panel.resolution.catalog_url != url:
                quality = (0, 0)
            return self.window.engine.start(url, int(option('--delay')),
                device=self.window.listening_device(), caption_offset=float(option('--caption-offset')),
                quality=quality, asr_model=self.window.languages.model_name(),
                source_language=self.window.languages.source_language,
                target_language=self.window.languages.target_language)
        except (ValueError, IndexError, RuntimeError) as exc:
            self.set_state('idle')
            self.failed.emit(str(exc))
            return False

    def stop(self):
        self.stop_requested = True
        if self.running:
            self.window.engine.finish()

    def pause(self):
        pending = getattr(self.window.engine, 'pending', None)
        if self.state == 'playing' or (pending and pending['kind'] == 'advertisement'):
            self.window.engine.toggle_pause()

    def resume(self):
        if self.state == 'paused':
            self.window.engine.toggle_pause()

    def show_captions(self):
        self.window.caption_overlay.reveal()

    def set_caption_offset(self, value):
        self.window.engine.set_caption_offset(value)

    def finished(self, result):
        self.set_state('idle')
        errors = result['errors'] + result['cleanup_errors']
        if errors:
            self.failed.emit('\n'.join(errors))
        else:
            self.completed.emit(result)


class LocalWindow(IntegratedWindow):
    def __init__(self, args, store=None):
        controller = LocalController()
        super().__init__(controller=controller, discover=False,
                         store=store if store is not None else AppSettings())
        controller.setParent(self)
        self.setWindowTitle('Langbuffer')
        self.engine = LocalPlayback(self.picture, self.caption_overlay, self,
                                    seconds=args.seconds, stop_after=args.stop_after)
        controller.window = self
        selector = self.panel.resolution
        selector.catalog_url = None
        self.panel.content_url.textChanged.connect(self.source_edited)
        self.panel.caption_offset.setToolTip(
            'Negative moves subtitles earlier; positive moves them later. 0.0 s keeps the original timing. '
            'Changes apply to upcoming pages; the current page stays fixed.')
        self.panel.session_form.labelForField(self.panel.source).setText('Recognition')
        for name in ('small.en', 'base.en'):
            self.panel.source.addItem(f'Local · English · {name}', name)
        requested_model = getattr(args, 'asr_model', None)
        remembered = self.panel.source.findData(self.panel.preferences['asr_model'])
        selected = self.panel.source.findData(requested_model) if requested_model else remembered
        self.panel.source.setCurrentIndex(max(0, selected))
        self.panel.listener.addItem('Windows default', None)
        desired = self.panel.preferences['listener']
        for device in QMediaDevices.audioOutputs():
            self.panel.listener.addItem(device.description(), bytes(device.id()))
        found = self.panel.listener.findText(desired)
        self.panel.listener.setCurrentIndex(max(0, found))
        self.panel.content_url.setText(args.url or '')
        if args.delay is not None:
            self.panel.delay.setValue(args.delay)
        if args.muted:
            self._muted = True
            self.update_volume_icon()
        self.engine.muted = self._muted
        self.volume_changed.connect(self.engine.set_volume)
        self.mute_changed.connect(self.engine.set_muted)
        self.engine.changed.connect(controller.set_state)
        self.engine.countdown.connect(self.show_wait)
        self.engine.recovering.connect(self.show_recovery)
        self.engine.advertisement.connect(self.show_advertisement)
        self.engine.quality_changed.connect(self.resolution_changed)
        self.engine.history_changed.connect(self.update_history)
        self.playback_timeline.seek_requested.connect(self.engine.request_seek)
        self.engine.completed.connect(controller.finished)
        self.languages = LanguageControls(self,
            source=getattr(args, 'input_language', None) or self.panel.preferences['input_language'],
            target=getattr(args, 'output_language', None) or self.panel.preferences['output_language'],
            asr_model=requested_model or self.panel.source.currentData())
        self.languages.changed.connect(self.remember_languages)
        self.languages.idle.connect(self.download_finished)
        self.panel.preferences.update(self.languages.values())
        self.ui_language = UiLocalization(self, self.languages.target_language)
        self.languages.retranslate()
        self._closing_download = False
        if args.auto_exit:
            self.engine.completed.connect(lambda result: QTimer.singleShot(0, self.close))
        self.session_state('idle')

    def remember_languages(self):
        if hasattr(self, 'ui_language'):
            self.ui_language.set_language(self.languages.target_language)
            self.languages.retranslate()
        self.panel.preferences.update(self.languages.values())
        self.panel.save_preferences()
        if (self.engine.running and not self.languages.busy and self.languages.ready()
                and self.engine.request_languages(self.languages.source_language,
                    self.languages.target_language, self.languages.model_name())):
            self.languages.dialog.hide()

    def download_finished(self):
        if self._closing_download:
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event):
        if hasattr(self, 'languages') and self.languages.busy:
            self._closing_download = True
            self.languages.cancel()
            event.ignore()
            return
        if hasattr(self, 'languages'):
            self.panel.preferences.update(self.languages.values())
        super().closeEvent(event)

    def listening_device(self):
        selected = self.panel.listener.currentData()
        if selected is None:
            device = QMediaDevices.defaultAudioOutput()
        else:
            device = next((value for value in QMediaDevices.audioOutputs()
                           if bytes(value.id()) == selected), None)
        if device is None or device.isNull():
            raise ValueError('Select an available audio output.')
        return device

    def session_state(self, state):
        super().session_state(state)
        self.panel.source.setEnabled(state == 'idle')
        self.panel.resolution.setEnabled(state == 'idle' or state in {'playing', 'paused'}
                                         and hasattr(self, 'engine') and not self.engine.finishing)
        self.resolution_action.setEnabled(self.panel.resolution.isEnabled())
        waiting_ad = (hasattr(self, 'engine') and getattr(self.engine, 'pending', None)
                      and self.engine.pending['kind'] == 'advertisement')
        self.pause_button.setEnabled(state in {'playing', 'paused'} or bool(waiting_ad))
        self.playback_timeline.set_busy(not self.seek_available())
        if state == 'playing':
            self.show_wait(visible=False)
        elif state == 'paused':
            self.show_wait(paused=True)
        elif state == 'preparing':
            self.show_wait()
        if state == 'idle' and hasattr(self.panel.resolution, 'catalog_url'):
            selector = self.panel.resolution
            if selector.url != selector.catalog_url:
                selector.catalog_url = selector.url
                selector.set_formats([], 0)
                if selector.url:
                    selector.timer.start()
        if hasattr(self, 'languages'):
            self.languages.refresh()

    def source_edited(self, text):
        if self.engine.running:
            # The editable field is for Reiniciar; quality still belongs to the active video.
            details = self.engine.source_details()
            self.panel.resolution.catalog_url = self.engine.url
            self.panel.resolution.set_formats(details.get('qualities') or [], self.engine.quality,
                                              self.engine.metrics.get('actual_quality'))
        elif self.panel.resolution.url:
            self.panel.resolution.catalog_url = self.panel.resolution.url
            self.panel.resolution.timer.start()

    def change_resolution(self, quality):
        if hasattr(self, 'engine') and self.engine.request_quality(quality):
            self.panel.resolution.set_pending(quality)

    def update_history(self, snapshot, position):
        self.set_timeline_active(True)
        self.playback_timeline.update_window(snapshot, position, self.engine.delay_s)
        self.playback_timeline.set_busy(not self.seek_available())

    def seek_available(self):
        engine = getattr(self, 'engine', None)
        return bool(engine is not None and engine.running
                    and (not engine.finishing or engine.pending is not None)
                    and (engine.pending is None or engine.pending['kind'] not in {'recovery', 'advertisement'})
                    and (engine.direct_file and engine.bridge is not None
                         or engine.platform in {'x', 'kick'} and engine.playlist is not None
                         and engine.playlist.snapshot is not None))

    def show_recovery(self):
        self.show_wait()
        self.wait_label.setText('Reconnecting to Twitch…')
        self.layout_wait_label()

    def show_advertisement(self, status):
        self.show_wait()
        remaining = status.get('remaining_s')
        if status.get('paused'):
            text = 'Twitch ad break · paused. Press Resume to continue.'
        elif remaining is not None and remaining > 0:
            text = self.ui_language.text('Twitch ad break · about {seconds} s remaining',
                                         seconds=math.ceil(remaining))
        else:
            text = 'Twitch ad break · waiting for the stream to return…'
        self.wait_label.setText(text)
        self.layout_wait_label()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url')
    parser.add_argument('--asr-model', choices=ASR_MODELS,
                        help='Local model; otherwise reuse the saved choice or start with small.en.')
    parser.add_argument('--input-language', choices=tuple(LANGUAGES))
    parser.add_argument('--output-language', choices=tuple(LANGUAGES))
    parser.add_argument('--seconds', type=int, choices=(0, *range(12, 61)), default=0)
    parser.add_argument('--delay', type=int, choices=range(1, 31))
    parser.add_argument('--muted', action='store_true')
    parser.add_argument('--auto-start', action='store_true')
    parser.add_argument('--auto-exit', action='store_true')
    parser.add_argument('--stop-after', type=int, choices=range(1, 61), default=0)
    parser.add_argument('--ui-check', action='store_true')
    args = parser.parse_args()
    if args.auto_start and not args.url:
        parser.error('--auto-start requires --url')
    qInstallMessageHandler(lambda *unused: None)
    (ROOT/'outputs').mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    from langbuffer.instance_lock import acquire_instance
    legacy_lock = acquire_instance(WORK)
    if legacy_lock is None:
        from PySide6.QtWidgets import QMessageBox
        language = AppSettings().load()[0].get('output_language', 'en')
        message = {
            'en': 'Close the older app window once before opening multiple Langbuffer windows. If none is open, check data folder permissions.',
            'es': 'Cierra una vez la ventana anterior de la app antes de abrir varias ventanas de Langbuffer. Si no hay ninguna abierta, revisa los permisos de la carpeta de datos.',
            'pt': 'Feche uma vez a janela anterior do aplicativo antes de abrir várias janelas do Langbuffer. Se nenhuma estiver aberta, verifique as permissões da pasta de dados.',
            'fr': 'Fermez une fois l’ancienne fenêtre de l’application avant d’ouvrir plusieurs fenêtres de Langbuffer. Si aucune n’est ouverte, vérifiez les droits du dossier de données.',
        }
        QMessageBox.information(None, 'Langbuffer', message.get(language, message['en']))
        return 1
    legacy_lock.unlock()
    store = MemorySettings(AppSettings().load()[0]) if args.auto_exit or args.ui_check else None
    window = LocalWindow(args, store=store)
    app.setWindowIcon(window.windowIcon())
    window.show()
    if args.ui_check:
        def capture():
            window.grab().save(str(ROOT / 'outputs/local-asr-integrated-settings.png'))
            window.open_section('subtitles')
            app.processEvents()
            window.grab().save(str(ROOT / 'outputs/local-asr-integrated-subtitles.png'))
            window.close()
        QTimer.singleShot(250, capture)
    elif args.auto_start:
        def start():
            window.panel.start_session()
            if args.auto_exit and not window.engine.running:
                window.close()
        QTimer.singleShot(0, start)
    app.exec()
    if args.auto_exit:
        result = window.engine.last_result or dict(passed=False, errors=['not_started'])
        print(json.dumps(result, indent=2))
        return 0 if result['passed'] else 1
    return 0


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
