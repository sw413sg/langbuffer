"""Video Qt en una escena compartida, compatible con subtítulos superpuestos."""
from PySide6.QtCore import Qt, QRectF, QSizeF
from PySide6.QtGui import QPainter, QPixmap, QTransform
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QFrame, QGraphicsPixmapItem
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem


class ComposedVideo(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setBackgroundBrush(Qt.GlobalColor.black)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setScene(QGraphicsScene(self))
        self.video = QGraphicsVideoItem()
        self.scene().addItem(self.video)
        self.video.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        self.preview = QGraphicsPixmapItem()
        self.preview.setZValue(1)
        self.scene().addItem(self.preview)
        self.preview.hide()

    def show_preview(self, image):
        self.preview.setPixmap(QPixmap.fromImage(image))
        self.preview.show()
        self.fit_preview()

    def clear_preview(self):
        self.preview.hide()
        self.preview.setPixmap(QPixmap())

    def fit_preview(self):
        size = self.preview.pixmap().size()
        if size.isEmpty():
            return
        scale = min(self.viewport().width()/size.width(), self.viewport().height()/size.height())
        self.preview.setTransform(QTransform.fromScale(scale, scale))
        self.preview.setPos((self.viewport().width()-size.width()*scale)/2,
                            (self.viewport().height()-size.height()*scale)/2)

    def videoSink(self):
        return self.video.videoSink()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        size = QSizeF(self.viewport().size())
        self.video.setSize(size)
        self.setSceneRect(QRectF(0, 0, size.width(), size.height()))
        self.fit_preview()
