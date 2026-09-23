"""Ventana superpuesta para cada opción de la rueda."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter, QColor, QImage
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea


PANEL_STYLE = '''
QWidget { color: #f1f4f8; font: 12px "Segoe UI"; background: transparent; }
QLabel { background: transparent; }
QLineEdit, QComboBox, QSpinBox { background: rgba(255,255,255,18); border: 1px solid rgba(255,255,255,24);
    border-radius: 5px; padding: 7px 8px; min-height: 18px; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #91b9ed; }
QComboBox QAbstractItemView { background: #202733; color: white; selection-background-color: #3a516e; }
QPushButton { background: rgba(255,255,255,22); border: 1px solid rgba(255,255,255,24);
    border-radius: 5px; padding: 8px; }
QPushButton:hover { background: rgba(255,255,255,40); }
QPushButton:focus { border-color: #91b9ed; }
QPushButton#languageManagerButton, QPushButton#restartSession {
    background: #555b63; color: #f1f4f8; border: 1px solid #737b85; font-weight: 600; }
QPushButton#languageManagerButton:hover, QPushButton#restartSession:hover { background: #69717b; }
QPushButton#startSession { background: #33965b; color: white; border: 1px solid #45af6e; font-weight: 600; }
QPushButton#startSession:hover { background: #42ad6b; }
QPushButton#stopSession { background: #a93b3b; color: white; border: 1px solid #c54d4d; font-weight: 600; }
QPushButton#stopSession:hover { background: #bf4848; }
QWidget:disabled { color: #88919e; }
QPushButton#startSession:disabled { background: rgba(255,255,255,20); color: #88919e; }
QPushButton#restartSession:disabled { background: #393e44; color: #9da5ad; border-color: #4b525a; }
QPushButton#stopSession:disabled { background: #954040; color: #e5c4c4; border-color: #aa4b4b; }
QSlider::groove:horizontal { height: 4px; background: rgba(255,255,255,35); border-radius: 2px; }
QSlider::sub-page:horizontal { background: #b6d1f5; border-radius: 2px; }
QSlider::handle:horizontal { background: #e2edfc; width: 12px; margin: -4px 0; border-radius: 6px; }
QScrollArea { border: none; }
QScrollBar:vertical { width: 6px; background: transparent; }
QScrollBar::handle:vertical { background: #667080; border-radius: 3px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
'''


class PanelDrawer(QWidget):
    visibility_changed = Signal(bool)

    def __init__(self, host, panel):
        super().__init__(host)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        panel.start.setObjectName('startSession')
        panel.restart.setObjectName('restartSession')
        panel.stop.setObjectName('stopSession')
        self.setStyleSheet(PANEL_STYLE)
        self.panel = panel
        self.body = QWidget(self)
        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(22, 8, 14, 16)
        header = QHBoxLayout()
        self.title = QLabel('Configuración')
        self.title.setStyleSheet('font-family: "Arial"; font-size: 17px; font-weight: bold; color: #e5edfa;')
        header.addWidget(self.title)
        header.addStretch()
        self.hide_button = QPushButton('×')
        self.hide_button.setFixedSize(30, 30)
        self.hide_button.setStyleSheet('font-size: 24px; border: none; background: transparent; padding: 0;')
        self.hide_button.setAccessibleName('Ocultar panel')
        self.hide_button.setToolTip('Ocultar panel · Esc')
        self.hide_button.clicked.connect(lambda: self.set_open(False))
        header.addWidget(self.hide_button)
        layout.addLayout(header)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(panel)
        self.scroll.viewport().setAutoFillBackground(False)
        layout.addWidget(self.scroll)
        panel.setAutoFillBackground(False)
        self._background = None
        self.opened = True
        self.locked_open = False
        panel.section_changed.connect(self.set_section)

    def relayout(self):
        host = self.parentWidget()
        content = host.content_rect()
        width = min(540, host.width()-16)
        available_height = max(130, content.height()-136)
        if self.panel.section == 'settings':
            desired_height = max(270, self.panel.pages.currentWidget().sizeHint().height()+64)
            height = min(320, available_height, desired_height)
        else:
            height = min(540, available_height)
        self.setGeometry(host.width()-width-8, max(content.top()+56, host.height()-height-80), width, height)
        self.body.setGeometry(0, 0, width, height)

    def set_open(self, opened, *, animate=True, force=False):
        if not opened and self.locked_open and not force:
            return
        if opened == self.opened and animate:
            return
        self.opened = opened
        if not opened:
            self.panel.close_preview()
        self.setVisible(opened)
        self.relayout()
        if opened:
            if self.panel.section == 'subtitles':
                self.panel.preview_requested.emit(True)
            self.raise_()
        if not opened:
            focus = self.focusWidget()
            if focus is not None:
                focus.clearFocus()
            self.parentWidget().setFocus()
        self.visibility_changed.emit(opened)
        self.parentWidget().raise_chrome()

    def set_section(self, section):
        self.title.setText({'settings': 'Configuración', 'subtitles': 'Subtítulos'}.get(section, 'Configuración'))
        self._background = None
        self.relayout()

    def set_locked_open(self, locked):
        self.locked_open = locked
        self.hide_button.setEnabled(not locked)
        if locked:
            self.set_open(True)

    def paintEvent(self, event):
        if self._background is None or self._background.size() != self.size():
            self._background = self.make_background()
        painter = QPainter(self)
        painter.drawImage(0, 0, self._background)

    def make_background(self):
        background = QImage(self.size(), QImage.Format.Format_ARGB32_Premultiplied)
        background.fill(Qt.GlobalColor.transparent)
        painter = QPainter(background)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 20, 20, 245))
        painter.drawRoundedRect(self.rect(), 8, 8)
        painter.end()
        return background
