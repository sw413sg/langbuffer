"""Controles del historial remoto; seleccionar no almacena el historial localmente."""
from PySide6.QtCore import Qt, Signal, QSize, QPointF
from PySide6.QtGui import QPainter, QPen, QColor
from PySide6.QtWidgets import QWidget, QSlider, QVBoxLayout, QLabel, QPushButton, QStyle
from .player_controls import player_icon


def duration_label(seconds):
    seconds = max(0, round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f'{hours}:{minutes:02}:{seconds:02}' if hours else f'{minutes}:{seconds:02}'


class SeekSlider(QSlider):
    navigation_keys = (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Home, Qt.Key.Key_End,
                       Qt.Key.Key_PageUp, Qt.Key.Key_PageDown)

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self._hover_x = None

    def handle_radius(self, handle_x):
        if self.isSliderDown() or (self._hover_x is not None and abs(self._hover_x-handle_x) <= 11):
            return 8
        return 6

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hovered = self.underMouse() or self.isSliderDown() or self.hasFocus()
        thickness = 5 if hovered else 3
        left, right, y = 5, self.width()-5, self.height()/2
        value = (self.sliderPosition()-self.minimum())/max(1, self.maximum()-self.minimum())
        x = left+(right-left)*value
        painter.setPen(QPen(QColor(255, 255, 255, 100), thickness))
        painter.drawLine(QPointF(left, y), QPointF(right, y))
        painter.setPen(QPen(QColor(255, 255, 255), thickness))
        painter.drawLine(QPointF(left, y), QPointF(x, y))
        if hovered and self.isEnabled():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(Qt.GlobalColor.white)
            radius = self.handle_radius(x)
            painter.drawEllipse(QPointF(x, y), radius, radius)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._hover_x = None
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._hover_x = event.position().x()
            self.setSliderDown(True)
            self.setValue(QStyle.sliderValueFromPosition(self.minimum(), self.maximum(),
                          round(event.position().x())-5, max(1, self.width()-10)))
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self._hover_x = event.position().x()
        self.update()
        if self.isSliderDown():
            self.setValue(QStyle.sliderValueFromPosition(self.minimum(), self.maximum(),
                          round(event.position().x())-5, max(1, self.width()-10)))
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            self.setSliderDown(False)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in self.navigation_keys and not self.isSliderDown():
            self.setSliderDown(True)
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        super().keyReleaseEvent(event)
        if event.key() in self.navigation_keys:
            self.setSliderDown(False)

    def wheelEvent(self, event):
        # Evitar cambiar la posición visual sin confirmar un salto.
        event.ignore()


class PlaybackTimeline(QWidget):
    seek_requested = Signal(object)

    def __init__(self, parent):
        super().__init__(parent)
        self.snapshot = None
        self.drag_snapshot = None
        self.busy = False
        self.setObjectName('playbackTimeline')
        self.setStyleSheet('QSlider {background: transparent;}')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.slider = SeekSlider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(0, 10000)
        self.slider.setAccessibleName('Momento de la emisión')
        layout.addWidget(self.slider)
        # La fila del reproductor aloja estas etiquetas; aquí solo va la línea.
        self.available = QLabel('Consultando historial…', self)
        self.position = QLabel('', self)
        self.live_button = QPushButton('EN VIVO', self)
        self.live_button.setIcon(player_icon('live'))
        self.live_button.setIconSize(QSize(10, 10))
        self.live_button.setAccessibleName('Volver al directo con el retraso configurado')
        self.live_button.setToolTip('Volver al directo y preparar de nuevo el buffer')
        self.slider.sliderPressed.connect(self.begin_drag)
        self.slider.valueChanged.connect(self.describe_selection)
        self.slider.sliderReleased.connect(self.commit)
        self.live_button.clicked.connect(lambda: self.seek_requested.emit('live'))
        self.set_busy(False)
        self.hide()

    def begin_drag(self):
        self.drag_snapshot = self.snapshot

    def selected_position(self):
        source = self.drag_snapshot or self.snapshot
        if source is None:
            return None
        return source.start+(source.end-source.start)*self.slider.value()/10000

    def describe_selection(self):
        source = self.drag_snapshot or self.snapshot
        if source is not None and self.slider.isSliderDown():
            self.position.setText(duration_label(self.selected_position()-source.start))

    def commit(self):
        value = self.selected_position()
        self.drag_snapshot = None
        if value is not None and not self.busy:
            self.seek_requested.emit(value)

    def set_busy(self, busy):
        self.busy = busy
        self.slider.setEnabled(self.snapshot is not None and len(self.snapshot.segments) > 1 and not busy)
        self.live_button.setEnabled(self.snapshot is not None and self.snapshot.live and not busy)

    def reset(self):
        self.snapshot = self.drag_snapshot = None
        self.available.setText('Consultando historial…')
        self.position.clear()
        self.live_button.setText('EN VIVO')
        self.live_button.show()
        self.slider.setValue(0)
        self.set_busy(False)

    def update_window(self, snapshot, position, delay):
        self.snapshot = snapshot
        self.set_busy(self.busy)
        self.available.setText('/ '+duration_label(snapshot.end-snapshot.start))
        self.slider.setToolTip(duration_label(snapshot.end-snapshot.start)+' disponibles')
        self.live_button.setVisible(snapshot.live)
        if self.slider.isSliderDown():
            return
        self.slider.blockSignals(True)
        self.slider.setValue(round(max(0, min(1, (position-snapshot.start)/max(.001, snapshot.end-snapshot.start)))*10000))
        self.slider.blockSignals(False)
        self.position.setText(duration_label(position-snapshot.start))
        self.position.setToolTip('A '+duration_label(max(0, snapshot.end-position))+' del extremo disponible')
        at_live = snapshot.live and position >= snapshot.live_start-delay-snapshot.target
        self.live_button.setIcon(player_icon('live' if at_live else 'behind'))
