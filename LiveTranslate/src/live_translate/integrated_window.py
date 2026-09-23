"""Una ventana para selección, reproducción original y controles."""
from PySide6.QtCore import Qt, QEvent, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QWidget, QLabel, QMenu
from .player_window import PlayerWindow
from .panel_drawer import PanelDrawer
from .launcher import Launcher
from .video_captions import VideoCaptions


class IntegratedWindow(PlayerWindow):
    def __init__(self, controller=None, discover=True, store=None):
        super().__init__()
        self.setMinimumSize(640, 420)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.panel = Launcher(controller=controller, discover=discover, store=store, parent=self)
        self.controller = self.panel.controller
        self.drawer = PanelDrawer(self, self.panel)
        self.drawer.relayout()
        self.caption_overlay = VideoCaptions(self.picture, **self.panel.appearance_values())
        self.caption_overlay.reveal()
        self.settings_menu = QMenu(self)
        self.settings_menu.setStyleSheet(
            'QMenu {background:#202733; color:white; border:1px solid #465264; padding:5px;}'
            'QMenu::item {padding:8px 28px 8px 14px; border-radius:4px;}'
            'QMenu::item:selected {background:#3a516e;}')
        self.configuration_action = self.settings_menu.addAction('Configuración')
        self.subtitles_action = self.settings_menu.addAction('Subtítulos')
        self.resolution_menu = self.settings_menu.addMenu(self.panel.resolution.label())
        self.resolution_action = self.resolution_menu.menuAction()
        self.configuration_action.triggered.connect(lambda: self.open_section('settings'))
        self.subtitles_action.triggered.connect(lambda: self.open_section('subtitles'))
        self.rebuild_resolution_menu()
        self.settings_menu.aboutToHide.connect(
            lambda: QTimer.singleShot(0, lambda: self.settings_visibility(self.drawer.opened)))
        self.settings_requested.connect(self.show_settings_menu)
        self.drawer.visibility_changed.connect(self.settings_visibility)
        self._closing_app = False
        self.controller.changed.connect(self.session_state)
        self.controller.failed.connect(self.session_error)
        self.controller.completed.connect(self.session_completed)
        self.pause_requested.connect(self.panel.toggle_pause)
        self.stop_requested.connect(self.controller.stop)
        self.panel.resolution_requested.connect(self.change_resolution)
        self.panel.resolution.label_changed.connect(self.update_resolution_action)
        self.panel.appearance_changed.connect(self.apply_appearance)
        self.panel.preview_requested.connect(self.set_caption_preview)
        self.panel.always_on_top.toggled.connect(self.apply_on_top)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.panel.always_on_top.isChecked())
        self.panel_shortcut = QShortcut(QKeySequence('Ctrl+,'), self)
        self.panel_shortcut.activated.connect(self.show_settings_menu)
        self._drag_surfaces = [self.drawer, self.drawer.body, self.drawer.scroll.viewport()]
        self._drag_surfaces += [widget for widget in self.drawer.findChildren(QWidget)
                               if type(widget) is QLabel]
        for widget in self._drag_surfaces:
            widget.setMouseTracking(True)
            widget.installEventFilter(self)
        self.show_wait(visible=False)
        self.session_state('idle')

    def apply_on_top(self, enabled):
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        if visible:
            self.show()

    def raise_chrome(self):
        self.controls.raise_()
        self.playback_timeline.raise_()
        self.volume_popup.raise_()
        self.close_button.raise_()
        self.minimize_button.raise_()
        self.frame_button.raise_()

    def update_resolution_action(self, label):
        self.resolution_menu.setTitle(label)
        self.rebuild_resolution_menu()

    def rebuild_resolution_menu(self):
        self.resolution_menu.clear()
        combo = self.panel.resolution.combo
        for index in range(combo.count()):
            quality = combo.itemData(index) or (0, 0)
            action = self.resolution_menu.addAction(combo.itemText(index))
            action.setCheckable(True)
            action.setChecked(index == combo.currentIndex())
            action.triggered.connect(lambda checked=False, value=quality: self.select_resolution(value))

    def select_resolution(self, quality):
        if not self.panel.resolution.isEnabled():
            return
        index = self.panel.resolution.index_of(quality)
        if index >= 0:
            self.panel.resolution.combo.setCurrentIndex(index)

    def apply_appearance(self, values):
        self.caption_overlay.configure(**values)

    def set_caption_preview(self, enabled):
        self.caption_overlay.set_preview(enabled)

    def open_section(self, section):
        self.panel.show_section(section)
        self.drawer.set_open(True, animate=False)
        self.settings_visibility(True)

    def show_settings_menu(self):
        if self.drawer.opened:
            self.drawer.set_open(False, force=True)
            self.settings_button.setChecked(False)
            return
        if self.settings_menu.isVisible():
            self.settings_menu.hide()
            self.settings_button.setChecked(False)
            return
        point = self.settings_button.mapToGlobal(self.settings_button.rect().topLeft())
        size = self.settings_menu.sizeHint()
        self.settings_menu.popup(point - self.settings_menu.rect().topLeft()
                                 - type(point)(max(0, size.width()-self.settings_button.width()), size.height()))
        self.settings_button.setChecked(True)

    def toggle_settings(self):
        self.show_settings_menu()

    def settings_visibility(self, visible):
        self.settings_button.setChecked(visible or self.settings_menu.isVisible())
        if visible:
            self.reveal_controls()

    def escape_pressed(self):
        if hasattr(self, 'drawer') and self.drawer.opened:
            self.drawer.set_open(False)
        else:
            super().escape_pressed()

    def layout_chrome(self):
        super().layout_chrome()
        if hasattr(self, 'drawer'):
            self.drawer.relayout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'drawer'):
            self.drawer.relayout()
            if hasattr(self, 'caption_overlay') and self.caption_overlay.preview:
                self.set_caption_preview(True)
            self.raise_chrome()

    def eventFilter(self, obj, event):
        if hasattr(self, 'drawer'):
            if event.type() == QEvent.Type.MouseButtonPress:
                if obj in (self.picture, self.picture.viewport(), self) and self.drawer.opened:
                    self.drawer.set_open(False)
        return super().eventFilter(obj, event)

    def show_wait(self, *args, **kwargs):
        super().show_wait(*args, **kwargs)
        if hasattr(self, 'drawer') and self.drawer.isVisible():
            self.drawer.raise_()
            self.raise_chrome()

    def session_state(self, state):
        if state == 'starting':
            self.drawer.set_locked_open(False)
            self.drawer.set_open(False)
            self.show_wait()
        elif state == 'idle':
            self.set_timeline_active(False)
            self._chrome_visible = True
            self.sync_chrome_visibility()
            self.show_wait(visible=False)
            self.drawer.set_locked_open(True)
        self.set_paused(state == 'paused')
        self.settings_visibility(self.drawer.opened)
        self.pause_button.setEnabled(state in ('preparing', 'playing', 'paused'))
        self.stop_button.setEnabled(state not in ('idle', 'stopping'))
        self.resolution_action.setEnabled(self.panel.resolution.isEnabled())
        self.controls.show()
        if state in ('playing', 'paused'):
            self.reveal_controls()




    def resolution_changed(self, qualities, selected, actual):
        self.panel.resolution.cancel()
        self.panel.resolution.set_formats(qualities, selected, actual)
        self.panel.resolution.setEnabled(self.controller.state in {'playing', 'paused'})
        self.update_resolution_action(self.panel.resolution.label())
        self.resolution_action.setEnabled(self.panel.resolution.isEnabled())


    def session_error(self, message):
        self.drawer.set_locked_open(True)
        self.drawer.set_open(True)
        if self._closing_app:
            QTimer.singleShot(0, self.close)

    def session_completed(self, result):
        if self._closing_app:
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event):
        if self.controller.running:
            self._closing_app = True
            self.controller.stop()
            event.ignore()
        else:
            self.panel.resolution.cancel()
            self.panel.save_preferences()
            event.accept()
