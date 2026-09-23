"""Reproductor con marco alternable; cerrar solicita limpieza cooperativa."""
import math
import sys
from PySide6.QtCore import Qt, QEvent, Signal, QRect, QTimer, QSize
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QApplication, QSlider, QStyle
from PySide6.QtGui import QPalette, QPainter, QIcon, QCursor, QShortcut, QKeySequence
from .composed_video import ComposedVideo
from .playback_timeline import PlaybackTimeline
from .player_controls import PlayerControls, player_icon


class PlayerWindow(QWidget):
    stop_requested = Signal()
    pause_requested = Signal()
    volume_changed = Signal(float)
    mute_changed = Signal(bool)
    settings_requested = Signal()

    def __init__(self):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        # layout_chrome reserva el título; evitar que Qt sume otra vez su área segura.
        self.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)
        self.setWindowTitle('Langbuffer')  # Identificación en la barra de tareas.
        self.setMinimumSize(320, 180)
        self.resize(1120, 630)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, Qt.GlobalColor.black)
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        self.picture = ComposedVideo(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.picture)
        self.wait_label = QLabel('Preparando video…', self)
        self.wait_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.wait_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.wait_label.setWordWrap(False)
        self.wait_label.setStyleSheet(
            'color: white; background: rgba(0,0,0,185); border: none; padding: 2px 6px;'
            'font-family: "Arial"; font-size: 24px;')
        self._normal_geometry = None
        self._windowed_geometry = QRect(self.geometry())
        self._changing_frame = False
        self._frame_restore_geometry = None
        self._native_frame = False
        self._chrome_visible = True
        self._frame_window = None
        self._drag_start = None
        self.close_button = QPushButton('×', self)
        self.close_button.setAccessibleName('Detener y cerrar')
        self.close_button.setToolTip('Detener y cerrar')
        self.close_button.setFixedSize(34, 34)
        self.close_button.setCursor(Qt.CursorShape.ArrowCursor)
        self.close_button.setStyleSheet(
            'QPushButton {color: white; background: rgba(0,0,0,110); border: none;'
            'font: 26px "Segoe UI"; padding: 0;}'
            'QPushButton:hover, QPushButton:focus {background: rgba(180,30,30,220);}')
        self.close_button.clicked.connect(self.close)
        self.minimize_button = QPushButton('−', self)
        self.minimize_button.setAccessibleName('Minimizar')
        self.minimize_button.setToolTip('Minimizar')
        self.minimize_button.setFixedSize(34, 34)
        self.minimize_button.setStyleSheet(
            'QPushButton {color: white; background: rgba(0,0,0,110); border: none;'
            'font: 24px "Segoe UI";} QPushButton:hover, QPushButton:focus {background: rgba(255,255,255,45);}')
        self.minimize_button.clicked.connect(self.showMinimized)
        self.frame_button = QPushButton(self)
        self.frame_button.setCheckable(True)
        self.frame_button.setFixedSize(34, 34)
        self.frame_button.setIcon(self.white_icon(QStyle.StandardPixmap.SP_TitleBarNormalButton))
        self.frame_button.setAccessibleName('Activar marco de Windows')
        self.frame_button.setToolTip('Activar marco de Windows')
        self.frame_button.setStyleSheet(
            'QPushButton {background: rgba(0,0,0,110); border: none;}'
            'QPushButton:hover, QPushButton:focus {background: rgba(255,255,255,45);}'
            'QPushButton:checked {background: rgba(140,180,230,55);}')
        self.frame_button.clicked.connect(self.toggle_frame)
        # El título y los controles los pinta la plataforma; aquí solo va el fondo.
        self.title_strip = QLabel('', self)
        self.title_strip.hide()
        self.title_strip.installEventFilter(self)
        self._closing = False
        self._muted = False
        self._paused = False
        self.controls = PlayerControls(self)
        self.controls.setObjectName('playerControls')
        self.controls.setStyleSheet(
            'QLabel, QPushButton {color: white; background: transparent;'
            'font-family: "Segoe UI Variable Text", "Segoe UI"; font-size: 14px; font-weight: 500;}'
            'QPushButton {border: none; padding: 0; border-radius: 18px;}'
            'QPushButton:hover {background: rgba(255,255,255,25);}'
            'QPushButton:focus-visible {border: 1px solid white;}'
            'QPushButton:checked {background: rgba(255,255,255,40);}')
        self.control_row = QWidget(self.controls)
        row = QHBoxLayout(self.control_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.pause_button = QPushButton(self.controls)
        self.stop_button = QPushButton(self.controls)
        self.stop_button.setIcon(player_icon('stop'))
        self.stop_button.setAccessibleName('Detener')
        self.stop_button.setToolTip('Detener')
        self.volume_button = QPushButton(self.controls)
        self.settings_button = QPushButton(self.controls)
        self.settings_button.setCheckable(True)
        self.settings_button.setIcon(player_icon('settings'))
        self.settings_button.setAccessibleName('Configuración')
        self.settings_button.setToolTip('Configuración')
        self.settings_button.clicked.connect(self.settings_requested.emit)
        self.fullscreen_button = QPushButton(self.controls)
        self.fullscreen_button.clicked.connect(self.toggle_fullscreen)
        for button in (self.pause_button, self.stop_button, self.volume_button, self.settings_button, self.fullscreen_button):
            button.setFixedSize(36, 36)
            button.setIconSize(QSize(20, 20))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        row.addWidget(self.pause_button)
        row.addWidget(self.stop_button)
        self.playback_timeline = PlaybackTimeline(self.controls)
        row.addSpacing(8)
        row.addWidget(self.playback_timeline.position)
        row.addSpacing(4)
        row.addWidget(self.playback_timeline.available)
        row.addSpacing(12)
        row.addWidget(self.playback_timeline.live_button)
        row.addStretch(1)
        row.addWidget(self.volume_button)
        row.addWidget(self.settings_button)
        row.addWidget(self.fullscreen_button)
        self.volume_popup = QWidget(self)
        self.volume_popup.setObjectName('volumePopup')
        self.volume_popup.setStyleSheet('QWidget#volumePopup {background: rgba(0,0,0,205); border-radius: 4px;}'
            'QSlider {background: transparent;} QSlider::groove:vertical {width: 3px; background: #777;}'
            'QSlider::add-page:vertical {background: white;}'
            'QSlider::handle:vertical {height: 10px; margin: 0 -4px; background: white; border-radius: 5px;}')
        volume_layout = QVBoxLayout(self.volume_popup)
        volume_layout.setContentsMargins(10, 8, 10, 8)
        self.volume_slider = QSlider(Qt.Orientation.Vertical, self.volume_popup)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(50)
        self.volume_slider.setAccessibleName('Volumen de escucha')
        volume_layout.addWidget(self.volume_slider)
        self.volume_popup.hide()
        self.pause_button.clicked.connect(self.pause_requested.emit)
        self.stop_button.clicked.connect(self.stop_requested.emit)
        self.volume_button.clicked.connect(self.toggle_mute)
        self.volume_slider.valueChanged.connect(self.change_volume)
        self._timeline_active = False
        self.exit_fullscreen = QShortcut(QKeySequence('Escape'), self)
        self.exit_fullscreen.activated.connect(self.escape_pressed)
        self.update_fullscreen_button()
        self.hide_controls_timer = QTimer(self)
        self.hide_controls_timer.setSingleShot(True)
        self.hide_controls_timer.setInterval(2200)
        self.hide_controls_timer.timeout.connect(self.hide_controls)
        self.set_paused(False)
        self.update_volume_icon()
        for widget in (self, self.picture, self.picture.viewport()):
            widget.setMouseTracking(True)
            widget.installEventFilter(self)
        for widget in (self.close_button, self.minimize_button, self.frame_button,
                       self.controls, self.pause_button, self.stop_button, self.volume_button, self.volume_slider,
                       self.control_row, self.settings_button, self.fullscreen_button, self.volume_popup,
                       self.playback_timeline, *self.playback_timeline.findChildren(QWidget),
                       self.playback_timeline.position, self.playback_timeline.available, self.playback_timeline.live_button):
            widget.setMouseTracking(True)
            widget.installEventFilter(self)
        self.set_timeline_active(False)

    def set_paused(self, paused):
        self._paused = paused
        self.pause_button.setIcon(player_icon('play' if paused else 'pause'))
        label = 'Reanudar' if paused else 'Pausar'
        self.pause_button.setToolTip(label)
        self.pause_button.setAccessibleName(label)
        self.pause_button.setEnabled(True)

    def update_volume_icon(self):
        silent = self._muted or self.volume_slider.value() == 0
        self.volume_button.setIcon(player_icon('muted' if silent else 'volume'))
        label = 'Activar sonido' if silent else 'Silenciar'
        self.volume_button.setToolTip(label)
        self.volume_button.setAccessibleName(label)

    def white_icon(self, kind):
        pixmap = self.style().standardIcon(kind).pixmap(20, 20)
        painter = QPainter(pixmap)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), Qt.GlobalColor.white)
        painter.end()
        return QIcon(pixmap)

    def toggle_mute(self):
        if self.volume_slider.value() == 0:
            self.volume_slider.setValue(50)
            self._muted = False
        else:
            self._muted = not self._muted
        self.mute_changed.emit(self._muted)
        self.update_volume_icon()

    def change_volume(self, value):
        self._muted = False
        self.volume_changed.emit(value / 100)
        self.mute_changed.emit(False)
        self.update_volume_icon()

    def reveal_controls(self):
        self._chrome_visible = True
        self.sync_chrome_visibility()
        self.controls.show()
        self.controls.raise_()
        self.playback_timeline.setVisible(self._timeline_active)
        self.playback_timeline.raise_()
        self.volume_popup.raise_()
        self.hide_controls_timer.start()

    def hide_controls(self, force=False):
        if not force and (self.controls.underMouse() or self.volume_slider.isSliderDown() or self._paused
                or self.volume_popup.underMouse() or self.settings_button.isChecked()
                or self.playback_timeline.underMouse() or self.playback_timeline.slider.isSliderDown()
                or any(button.underMouse() or button.isDown()
                       for button in (self.close_button, self.minimize_button, self.frame_button))):
            self.hide_controls_timer.start()
        else:
            self.hide_controls_timer.stop()
            self.volume_popup.hide()
            self.controls.hide()
            self.playback_timeline.hide()
            self._chrome_visible = False
            self.sync_chrome_visibility()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if hasattr(self, 'controls') and not self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            self.hide_controls(force=True)

    def enterEvent(self, event):
        super().enterEvent(event)
        if hasattr(self, 'controls'):
            self.reveal_controls()

    def hide_volume_if_outside(self):
        if not (self.volume_button.underMouse() or self.volume_popup.underMouse() or self.volume_slider.isSliderDown()):
            self.volume_popup.hide()

    def escape_pressed(self):
        if self.isFullScreen():
            self.toggle_fullscreen()

    def update_fullscreen_button(self):
        label = 'Salir de pantalla completa' if self.isFullScreen() else 'Pantalla completa'
        self.fullscreen_button.setIcon(player_icon('restore' if self.isFullScreen() else 'fullscreen'))
        self.fullscreen_button.setToolTip(label)
        self.fullscreen_button.setAccessibleName(label)

    def sync_chrome_visibility(self):
        native = self.native_frame_visible()
        self.close_button.setVisible(not native and self._chrome_visible)
        self.minimize_button.setVisible(not native and self._chrome_visible)
        self.frame_button.setVisible(native or self._chrome_visible)
        self.title_strip.setVisible(native)
        if self._native_frame and self.windowHandle() is not None:
            self.sync_platform_titlebar(native)

    def sync_platform_titlebar(self, visible):
        # La barra expandida de Qt es un HWND hijo, fuera del árbol QWidget.
        # En pantalla completa se oculta sin cambiar márgenes ni flags.
        if sys.platform != 'win32' or QApplication.platformName() != 'windows':
            return
        import ctypes as C
        from ctypes import wintypes as W
        user = C.WinDLL('user32')
        callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        user.EnumChildWindows.argtypes = [W.HWND, callback_type, W.LPARAM]
        user.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
        user.ShowWindow.argtypes = [W.HWND, C.c_int]
        @callback_type
        def visit(hwnd, unused):
            name = C.create_unicode_buffer(200)
            user.GetClassNameW(hwnd, name, len(name))
            if name.value == '_q_titlebar':
                user.ShowWindow(hwnd, 5 if visible else 0)
            return True
        user.EnumChildWindows(int(self.winId()), visit, 0)

    def edges_at(self, point):
        edges = Qt.Edge(0)
        if self.isFullScreen() or self.native_frame_visible() or self.isMaximized():
            return edges
        if point.x() < 7:
            edges |= Qt.Edge.LeftEdge
        elif point.x() >= self.width()-7:
            edges |= Qt.Edge.RightEdge
        if point.y() < 7:
            edges |= Qt.Edge.TopEdge
        elif point.y() >= self.height()-7:
            edges |= Qt.Edge.BottomEdge
        return edges

    def eventFilter(self, obj, event):
        if hasattr(self, 'volume_popup'):
            if obj is self.volume_button and event.type() in (QEvent.Type.Enter, QEvent.Type.MouseMove):
                self.volume_popup.show()
                self.volume_popup.raise_()
            elif obj in (self.volume_button, self.volume_popup, self.volume_slider) and event.type() == QEvent.Type.Leave:
                QTimer.singleShot(0, self.hide_volume_if_outside)
        if (hasattr(self, 'controls') and (obj in (self.close_button, self.minimize_button, self.frame_button,
                self.controls) or self.controls.isAncestorOf(obj)
                or (hasattr(self, 'volume_popup') and (obj is self.volume_popup or self.volume_popup.isAncestorOf(obj)))
                or (hasattr(self, 'playback_timeline') and (obj is self.playback_timeline or self.playback_timeline.isAncestorOf(obj))))):
            if event.type() in (QEvent.Type.Enter, QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress):
                self.reveal_controls()
            return super().eventFilter(obj, event)
        if (event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                             QEvent.Type.MouseButtonDblClick, QEvent.Type.MouseMove)
                and self.native_frame_visible()
                and self.mapFromGlobal(event.globalPosition().toPoint()).y() < self.title_height()):
            # No consumir los gestos de la barra que corresponden al sistema.
            return super().eventFilter(obj, event)
        if event.type() == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
            point = self.mapFromGlobal(event.globalPosition().toPoint())
            if 0 <= point.y() <= 56:
                self._drag_start = None
                self.toggle_fullscreen()
                return True
        if event.type() == QEvent.Type.MouseButtonRelease:
            self._drag_start = None
        if event.type() in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress):
            self.reveal_controls()
            edges = self.edges_at(self.mapFromGlobal(event.globalPosition().toPoint()))
            if event.type() == QEvent.Type.MouseMove:
                if (self._drag_start is not None and event.buttons() & Qt.MouseButton.LeftButton
                        and not self.isFullScreen()
                        and (event.globalPosition().toPoint()-self._drag_start).manhattanLength() >= QApplication.startDragDistance()):
                    self._drag_start = None
                    return self.windowHandle().startSystemMove()
                horizontal = bool(edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge))
                vertical = bool(edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge))
                if horizontal and vertical:
                    forward = edges in (Qt.Edge.LeftEdge | Qt.Edge.TopEdge, Qt.Edge.RightEdge | Qt.Edge.BottomEdge)
                    cursor = Qt.CursorShape.SizeFDiagCursor if forward else Qt.CursorShape.SizeBDiagCursor
                else:
                    cursor = Qt.CursorShape.SizeHorCursor if horizontal else Qt.CursorShape.SizeVerCursor if vertical else Qt.CursorShape.ArrowCursor
                obj.setCursor(cursor)
            elif event.button() == Qt.MouseButton.LeftButton and self.windowHandle():
                if edges:
                    return self.windowHandle().startSystemResize(edges)
                self._drag_start = event.globalPosition().toPoint()
                return True
        return super().eventFilter(obj, event)

    def native_frame_visible(self):
        return self._native_frame and not self.isFullScreen()

    def content_rect(self):
        return self.rect().adjusted(0, self.title_height(), 0, 0)

    def title_height(self):
        if not self.native_frame_visible():
            return 0
        handle = self.windowHandle()
        return max(31, handle.safeAreaMargins().top() if handle is not None else 0)

    def toggle_frame(self):
        visible = self.isVisible()
        state = self.windowState()
        geometry = QRect(self._windowed_geometry if self.isMaximized() else self.geometry())
        self._native_frame = not self._native_frame
        self._drag_start = None
        if self.isFullScreen() and self._native_frame:
            state &= ~Qt.WindowState.WindowFullScreen
            if self._normal_geometry is not None:
                geometry = QRect(self._normal_geometry)
            self.showNormal()
        flags = self.windowFlags()
        decoration = (Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowSystemMenuHint
                      | Qt.WindowType.WindowMinMaxButtonsHint | Qt.WindowType.WindowCloseButtonHint
                      | Qt.WindowType.ExpandedClientAreaHint | Qt.WindowType.NoTitleBarBackgroundHint)
        if self._native_frame:
            flags = (flags & ~Qt.WindowType.FramelessWindowHint) | decoration
        else:
            flags = (flags & ~decoration) | Qt.WindowType.FramelessWindowHint
        self._changing_frame = True
        self.retire_expanded_titlebar(flags)
        self.setWindowFlags(flags)
        self.setGeometry(geometry)
        self.setWindowState(state)
        if visible:
            self.show()
        self.layout_chrome()
        self._changing_frame = False
        self._windowed_geometry = QRect(geometry)
        if self.isMaximized():
            self._frame_restore_geometry = QRect(geometry)
        elif not self.isFullScreen():
            # Windows puede notificar los nuevos márgenes después de mostrar.
            QTimer.singleShot(0, lambda: self.restore_frame_geometry(geometry))

    def retire_expanded_titlebar(self, flags):
        handle = self.windowHandle()
        if (handle is not None and handle.flags() & Qt.WindowType.ExpandedClientAreaHint
                and not flags & Qt.WindowType.ExpandedClientAreaHint):
            # QWidget oculta primero al cambiar flags. Qt/Windows no retira su
            # barra hija si IsWindowVisible ya ve el padre oculto: retirarla antes.
            handle.setFlag(Qt.WindowType.ExpandedClientAreaHint, False)

    def layout_chrome(self):
        if not hasattr(self, 'frame_button'):
            return
        native = self.native_frame_visible()
        top = self.title_height()
        self.layout().setContentsMargins(0, top, 0, 0)
        self.layout_wait_label()
        self.sync_chrome_visibility()
        dark = QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        self.title_strip.setStyleSheet('background: '+('#202020' if dark else '#f3f3f3')+';')
        self.title_strip.setGeometry(0, 0, self.width(), top)
        self.title_strip.raise_()
        self.frame_button.setChecked(self._native_frame)
        label = 'Quitar marco de Windows' if self._native_frame else 'Activar marco de Windows'
        self.frame_button.setToolTip(label)
        self.frame_button.setAccessibleName(label)
        self.frame_button.setFixedSize(34, min(34, top) if native else 34)
        # Qt reserva tres botones de título del sistema (1,5 × altura cada uno).
        reserved = math.ceil(top * 4.5) + 8 if native else 76
        self.frame_button.move(self.width()-reserved-44, 0 if native else 10)
        self.frame_button.raise_()

    def showEvent(self, event):
        super().showEvent(event)
        handle = self.windowHandle()
        if handle is not None and handle is not self._frame_window:
            self._frame_window = handle
            handle.safeAreaMarginsChanged.connect(self.layout_chrome)
        self.layout_chrome()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self.layout_chrome()
            if hasattr(self, 'fullscreen_button'):
                self.update_fullscreen_button()
            if (not self._changing_frame and self._frame_restore_geometry is not None
                    and not (self.isMaximized() or self.isFullScreen() or self.isMinimized())):
                geometry = self._frame_restore_geometry
                self._frame_restore_geometry = None
                QTimer.singleShot(0, lambda: self.restore_frame_geometry(geometry))

    def restore_frame_geometry(self, geometry):
        if not (self.isMaximized() or self.isFullScreen() or self.isMinimized()):
            self.setGeometry(geometry)

    def remember_windowed_geometry(self):
        if (hasattr(self, '_changing_frame') and not self._changing_frame
                and not (self.isMaximized() or self.isFullScreen() or self.isMinimized())):
            self._windowed_geometry = QRect(self.geometry())

    def moveEvent(self, event):
        super().moveEvent(event)
        self.remember_windowed_geometry()

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            if self._normal_geometry is not None:
                geometry = QRect(self._normal_geometry)
                self.setGeometry(geometry)
                # El sistema publica los márgenes al salir de pantalla completa.
                QTimer.singleShot(50, lambda: self.restore_frame_geometry(geometry))
        else:
            self._normal_geometry = QRect(self.geometry())
            self.showFullScreen()

    def show_wait(self, remaining=None, *, paused=False, visible=True, reconnecting=False):
        if not visible:
            self.wait_label.hide()
            return
        if reconnecting:
            countdown = f' ({max(0, math.ceil(remaining))} s)' if remaining is not None else ''
            text = 'Anuncio de Twitch detectado. Intentando reconectar'+countdown
        elif paused:
            text = 'En pausa'
        elif remaining is None:
            text = 'Preparando video…'
        elif remaining > 0:
            text = f'Almacenando en buffer y traduciendo ahora... {math.ceil(remaining)} s'
        else:
            text = 'Mostrando video…'
        self.wait_label.setText(text)
        self.layout_wait_label()
        self.wait_label.show()
        self.wait_label.raise_()
        self.controls.raise_()
        self.playback_timeline.raise_()
        self.volume_popup.raise_()
        self.close_button.raise_()
        self.minimize_button.raise_()
        self.frame_button.raise_()

    def layout_wait_label(self):
        if not hasattr(self, 'wait_label'):
            return
        self.wait_label.adjustSize()
        area = self.content_rect()
        size = self.wait_label.size()
        self.wait_label.move(area.x()+(area.width()-size.width())//2,
                             area.y()+(area.height()-size.height())//2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.remember_windowed_geometry()
        self.layout_chrome()
        self.close_button.move(self.width()-self.close_button.width()-10, 10)
        self.minimize_button.move(self.width()-self.close_button.width()-48, 10)
        self.minimize_button.raise_()
        self.close_button.raise_()
        self.layout_playback_controls()
        self.controls.raise_()
        self.volume_popup.raise_()

    def set_timeline_active(self, active):
        self._timeline_active = active
        self.playback_timeline.setVisible(active and self._chrome_visible)
        if not active:
            self.playback_timeline.reset()
        for widget in (self.playback_timeline.position, self.playback_timeline.available, self.playback_timeline.live_button):
            widget.setVisible(active)
        self.layout_playback_controls()

    def layout_playback_controls(self):
        self.controls.setGeometry(0, self.height()-96, self.width(), 96)
        self.control_row.setGeometry(8, 48, self.width()-16, 40)
        self.control_row.layout().activate()
        self.playback_timeline.setGeometry(8, 26, self.width()-16, 20)
        self.playback_timeline.available.setVisible(self._timeline_active and self.width() >= 480)
        point = self.volume_button.mapTo(self, self.volume_button.rect().topLeft())
        self.volume_popup.setGeometry(point.x(), point.y()-104, 36, 104)
        self.playback_timeline.raise_()

    def closeEvent(self, event):
        if not self._closing:
            self._closing = True
            self.stop_requested.emit()
        event.accept()
