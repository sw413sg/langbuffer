"""Language manager and offline package readiness."""
import threading

from PySide6.QtCore import QObject, QThread, Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel,
                               QProgressBar, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from langbuffer.language_packages import (LANGUAGES, catalog_packages, effective_asr_model,
                               install_packages, package_present, package_ready,
                               remove_package, required_packages, translation_route)
from langbuffer.language_sections import PackageSection


LANGUAGE_NAMES = dict(en='English', es='Spanish', pt='Portuguese', fr='French',
                      de='German', it='Italian', ja='Japanese', ko='Korean',
                      zh='Simplified Chinese')


def byte_label(value):
    value = max(0, int(value or 0))
    if value >= 1_000_000_000:
        return f'{value / 1_000_000_000:.2f} GB'
    if value >= 1_000_000:
        return f'{value / 1_000_000:.1f} MB'
    return f'{value / 1_000:.0f} KB'


class PackageInstaller(QThread):
    """Keep disk/network work outside the GUI and retain ownership until finished."""
    progress = Signal(dict)

    def __init__(self, specs, parent=None, operation='download'):
        super().__init__(parent)
        self.specs = list(specs)
        self.operation = operation
        self.cancel_event = threading.Event()
        self.result = None

    def run(self):
        try:
            if self.operation == 'remove':
                if self.cancel_event.is_set():
                    self.result = 'cancelled'
                    return
                # Removal is atomic from the GUI's perspective and is not
                # interrupted once the backend starts changing this package.
                for spec in self.specs:
                    remove_package(spec)
                self.result = 'removed'
            else:
                install_packages(self.specs, self.cancel_event, self.progress.emit)
                self.result = 'cancelled' if self.cancel_event.is_set() else 'ready'
        except RuntimeError as error:
            self.result = ('cancelled' if self.cancel_event.is_set() else
                           str(error) if str(error) in {'language_package_in_use', 'language_packages_busy'}
                           else 'error')
        except Exception:
            # Exceptions can contain signed download URLs and local paths.
            self.result = 'cancelled' if self.cancel_event.is_set() else 'error'


class LanguageDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner.window)
        self.owner = owner
        self.pending_close = False
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumSize(650, 260)
        self.resize(860, 560)
        drawer = getattr(owner.window, 'drawer', None)
        style = drawer.styleSheet() if drawer else 'QWidget {color:#f1f4f8; font:12px "Segoe UI";}'
        self.setStyleSheet(style + '''
            QDialog {background:#141414;}
            QScrollArea, QScrollArea > QWidget > QWidget {background:#141414; border:none;}
            QPushButton#languageSection {background:#252e3c; color:#e7f1ff;
                border:1px solid #46566c; border-radius:7px; padding:13px;
                text-align:left; font-size:15px; font-weight:600;}
            QPushButton#languageSection:checked {border-color:#8eadd2;}
            QFrame#packageCard {background:#202020; border:1px solid #333840; border-radius:7px;}
            QFrame#packageCard[activePackage="true"] {background:#203d2d; border-color:#4caa72;}
            QFrame#packageCard[expandedPackage="true"] {border-color:#8eadd2;}
            QFrame#packageCard[activePackage="true"][expandedPackage="true"] {border-color:#65c78b;}
            QLabel#packageStatus {color:#aeb8c7;}
            QLabel#modelRecommendation {color:#d5deea;}
            QPushButton#languageChoice {min-height:37px; text-align:left; padding:4px 8px;
                font-size:14px; font-weight:600;}
            QPushButton#languageChoice:checked {background:#59bd7c; color:#10251a;
                border-color:#59bd7c;}
        ''')

    def reject(self):
        if self.owner.busy:
            self.pending_close = True
            self.owner.cancel()
            return
        super().reject()

    def closeEvent(self, event):
        if self.owner.busy:
            self.pending_close = True
            self.owner.cancel()
            event.ignore()
            return
        super().closeEvent(event)


class LanguageControls(QObject):
    changed = Signal()
    idle = Signal()

    def __init__(self, window, source='en', target='en', asr_model=None):
        super().__init__(window)
        self.window = window
        self.panel = window.panel
        self._installer = None
        self._notice = ''
        self._notice_values = {}
        self._remove_pending = None
        self._english_model = 'small.en'
        remembered = asr_model or self.panel.source.currentData()
        if remembered in {'small.en', 'base.en'}:
            self._english_model = remembered

        self.dialog = LanguageDialog(self)
        self._fit_pending = False
        self._static = []
        outer = QVBoxLayout(self.dialog)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)
        # Keep stable selection adapters for the controller and preferences.
        # The visible cards are the only way to select in the manager.
        self.input = QComboBox(self.dialog)
        self.output = QComboBox(self.dialog)
        for code in LANGUAGES:
            self.input.addItem(LANGUAGE_NAMES[code], code)
            self.output.addItem(LANGUAGE_NAMES[code], code)
        self.input.setCurrentIndex(max(0, self.input.findData(source)))
        self.output.setCurrentIndex(max(0, self.output.findData(target)))
        self.input.hide()
        self.output.hide()

        # takeRow keeps the source combo alive: panel/settings still own the
        # same reference, but the only editable language controls live here.
        source_row = self.panel.session_form.takeRow(self.panel.source)
        if source_row.labelItem and source_row.labelItem.widget():
            source_row.labelItem.widget().hide()
            source_row.labelItem.widget().deleteLater()
        self.panel.source.setParent(self.dialog)
        self.panel.source.hide()
        self.sections = {}
        for role, title in (('input', 'Original language'),
                            ('output', 'Translate to'),
                            ('recognition', 'Translation engine')):
            section = PackageSection(self, role, title)
            self.sections[role] = section
        self._catalog = list(catalog_packages())
        for code in LANGUAGES:
            self.sections['input'].add(code, next(iter(translation_route(code, 'en')), None))
            self.sections['output'].add(code, next(iter(translation_route('en', code)), None))
        for spec in self._catalog:
            if spec['kind'] == 'asr':
                self.sections['recognition'].add(spec['model'], spec)
        # Expose additional installed translators without replacing the effective
        # package chosen by the backend (e.g. owned Argos alongside shared OPUS).
        primary_ids = {row.spec['id'] for section in self.sections.values()
                       for row in section.rows.values() if row.spec is not None}
        for spec in self._catalog:
            if spec['kind'] == 'translation' and (spec['id'] not in primary_ids or
                    (spec['source'], spec['target']) == ('en', 'es') and not spec.get('readonly')):
                role = 'input' if spec['target'] == 'en' else 'output'
                self.sections[role].add('extra:'+spec['id'], spec, extra=True)
        self.section_scroll = QScrollArea(self.dialog)
        self.section_scroll.setWidgetResizable(True)
        self.section_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        section_body = QWidget(self.section_scroll)
        section_layout = QVBoxLayout(section_body)
        section_layout.setContentsMargins(0, 0, 8, 0)
        section_layout.setSpacing(12)
        section_layout.addWidget(self.sections['input'].widget)
        section_layout.addWidget(self.sections['output'].widget)
        section_layout.addStretch(1)
        self.section_scroll.setWidget(section_body)
        outer.addWidget(self.section_scroll, 1)
        outer.addWidget(self.sections['recognition'].widget)

        self.status_label = QLabel(self.dialog)
        self.status_label.setWordWrap(True)
        outer.addWidget(self.status_label)
        self.progress = QProgressBar(self.dialog)
        self.progress.setRange(0, 1000)
        self.progress.setStyleSheet(
            'QProgressBar {border:1px solid #465264; border-radius:4px; '
            'background:#202733; text-align:center; min-height:18px;}'
            'QProgressBar::chunk {background:#3a516e; border-radius:3px;}')
        outer.addWidget(self.progress)
        package_actions = QHBoxLayout()
        self.download_button = QPushButton(self.dialog)
        self.cancel_button = QPushButton(self.dialog)
        self._static.append((self.cancel_button, 'Cancel'))
        package_actions.addWidget(self.download_button, 1)
        package_actions.addWidget(self.cancel_button)
        outer.addLayout(package_actions)
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        self.close_button = QPushButton(self.dialog)
        self._static.append((self.close_button, 'Done'))
        close_row.addWidget(self.close_button)
        outer.addLayout(close_row)

        self.language_button = QPushButton(self.panel)
        self.language_button.setObjectName('languageManagerButton')
        self.summary_label = QLabel(self.panel)
        self.button, self.summary = self.language_button, self.summary_label
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.summary_label.hide()
        self.panel.session_form.addRow(self.language_button)
        # Retained for UI integrations that referenced the old container.
        self.packages = self.dialog

        self.input.currentIndexChanged.connect(self._source_changed)
        self.output.currentIndexChanged.connect(self._selection_changed)
        self.panel.source.currentIndexChanged.connect(self._model_changed)
        self.download_button.clicked.connect(self.download)
        self.cancel_button.clicked.connect(self.cancel)
        self.close_button.clicked.connect(self.dialog.reject)
        self.language_button.clicked.connect(self.show_manager)
        self.panel.controller.changed.connect(self.refresh)
        self.panel.controller.completed.connect(self.refresh)
        self.panel.controller.failed.connect(self.refresh)
        self._sync_models()
        self.retranslate()

    def text(self, key, **values):
        localizer = getattr(self.window, 'ui_language', None)
        return localizer.text(key, **values) if localizer else key.format(**values)

    def label(self, key):
        widget = QLabel(self.dialog)
        widget.setWordWrap(True)
        self._static.append((widget, key))
        return widget

    def language_name(self, code):
        return self.text(LANGUAGE_NAMES.get(code, code))

    def package_size(self, spec):
        return byte_label(spec.get('download_bytes', 0))

    def package_label(self, spec):
        if spec.get('kind') == 'asr':
            key = 'Recognition {model} · multilingual' if spec.get('model') == 'small' else 'Recognition {model}'
            return self.text(key, model=spec.get('model', spec['id']))
        return self.text('Translation {source} → {target}',
                         source=self.language_name(spec.get('source', 'en')),
                         target=self.language_name(spec.get('target', 'en')))

    @property
    def source_language(self):
        return self.input.currentData()

    @property
    def target_language(self):
        return self.output.currentData()

    @property
    def busy(self):
        # Only QThread.finished releases the start/close gate.
        return self._installer is not None

    def model_name(self):
        return effective_asr_model(self.panel.source.currentData(), self.source_language)

    def values(self):
        return dict(input_language=self.source_language, output_language=self.target_language,
                    asr_model=self._english_model)

    def specs(self):
        return required_packages(self.model_name(), self.source_language, self.target_language)

    def missing(self):
        return [spec for spec in self.specs() if not package_ready(spec)]

    def ready(self):
        return not self.missing()

    def _available(self):
        controller = self.panel.controller
        return (controller.state != 'stopping' and not self.busy
                and not self.panel._restart_pending)

    def _sync_models(self):
        combo = self.panel.source
        old_block = combo.blockSignals(True)
        try:
            combo.clear()
            if self.source_language == 'en':
                for name in ('small.en', 'base.en'):
                    combo.addItem(self.text('Local · English · {model}', model=name), name)
                combo.setCurrentIndex(max(0, combo.findData(self._english_model)))
                combo.setToolTip(self.text('Local recognition model for the input language.'))
            else:
                combo.addItem(self.text('Local · multilingual · small'), 'small')
                combo.setToolTip(self.text('One small package recognizes all non-English input languages in this list.'))
        finally:
            combo.blockSignals(old_block)

    def _source_changed(self, *unused):
        previous = self.panel.source.currentData()
        if previous in {'small.en', 'base.en'}:
            self._english_model = previous
        self._sync_models()
        self._selection_changed()

    def _model_changed(self, *unused):
        if self.panel.source.currentData() in {'small.en', 'base.en'}:
            self._english_model = self.panel.source.currentData()
        self._selection_changed()

    def _selection_changed(self, *unused):
        self._notice = ''
        self._remove_pending = None
        self.refresh()
        self.changed.emit()

    def retranslate(self):
        self.dialog.setWindowTitle(self.text('Language manager'))
        self.language_button.setText(self.text('Language'))
        self.language_button.setAccessibleName(self.text('Open language manager'))
        self.input.setAccessibleName(self.text('Input language'))
        self.output.setAccessibleName(self.text('Output language'))
        self.progress.setAccessibleName(self.text('Language package progress'))
        for widget, key in self._static:
            widget.setText(self.text(key))
        for combo in (self.input, self.output):
            old_block = combo.blockSignals(True)
            for index in range(combo.count()):
                combo.setItemText(index, self.language_name(combo.itemData(index)))
            combo.blockSignals(old_block)
        self._sync_models()
        self.refresh()

    def schedule_dialog_fit(self):
        if self._fit_pending:
            return
        self._fit_pending = True
        QTimer.singleShot(0, self.fit_dialog)

    def fit_dialog(self):
        self._fit_pending = False
        if not self.dialog.isVisible():
            return
        screen = self.dialog.screen() or self.window.screen()
        usable_height = max(300, screen.availableGeometry().height()-96)
        self.section_scroll.widget().layout().activate()
        natural_scroll = self.section_scroll.widget().sizeHint().height()+2
        footer_height = self.sections['recognition'].widget.sizeHint().height()+140
        self.section_scroll.setFixedHeight(min(natural_scroll, max(120, usable_height-footer_height)))
        self.dialog.layout().activate()
        self.dialog.resize(self.dialog.width(),
                           max(260, min(usable_height, self.dialog.sizeHint().height())))

    def show_manager(self):
        self.dialog.pending_close = False
        for section in self.sections.values():
            section.set_expanded(False)
        self.section_scroll.verticalScrollBar().setValue(0)
        self.refresh()
        self.dialog.show()
        self.schedule_dialog_fit()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def refresh(self, *unused):
        available = self._available()
        for widget in (self.input, self.output, self.panel.source):
            widget.setEnabled(available)
        missing = self.missing()
        ready = not missing
        pending_restart = self.panel._restart_pending
        self.panel.start.setEnabled(available and self.panel.controller.state == 'idle' and ready)
        self.panel.restart.setEnabled(ready and not self.busy and not pending_restart)
        start_hint = '' if ready else self.text('Download the selected language packages before starting.')
        if self.busy:
            start_hint = self.text('Wait until package preparation finishes.')
        self.panel.start.setToolTip(start_hint)
        self.panel.restart.setToolTip(start_hint)
        self.panel.show_original.setText(self.text('Also show the original in {language}',
                                                   language=self.language_name(self.source_language)))
        self.summary_label.setText(self.text('Input: {source}\nOutput: {target}',
            source=self.language_name(self.source_language), target=self.language_name(self.target_language)))
        self.progress.setVisible(self.busy)
        downloading = self.busy and self._installer.operation == 'download'
        self.cancel_button.setVisible(downloading)
        self.cancel_button.setEnabled(downloading and not self._installer.cancel_event.is_set())
        self.download_button.setEnabled(available and bool(missing))
        self.download_button.setVisible(self.busy or bool(missing))
        self.status_label.setVisible(self.busy or bool(missing) or bool(self._notice))
        if self.busy:
            self.download_button.setText(self.text('Removing package…' if self._installer.operation == 'remove' else 'Preparing packages…'))
        else:
            size = byte_label(sum(spec['download_bytes'] for spec in missing))
            self.download_button.setText(self.text('Download required packages · {size}', size=size)
                                        if missing else self.text('Required packages installed'))
            if self._notice:
                self.status_label.setText(self.text(self._notice, **self._notice_values))
            elif ready:
                self.status_label.setText(self.text('Packages are ready for the selected languages.'))
            else:
                self.status_label.setText(self.text('Required: {packages}.',
                    packages='; '.join(self.package_label(spec) for spec in missing)))
        self.refresh_inventory()
        self.schedule_dialog_fit()
        self.panel.updateGeometry()

    def refresh_inventory(self):
        # Resolve current effective packages again: a partial shared installation
        # may become available between manager visits.
        for code in LANGUAGES:
            self.sections['input'].rows[code].spec = next(iter(translation_route(code, 'en')), None)
            self.sections['output'].rows[code].spec = next(iter(translation_route('en', code)), None)
        for section in self.sections.values():
            for row in section.rows.values():
                if row.extra:
                    primary_ids = {other.spec['id'] for other in section.rows.values()
                                   if not other.extra and other.spec is not None}
                    row.setVisible(package_present(row.spec) and row.spec['id'] not in primary_ids)
            section.refresh()

    def activate(self, role, key):
        if not self._available():
            self.refresh_inventory()
            return
        if role in ('input', 'output') and key in LANGUAGES:
            combo = self.input if role == 'input' else self.output
            combo.setCurrentIndex(combo.findData(key))
        elif role == 'recognition':
            index = self.panel.source.findData(key)
            if index >= 0:
                self.panel.source.setCurrentIndex(index)
        self.refresh_inventory()

    def choose_row(self, row):
        compatible_model = (row.key == 'small') == (self.source_language != 'en')
        if not row.extra and (row.role != 'recognition' or compatible_model):
            self.activate(row.role, row.key)
        else:
            row.refresh()
        if row.role == 'recognition':
            self.sections['recognition'].set_expanded(True)
        self.sections[row.role].reveal(row.key)

    def _begin(self, specs, operation='download'):
        if not self._available() or not specs or operation == 'remove' and self.panel.controller.running:
            return
        self._notice = ''
        self._remove_pending = None
        self.status_label.setText(self.text('Removing package…' if operation == 'remove' else 'Preparing download…'))
        self.progress.setRange(0, 0)
        worker = PackageInstaller(specs, self, operation)
        self._installer = worker
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._download_finished)
        self.refresh()
        worker.start()

    def download(self):
        self._begin(self.missing())

    def download_row(self, row):
        spec = row.spec
        if spec and not spec.get('readonly') and not package_ready(spec):
            self._begin([spec])

    def remove_row(self, row):
        spec = row.spec
        if (not self._available() or self.panel.controller.running or not spec
                or spec.get('readonly') or not package_present(spec)):
            return
        if self._remove_pending != spec['id']:
            self._remove_pending = spec['id']
            self.refresh_inventory()
            return
        self._begin([spec], 'remove')

    def _on_progress(self, event):
        if not self.busy or self._installer.cancel_event.is_set():
            return
        phase = event.get('phase', 'download')
        verb = {'download': 'Downloading', 'verify': 'Verifying',
                'install': 'Preparing', 'ready': 'Ready'}.get(phase, 'Preparing')
        done, total = int(event.get('done') or 0), int(event.get('total') or 0)
        if total > 0:
            self.progress.setRange(0, 1000)
            self.progress.setValue(max(0, min(1000, int(1000 * done / total))))
        else:
            self.progress.setRange(0, 0)
        # Resolve progress labels through the selected catalog specs, avoiding
        # backend Spanish labels in an otherwise English/Portuguese/French UI.
        spec = next((value for value in self._installer.specs
                     if value.get('label') == event.get('label')), None)
        label = self.package_label(spec) if spec else self.text('language packages')
        if phase == 'download' and total:
            self.status_label.setText(self.text('{action} {package} · {done} / {total}',
                action=self.text(verb), package=label, done=byte_label(done), total=byte_label(total)))
        else:
            self.status_label.setText(self.text('{action} {package}', action=self.text(verb), package=label))

    def cancel(self):
        if self.busy and self._installer.operation == 'download':
            self._installer.cancel_event.set()
            self.cancel_button.setEnabled(False)
            self.status_label.setText(self.text('Cancelling package preparation…'))

    def _download_finished(self):
        worker = self._installer
        if worker is None:
            return
        self._installer = None
        result, operation = worker.result, worker.operation
        worker.deleteLater()
        self._notice_values = {}
        if result == 'language_package_in_use':
            self._notice = 'Stop playback in every window before changing shared packages.'
        elif result == 'language_packages_busy':
            self._notice = 'Another window is preparing packages. Try again when it finishes.'
        elif result == 'error':
            self._notice = ('The package could not be removed. Close any session using it and try again.'
                            if operation == 'remove' else
                            'Packages could not be prepared. Check your connection and free space, then try again.')
        elif result == 'cancelled':
            self._notice = 'Preparation cancelled. Packages that finished installing are kept.'
        elif result == 'removed':
            self._notice = 'Package removed. Download it again before using languages that need it.'
        else:
            self._notice = 'Download complete.'
        self.refresh()
        self.changed.emit()
        if self.dialog.pending_close:
            self.dialog.pending_close = False
            self.dialog.reject()
        self.idle.emit()
