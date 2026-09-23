"""Locale switching and manager integration, without media or package mutations."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QPoint, qInstallMessageHandler
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QComboBox, QFormLayout, QSlider

from live_translate import integrated
from live_translate.pipeline import ROOT


def main():
    qInstallMessageHandler(lambda *unused: None)
    app = QApplication.instance() or QApplication([])
    for name in ('arial.ttf', 'segoeui.ttf', 'verdana.ttf'):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + name)
    app.setFont(QFont('Segoe UI', 10))
    args = SimpleNamespace(url=None, delay=None, seconds=0, stop_after=0,
                           muted=True, auto_exit=False, asr_model=None)
    fresh = integrated.local_preferences({})
    assert fresh['input_language'] == fresh['output_language'] == 'en'
    assert integrated.local_preferences(dict(output_language='invalid'))['output_language'] == 'en'
    assert integrated.local_preferences(dict(source='Local · inglés · base.en'))['asr_model'] == 'base.en'
    store = integrated.MemorySettings(dict(caption_offset=.4))
    window = integrated.LocalWindow(args, store=store)
    panel, languages = window.panel, window.languages
    window.show()
    app.processEvents()
    assert panel.start.text() == 'Start'
    assert window.configuration_action.text() == 'Settings'
    assert panel.effective_caption_offset() == .4
    fields = panel.session_form
    assert fields.rowCount() == 4
    assert fields.itemAt(0, QFormLayout.ItemRole.FieldRole).widget() is panel.content_url
    assert fields.itemAt(1, QFormLayout.ItemRole.FieldRole).widget() is panel.listener
    assert fields.itemAt(2, QFormLayout.ItemRole.FieldRole).widget().findChild(QSlider) is panel.delay
    assert fields.itemAt(3, QFormLayout.ItemRole.SpanningRole).widget() is languages.button
    assert [button.objectName() for button in (languages.button, panel.start, panel.restart, panel.stop)] == [
        'languageManagerButton', 'startSession', 'restartSession', 'stopSession']
    settings_height = window.drawer.height()
    assert settings_height <= 320
    language_bottom = languages.button.mapTo(window, QPoint(0, languages.button.height())).y()
    actions_top = panel.start.mapTo(window, QPoint()).y()
    action_gap = panel.restart.mapTo(window, QPoint()).x() - (
        panel.start.mapTo(window, QPoint()).x()+panel.start.width())
    assert abs((actions_top-language_bottom)-action_gap) <= 2
    assert not panel.stop.isEnabled()
    window.controller.set_state('preparing')
    assert panel.stop.isEnabled()
    panel.stop.click()
    assert window.controller.stop_requested
    window.controller.set_state('idle')
    assert languages.input.count() == languages.output.count() == 9
    assert languages.dialog.isAncestorOf(languages.input)
    assert languages.dialog.isAncestorOf(languages.output)
    assert languages.dialog.isAncestorOf(panel.source)
    assert languages.input.isHidden() and languages.output.isHidden() and panel.source.isHidden()
    assert tuple(languages.sections) == ('input', 'output', 'recognition')
    assert not languages.sections['input'].header.isChecked()
    assert not languages.sections['output'].header.isChecked()
    assert not languages.sections['recognition'].header.isChecked()
    assert languages.sections['input'].layout.columnCount() == 3
    assert languages.sections['output'].layout.columnCount() == 3
    assert languages.sections['recognition'].layout.columnCount() == 3
    settings_page = panel.pages.widget(0)
    assert languages.input not in settings_page.findChildren(QComboBox)
    assert languages.output not in settings_page.findChildren(QComboBox)
    assert panel.source not in settings_page.findChildren(QComboBox)
    languages.button.click()
    app.processEvents()
    assert languages.dialog.isVisible()
    assert languages.sections['recognition'].header.isVisible()
    assert not languages.sections['input'].body.isVisible()
    assert not languages.sections['output'].body.isVisible()
    assert not languages.sections['recognition'].body.isVisible()
    collapsed_height = languages.dialog.height()
    languages.sections['input'].header.click()
    languages.sections['input'].rows['en'].use_button.click()
    app.processEvents()
    assert languages.dialog.height() > collapsed_height
    assert languages.sections['input'].rows['en'].details.isVisible()
    assert not languages.sections['input'].rows['es'].details.isVisible()
    assert languages.source_language == 'en'
    languages.sections['output'].header.click()
    assert languages.sections['output'].header.isChecked()
    assert not languages.sections['input'].header.isChecked()
    languages.sections['output'].rows['en'].use_button.click()
    assert languages.sections['output'].rows['en'].details.isVisible()
    for row in languages.sections['output'].rows.values():
        if row.extra and not row.isHidden():
            row.use_button.click()
            assert row.details.isVisible()
            assert not row.use_button.isChecked()
            assert languages.target_language == 'en'
    languages.sections['recognition'].rows['small'].use_button.click()
    assert languages.sections['recognition'].rows['small'].details.isVisible()
    assert languages.sections['recognition'].rows['small'].recommendation_label.isVisible()
    assert all(row.recommendation_label.text() and row.recommendation_label.isVisible()
               for row in languages.sections['recognition'].rows.values())
    assert languages.sections['recognition'].header.isChecked()
    assert not languages.sections['output'].header.isChecked()
    assert not languages.sections['recognition'].rows['small'].use_button.isChecked()
    assert languages.model_name() == 'small.en'
    languages.dialog.hide()

    expected = {'en': ('Start', 'Settings', 'Buffering and translating now... 5 s'),
                'es': ('Iniciar', 'Configuración', 'Almacenando en buffer y traduciendo ahora... 5 s'),
                'pt': ('Iniciar', 'Configurações', 'Armazenando em buffer e traduzindo agora... 5 s'),
                'fr': ('Démarrer', 'Paramètres', 'Mise en mémoire tampon et traduction en cours... 5 s')}
    captions = window.caption_overlay
    captions.set_captions('Configuración', 'Iniciar')
    choices = {name: getattr(panel, name).currentData() for name in
               ('font_size', 'font_family', 'text_color', 'original_text_color', 'text_alignment')}
    render = os.environ.get('LANGUAGE_UI_RENDER') == '1'
    for locale, (start, settings, wait_text) in expected.items():
        languages.sections['output'].rows[locale].use_button.click()
        app.processEvents()
        assert panel.start.text() == start, (locale, panel.start.text())
        assert window.configuration_action.text() == settings
        assert languages.sections['output'].rows[locale].use_button.isChecked()
        assert sum(row.use_button.isChecked() for row in languages.sections['output'].rows.values()) == 1
        assert captions.translation.text() == 'Configuración'
        assert captions.original_text == 'Iniciar'
        assert all(getattr(panel, name).currentData() == value for name, value in choices.items())
        panel.resolution.set_formats([dict(height=720, fps=60)], (720, 60), (720, 60))
        window.update_resolution_action(panel.resolution.label())
        assert window.resolution_menu.actions()[0].text() != 'Máxima disponible' or locale == 'es'
        window.show_wait(remaining=5)
        assert window.wait_label.text() == wait_text
        window.set_paused(True)
        assert locale == 'es' or window.pause_button.toolTip() != 'Reanudar'
        panel.on_error('source_kick_offline')
        assert locale == 'es' or 'Ese canal de Kick' not in panel.status.text()
        panel.set_status('')
        window.show_wait(visible=False)
        window.set_paused(False)
        window.open_section('subtitles')
        app.processEvents()
        assert window.drawer.height() > settings_height
        assert captions.preview
        if render:
            window.grab().save(str(ROOT / f'outputs/language-subtitles-{locale}.png'))
        window.open_section('settings')
        if render:
            captions.set_captions('', '')
            window.grab().save(str(ROOT / f'outputs/language-settings-{locale}.png'))
            languages.button.click()
            app.processEvents()
            for role in languages.sections:
                languages.sections[role].set_expanded(True)
                app.processEvents()
                languages.dialog.grab().save(str(ROOT / f'outputs/language-manager-sections-{locale}-{role}.png'))
            languages.dialog.hide()
            captions.set_captions('Configuración', 'Iniciar')
    # Unsupported UI languages use English while retaining the actual subtitle target.
    languages.sections['output'].rows['ja'].use_button.click()
    assert languages.target_language == 'ja' and panel.start.text() == 'Start'
    languages.sections['recognition'].rows['base.en'].use_button.click()
    languages.sections['input'].rows['es'].use_button.click()
    assert languages.model_name() == 'small'
    assert store.values['asr_model'] == 'base.en'
    languages.sections['output'].rows['fr'].use_button.click()
    window.close()
    reopened = integrated.LocalWindow(args, store=store)
    assert reopened.panel.start.text() == 'Démarrer'
    assert reopened.languages.source_language == 'es'
    assert reopened.panel.caption_offset.value() == 4
    reopened.languages.sections['input'].rows['en'].use_button.click()
    assert reopened.panel.source.currentData() == 'base.en'
    reopened.close()

    # A real save/load roundtrip uses a disposable location, never user preferences.
    with tempfile.TemporaryDirectory(prefix='language-prefs-', dir=ROOT/'data') as directory:
        file_store = integrated.AppSettings()
        file_store.path = Path(directory)/'preferences.json'
        file_store.save(store.values)
        loaded, warning = file_store.load()
        assert warning is None and loaded['output_language'] == 'fr'
        assert loaded['asr_model'] == 'base.en' and loaded['caption_offset'] == .4
    result = dict(passed=True, locales=list(expected), fallback='en', default='en',
                  languages=9, manager_only_selection=True, dynamic_strings=True,
                  preferences_roundtrip=True, stable_model_identifier=True,
                  subtitle_content_preserved=True, sections=list(languages.sections), direct_selection=True,
                  remote_media=False, package_mutations=False)
    (ROOT/'outputs/local-asr-language-ui-check.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
