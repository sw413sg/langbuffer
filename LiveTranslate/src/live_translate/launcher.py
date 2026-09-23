"""Paneles integrados para enlace, subtítulos y resolución."""
from PySide6.QtCore import QTimer, Qt, Signal, QSize
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QFormLayout,
                               QComboBox, QLabel, QPushButton, QHBoxLayout,
                               QCheckBox, QLineEdit, QSlider, QStackedWidget)

from .settings import SettingsStore, DEFAULTS, fit_geometry
from .media_source import content_url


# El ajuste visible es relativo a este calibrado histórico. No es una marca de voz.
CAPTION_OFFSET_BASELINE = 0.0


class CurrentPageStack(QStackedWidget):
    """No deja que la página grande ensanche el selector compacto."""
    def sizeHint(self):
        widget = self.currentWidget()
        return widget.sizeHint() if widget is not None else QSize()

    def minimumSizeHint(self):
        widget = self.currentWidget()
        return widget.minimumSizeHint() if widget is not None else QSize()


class Launcher(QWidget):
    resolution_requested = Signal(object)
    appearance_changed = Signal(dict)
    preview_requested = Signal(bool)
    section_changed = Signal(str)

    def __init__(self, controller=None, discover=True, store=None, parent=None):
        super().__init__(parent)
        self.embedded = parent is not None
        self.setWindowTitle('Live Translate — configuración')
        self.resize(420, 430)
        self.controller = controller
        self.closing = False
        self._restart_pending = False
        self._section = 'settings'
        self.store = store if store is not None else (SettingsStore() if discover else None)
        self.preferences, self.preference_warning = self.store.load() if self.store else (dict(DEFAULTS), None)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.pages = CurrentPageStack()
        layout.addWidget(self.pages)
        self._build_settings_page(discover)
        self._build_subtitles_page()

        self.caption_shortcut = QShortcut(QKeySequence('Ctrl+Shift+S'), self)
        self.caption_shortcut.activated.connect(self.controller.show_captions)
        self.controller.changed.connect(self.on_state)
        self.controller.failed.connect(self.on_error)
        self.controller.completed.connect(self.on_completed)
        self.on_state('idle')
        self.update_appearance(emit=False)
        if not self.embedded and 'geometry' in self.preferences:
            screens = [screen.availableGeometry().getRect() for screen in QApplication.screens()]
            self.setGeometry(*fit_geometry(self.preferences['geometry'], screens))

    def _build_settings_page(self, discover):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        form = QFormLayout()
        self.session_form = form
        self.content_url = QLineEdit()
        self.content_url.setMaxLength(2048)
        self.content_url.setPlaceholderText('YouTube Live, Facebook, X, Twitch or Kick link')
        self.content_url.setToolTip('El enlace puede editarse durante la reproducción. Pulsa Reiniciar para aplicarlo.')
        form.addRow('Enlace del contenido', self.content_url)
        self.source = QComboBox()
        self.listener = QComboBox()
        form.addRow('Recognition', self.source)
        form.addRow('Escuchar por', self.listener)
        self.delay = QSlider(Qt.Orientation.Horizontal)
        self.delay.setRange(1, 30)
        self.delay.setValue(self.preferences['delay'])
        self.delay.setAccessibleName('Retraso en segundos')
        self.delay_value = QLabel(f'{self.delay.value()} s')
        self.delay_value.setMinimumWidth(35)
        self.delay.valueChanged.connect(lambda value: self.delay_value.setText(f'{value} s'))
        delay_row = QWidget()
        delay_layout = QHBoxLayout(delay_row)
        delay_layout.setContentsMargins(0, 0, 0, 0)
        delay_layout.addWidget(self.delay)
        delay_layout.addWidget(self.delay_value)
        form.addRow('Retraso', delay_row)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        self.start = QPushButton('Iniciar')
        self.restart = QPushButton('Reiniciar')
        self.stop = QPushButton('Detener')
        buttons.addWidget(self.start, 1)
        buttons.addWidget(self.restart, 1)
        buttons.addWidget(self.stop, 1)
        layout.addLayout(buttons)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.hide()
        layout.addWidget(self.status)
        self.start.clicked.connect(self.start_session)
        self.restart.clicked.connect(self.restart_session)
        self.stop.clicked.connect(self.controller.stop)
        from .resolution_selector import ResolutionSelector
        self.resolution = ResolutionSelector(self)
        self.resolution.hide()
        self.content_url.textChanged.connect(lambda value: self.resolution.set_source(value, probe=discover))
        self.resolution.requested.connect(self.resolution_requested)
        self.pages.addWidget(page)

    def _combo(self, items, current):
        combo = QComboBox()
        for label, value in items:
            combo.addItem(label, value)
        combo.setCurrentIndex(max(0, combo.findData(current)))
        return combo

    def _slider_row(self, slider, value_label):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(slider)
        layout.addWidget(value_label)
        return row

    def _build_subtitles_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        selected_size = 0 if self.preferences.get('font_size_mode') == 'auto' else self.preferences['font_size']
        self.font_size = self._combo([('Automático', 0), ('18 px', 18), ('24 px', 24),
                                      ('30 px', 30), ('36 px', 36), ('40 px', 40)], selected_size)
        self.font_family = self._combo([('Arial', 'Arial'), ('Segoe UI', 'Segoe UI'),
                                        ('Verdana', 'Verdana')], self.preferences['font_family'])
        self.text_color = self._combo([('Blanco', 'white'), ('Amarillo', 'yellow'),
                                       ('Rojo', 'red')], self.preferences['text_color'])
        self.original_text_color = self._combo([('Amarillo', 'yellow'), ('Blanco', 'white'),
                                                ('Rojo', 'red')], self.preferences['original_text_color'])
        self.background_color = self._combo([('Negro', 'black'), ('Blanco', 'white'),
                                             ('Gris', 'gray')], self.preferences['background_color'])
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(0, 10)
        self.opacity.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.opacity.setTickInterval(1)
        self.opacity.setValue(self.preferences['background_opacity_level'])
        self.opacity_value = QLabel(str(self.opacity.value()))
        self.opacity_value.setMinimumWidth(20)
        self.wrap_width = QSlider(Qt.Orientation.Horizontal)
        self.wrap_width.setRange(40, 100)
        self.wrap_width.setSingleStep(2)
        self.wrap_width.setValue(self.preferences['caption_width'])
        self.wrap_value = QLabel(f'{self.wrap_width.value()} %')
        self.wrap_value.setMinimumWidth(40)
        self.text_alignment = self._combo([('Izquierda', 'left'), ('Centro', 'center'),
                                           ('Derecha', 'right')], self.preferences['text_alignment'])
        self.position_x = QSlider(Qt.Orientation.Horizontal)
        self.position_x.setRange(-100, 100)
        self.position_x.setValue(self.preferences['caption_x'])
        self.position_x.setToolTip('Valores negativos mueven a la izquierda; positivos, a la derecha.')
        self.position_x_value = QLabel(str(self.position_x.value()))
        self.position_x_value.setMinimumWidth(32)
        self.position_y = QSlider(Qt.Orientation.Horizontal)
        self.position_y.setRange(-100, 100)
        self.position_y.setValue(self.preferences['caption_y'])
        self.position_y.setToolTip('Valores negativos mueven hacia abajo; positivos, hacia arriba.')
        self.position_y_value = QLabel(str(self.position_y.value()))
        self.position_y_value.setMinimumWidth(32)
        self.caption_offset = QSlider(Qt.Orientation.Horizontal)
        self.caption_offset.setRange(-50, 50)
        self.caption_offset.setSingleStep(1)
        self.caption_offset.setPageStep(5)
        self.caption_offset.setValue(round(self.preferences['caption_offset'] * 10))
        self.caption_offset.setAccessibleName('Desfase de subtítulos')
        self.caption_offset.setToolTip('0,0 s usa el calibrado base de −0,7 s. Negativo adelanta; positivo retrasa.')
        self.caption_offset_value = QLabel()
        self.caption_offset_value.setMinimumWidth(50)
        self.show_original = QCheckBox('Mostrar también el original en inglés')
        self.show_original.setChecked(self.preferences['show_original'])
        self.always_on_top = QCheckBox('Mantener la reproducción siempre visible')
        self.always_on_top.setChecked(self.preferences['always_on_top'])
        form.addRow('Tamaño', self.font_size)
        form.addRow('Fuente', self.font_family)
        form.addRow('Color del texto', self.text_color)
        form.addRow('Color del original', self.original_text_color)
        form.addRow('Color del fondo', self.background_color)
        form.addRow('Opacidad del fondo', self._slider_row(self.opacity, self.opacity_value))
        form.addRow('Ancho de línea', self._slider_row(self.wrap_width, self.wrap_value))
        form.addRow('Alineación', self.text_alignment)
        form.addRow('Posición horizontal', self._slider_row(self.position_x, self.position_x_value))
        form.addRow('Posición vertical', self._slider_row(self.position_y, self.position_y_value))
        form.addRow('Desfase', self._slider_row(self.caption_offset, self.caption_offset_value))
        layout.addLayout(form)
        layout.addWidget(self.show_original)
        layout.addWidget(self.always_on_top)
        help_label = QLabel('Mientras esta ventana esté abierta, el video muestra un ejemplo. Al cerrarla vuelven los subtítulos en directo.')
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        layout.addStretch()
        for control in (self.font_size, self.font_family, self.background_color,
                        self.opacity, self.wrap_width, self.text_alignment, self.position_x,
                        self.position_y, self.caption_offset, self.show_original):
            signal = (control.toggled if isinstance(control, QCheckBox) else
                      control.valueChanged if isinstance(control, QSlider) else control.currentIndexChanged)
            signal.connect(self.update_appearance)
        self.text_color.currentIndexChanged.connect(self.ensure_distinct_text_colors)
        self.original_text_color.currentIndexChanged.connect(self.ensure_distinct_text_colors)
        self.ensure_distinct_text_colors(emit=False)
        self.always_on_top.toggled.connect(self.update_appearance)
        self.pages.addWidget(page)

    def show_section(self, section):
        indices = {'settings': 0, 'subtitles': 1}
        if section not in indices:
            return
        was_preview = self._section == 'subtitles'
        self._section = section
        self.pages.setCurrentIndex(indices[section])
        self.section_changed.emit(section)
        if was_preview != (section == 'subtitles') or section == 'subtitles':
            self.preview_requested.emit(section == 'subtitles')

    @property
    def section(self):
        return self._section

    def close_preview(self):
        if self._section == 'subtitles':
            self.preview_requested.emit(False)



    def appearance_values(self):
        return dict(font_size=self.font_size.currentData(), font_family=self.font_family.currentData(),
                    text_color=self.text_color.currentData(), original_text_color=self.original_text_color.currentData(),
                    background_color=self.background_color.currentData(),
                    background_opacity_level=self.opacity.value(), caption_width=self.wrap_width.value(),
                    text_alignment=self.text_alignment.currentData(), caption_x=self.position_x.value(),
                    caption_y=self.position_y.value(), show_original=self.show_original.isChecked())

    def ensure_distinct_text_colors(self, *unused, emit=True):
        if self.original_text_color.currentData() == self.text_color.currentData():
            replacement = next(value for value in ('yellow', 'white', 'red')
                               if value != self.text_color.currentData())
            self.original_text_color.blockSignals(True)
            self.original_text_color.setCurrentIndex(self.original_text_color.findData(replacement))
            self.original_text_color.blockSignals(False)
        self.update_appearance(emit=emit)

    def effective_caption_offset(self):
        return round(CAPTION_OFFSET_BASELINE + self.caption_offset.value()/10, 1)

    def update_appearance(self, *unused, emit=True):
        self.opacity_value.setText(str(self.opacity.value()))
        self.wrap_value.setText(f'{self.wrap_width.value()} %')
        self.position_x_value.setText(str(self.position_x.value()))
        self.position_y_value.setText(str(self.position_y.value()))
        value = self.caption_offset.value()/10
        self.caption_offset_value.setText(f'{value:+.1f} s' if value else '0.0 s')
        self.preferences.update(self.appearance_values(), caption_offset=value)
        if emit:
            self.save_preferences()
            self.appearance_changed.emit(self.appearance_values())
            if self.controller.running or self.controller.state == 'paused':
                self.controller.set_caption_offset(self.effective_caption_offset())

    def save_preferences(self):
        if self.store is None:
            return True
        data = dict(self.preferences)
        for key, control in (('source', self.source), ('listener', self.listener)):
            if control.currentText():
                data[key] = control.currentText()
        data.update(self.appearance_values(), always_on_top=self.always_on_top.isChecked(),
                    font_size_mode='auto' if self.font_size.currentData() == 0 else 'fixed',
                    delay=self.delay.value(), caption_offset=self.caption_offset.value()/10,
                    caption_offset_calibrated=True, duration=0)
        if not self.embedded:
            data['geometry'] = list(self.geometry().getRect())
        try:
            self.store.save(data)
            self.preferences = data
            self.preference_warning = None
            return True
        except OSError:
            self.preference_warning = 'No se pudieron guardar los ajustes locales.'
            self.set_status(self.preference_warning)
            return False

    def validate_start(self):
        from .integrated import validate_local_start
        return validate_local_start(self)

    def start_session(self):
        if self.controller.running or self.controller.state != 'idle':
            return
        selected_url = self.validate_start()
        if selected_url is None:
            return
        self.content_url.setText(selected_url)
        self.save_preferences()
        self.resolution.cancel()
        self.set_status('')
        self.controller.start(['--content-url', selected_url,
            '--delay', str(self.delay.value()), '--caption-offset', str(self.effective_caption_offset()),
            '--resolution', str(self.resolution.height()), '--fps', str(self.resolution.fps())])

    def restart_session(self):
        if not self.controller.running and self.controller.state == 'idle':
            self.start_session()
            return
        if self.validate_start() is None:
            return
        self._restart_pending = True
        self.set_status('Reiniciando la conexión…')
        self.controller.stop()

    def is_original(self):
        return True

    def toggle_pause(self):
        if self.controller.state == 'paused':
            self.controller.resume()
        else:
            self.controller.pause()

    def on_state(self, state):
        idle = state == 'idle'
        for control in (self.source, self.listener, self.delay):
            control.setEnabled(idle)
        self.content_url.setEnabled(True)
        self.start.setEnabled(idle and not self._restart_pending)
        self.restart.setEnabled(not self._restart_pending)
        self.stop.setEnabled(state not in {'idle', 'stopping'})
        self.resolution.setEnabled(idle or (self.embedded and state in {'playing', 'paused'}))
        self.caption_offset.setEnabled(state not in {'starting', 'stopping', 'routing_audio', 'restoring_audio'})


    def remember_player(self, result):
        from .settings import validated
        values = validated(result)
        for key in ('player_geometry', 'caption_geometry'):
            if key in values:
                self.preferences[key] = values[key]
        self.save_preferences()

    def set_status(self, message):
        self.status.setText(message)
        self.status.setVisible(bool(message))

    def on_error(self, message):
        self._restart_pending = False
        explanations = {
            'language_packages_busy': 'Another window is preparing packages. Try again when it finishes.',
            'language_packages_missing': 'Download the selected language packages before starting.',
            'youtube_direct_live_link_required': 'Paste a direct YouTube Live or Facebook video link.',
            'facebook_direct_video_link_required': 'Paste a direct YouTube Live or Facebook video link.',
            'invalid_source_url': 'Paste a direct YouTube Live or Facebook video link.',
            'youtube_playlist_out_of_scope': 'YouTube support is limited to active live streams.',
            'source_source_scheduled': 'This stream has not started yet.',
            'source_youtube_live_ended': 'This YouTube stream has ended.',
            'source_youtube_vod_out_of_scope': 'YouTube support is limited to active live streams.',
            'source_source_login_required': 'This content requires a login. Use a public link.',
            'source_javascript_runtime_missing': 'The YouTube JavaScript runtime is missing.',
            'source_ejs_unavailable': 'The YouTube EJS package is missing or incompatible.',
            'source_facebook_state_unverified': 'Facebook live or video state could not be verified.',
            'source_live_state_unknown': 'The stream did not publish new segments during verification.',
            'source_paired_clock_unverified': 'The audio and video tracks could not be aligned.',
            'source_paired_codec_unsupported': 'These separate audio/video tracks are not supported.',
            'source_dash_transport_unimplemented': 'This source only offers an unsupported DASH format.',
            'source_separate_tracks_unimplemented': 'These separate audio/video tracks are not supported.',
            'source_range_not_supported': 'This video server does not support the required seeking.',
            'source_source_timeout': 'Checking this source timed out. Try again.',
            'source_source_unavailable': 'The source could not be accessed. Check the public link and try again.',
            'source_content_identity_changed': 'The source returned a different video. Playback was stopped.',
            'source_no_supported_combined_format': 'No supported audio/video format is available for this source.',
            'source_twitch_recovery_limit': 'Twitch cambió de formato repetidamente y agotó los reintentos automáticos. Espera un momento y pulsa Reiniciar.',
            'source_live_window_expired': 'El directo retiró contenido pendiente. Pulsa Reiniciar para volver cerca del directo.',
            'source_live_playlist_stalled': 'El servidor dejó de publicar segmentos. Puedes pulsar Reiniciar.',
            'source_twitch_offline': 'Ese canal de Twitch no está en directo.',
            'source_twitch_unavailable': 'No se pudo acceder al directo de Twitch.',
            'source_kick_offline': 'Ese canal de Kick no está en directo.',
            'source_kick_unavailable': 'No se pudo acceder al directo de Kick.',
            'source_kick_dvr_unavailable': 'Kick no publicó una repetición activa para poder retroceder.',
            'source_resolution_unavailable': 'Esa resolución ya no está disponible. Elige otra.',
            'source_quality_unavailable': 'Esa combinación de resolución y FPS ya no está disponible. Elige otra.',
            'source_resource_byte_limit': 'Un segmento del video supera el límite temporal de seguridad. Elige otra calidad.',
        }
        for code, explanation in explanations.items():
            if code in message.splitlines():
                message = explanation
                break
        self.set_status('Error: '+message)
        if self.closing and not self.controller.running:
            QTimer.singleShot(0, self.close)

    def on_completed(self, result):
        self.remember_player(result)
        if self._restart_pending:
            self._restart_pending = False
            QTimer.singleShot(0, self.start_session)
        else:
            self.set_status('')
        if self.closing:
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event):
        self.close_preview()
        if self.controller.running:
            self.closing = True
            self.controller.stop()
            event.ignore()
        else:
            self.resolution.cancel()
            self.save_preferences()
            event.accept()


