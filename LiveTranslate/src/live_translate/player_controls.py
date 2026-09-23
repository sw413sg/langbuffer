"""Controles dibujados por la aplicación, basados en la referencia visual de X."""
import math
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QIcon, QPixmap, QPainter, QPen, QColor, QPolygonF, QLinearGradient
from PySide6.QtWidgets import QWidget


def player_icon(name):
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(2)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(Qt.GlobalColor.white, 2.2, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    if name == 'pause':
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(Qt.GlobalColor.white)
        painter.drawRoundedRect(QRectF(6, 5, 4, 14), 1, 1)
        painter.drawRoundedRect(QRectF(14, 5, 4, 14), 1, 1)
    elif name == 'play':
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(Qt.GlobalColor.white)
        painter.drawPolygon(QPolygonF([QPointF(7, 4), QPointF(20, 12), QPointF(7, 20)]))
    elif name == 'stop':
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(Qt.GlobalColor.white)
        painter.drawRoundedRect(QRectF(6, 6, 12, 12), 1.5, 1.5)
    elif name in ('volume', 'muted'):
        painter.drawPolygon(QPolygonF([QPointF(3, 9), QPointF(7, 9), QPointF(12, 5),
            QPointF(12, 19), QPointF(7, 15), QPointF(3, 15)]))
        if name == 'volume':
            painter.drawArc(QRectF(8, 6, 11, 12), -65*16, 130*16)
            painter.drawArc(QRectF(5, 2, 18, 20), -55*16, 110*16)
        else:
            painter.drawLine(QPointF(3, 21), QPointF(21, 3))
    elif name == 'settings':
        points = []
        for index in range(32):
            angle = math.pi*index/16-math.pi/8
            radius = 9 if index % 4 in (1, 2) else 7
            points.append(QPointF(12+math.cos(angle)*radius, 12+math.sin(angle)*radius))
        painter.drawPolygon(QPolygonF(points))
        painter.drawEllipse(QPointF(12, 12), 3, 3)
    elif name in ('fullscreen', 'restore'):
        if name == 'fullscreen':
            lines = ((4, 10, 4, 4), (4, 4, 10, 4), (4, 4, 10, 10),
                     (20, 14, 20, 20), (20, 20, 14, 20), (20, 20, 14, 14))
        else:
            lines = ((4, 10, 10, 10), (10, 10, 10, 4), (4, 4, 10, 10),
                     (20, 14, 14, 14), (14, 14, 14, 20), (20, 20, 14, 14))
        for x1, y1, x2, y2 in lines:
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    elif name in ('live', 'behind'):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#f91880' if name == 'live' else '#a0a0a0'))
        painter.drawEllipse(QPointF(12, 12), 4, 4)
    painter.end()
    return QIcon(pixmap)


class PlayerControls(QWidget):
    def paintEvent(self, event):
        painter = QPainter(self)
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0, QColor(0, 0, 0, 0))
        gradient.setColorAt(1, QColor(0, 0, 0, 204))
        painter.fillRect(self.rect(), gradient)
