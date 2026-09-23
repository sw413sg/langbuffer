"""Selector de calidad y consulta cancelable fuera del hilo gráfico."""
import json
import sys
from pathlib import Path
from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QComboBox, QLabel
from .media_source import content_url


class ResolutionSelector(QWidget):
    requested = Signal(object)
    label_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.combo = QComboBox()
        self.combo.setAccessibleName('Resolución del video')
        self.combo.addItem('Máxima disponible', (0, 0))
        self.hint = QLabel('Pega un enlace para consultar sus resoluciones.')
        self.hint.setWordWrap(True)
        self.hint.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addWidget(self.combo)
        layout.addWidget(self.hint)
        self.setToolTip('Al cambiar se vuelve a llenar el retraso. X y Kick conservan el punto disponible; '
                        'Twitch vuelve al directo. Si está en pausa, se reanuda.')
        # ``activated`` no cubre todas las formas en que QComboBox cambia en
        # Windows (en particular, la rueda puede mover el índice sin activarlo).
        # Los cambios internos de catálogo bloquean señales en ``set_formats``,
        # por lo que escuchar el índice real del control no genera solicitudes
        # espurias al refrescar las variantes.
        self.combo.currentIndexChanged.connect(self._selected)
        self.url = None
        self.actual = None
        self.available = []
        self.process = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(600)
        self.timer.timeout.connect(self.query)

    def _selected(self, unused):
        self.actual = None
        self.label_changed.emit(self.label())
        self.requested.emit(self.quality())

    @staticmethod
    def normalize(value):
        if type(value) is int:
            return (value, 0) if 0 <= value <= 16384 else None
        if isinstance(value, dict):
            value = (value.get('height'), value.get('fps', 0))
        if isinstance(value, (tuple, list)) and len(value) == 2:
            height, fps = value
            if type(height) is int and type(fps) is int and 0 <= height <= 16384 and 0 <= fps <= 1000:
                return height, fps
        return None

    def quality(self):
        return self.normalize(self.combo.currentData()) or (0, 0)

    def index_of(self, value):
        quality = self.normalize(value)
        return next((index for index in range(self.combo.count())
                     if self.normalize(self.combo.itemData(index)) == quality), -1)

    def height(self):
        return self.quality()[0]

    def fps(self):
        return self.quality()[1]

    def quality_text(self, quality):
        height, fps = quality
        if not height:
            return 'Calidad'
        duplicate = sum(value[0] == height for value in self.available) > 1
        suffix = f' {fps} fps' if fps > 30 or (duplicate and fps not in (0, 30)) else ''
        return f'{height}p{suffix}'

    def label(self):
        selected = self.quality()
        value = self.actual or (selected if selected != (0, 0) else
                                self.available[0] if self.available else (0, 0))
        return self.quality_text(value)

    def cancel(self):
        self.timer.stop()
        process, self.process = self.process, None
        if process is not None:
            process.kill()

    def set_source(self, value, probe=True):
        try:
            url = content_url(value)
        except ValueError:
            url = None
        if self.url == url:
            return
        self.cancel()
        self.url = url
        self.set_formats([], 0)
        self.hint.setText('Consultando resoluciones…' if url else
                          'Pega un enlace para consultar sus resoluciones.')
        if url and probe:
            self.timer.start()

    def set_formats(self, formats, selected=None, actual=None):
        self.available = sorted({value for item in formats
                                 if (value := self.normalize(item)) is not None and value[0] > 0},
                                reverse=True)
        desired = self.quality() if selected is None else (self.normalize(selected) or (0, 0))
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem('Máxima disponible', (0, 0))
        for quality in self.available:
            self.combo.addItem(self.quality_text(quality), quality)
        # Una selección explícita no cambia silenciosamente si desaparece.
        if desired != (0, 0) and self.index_of(desired) < 0:
            self.combo.addItem(f'{self.quality_text(desired)} · no disponible', desired)
        self.combo.setCurrentIndex(max(0, self.index_of(desired)))
        self.combo.blockSignals(False)
        self.actual = self.normalize(actual)
        self.hint.setText(f'Calidad actual: {self.quality_text(self.actual)}' if self.actual else
                          'El cambio vuelve a preparar el retraso.' if formats else
                          'Las resoluciones se consultarán al iniciar.')
        self.label_changed.emit(self.label())

    def set_pending(self, value):
        quality = self.normalize(value) or (0, 0)
        target = self.quality_text(quality) if quality != (0, 0) else 'la máxima disponible'
        self.hint.setText(f'Cambiando a {target}…')
        self.label_changed.emit(self.quality_text(quality) if quality != (0, 0) else 'Máxima')

    def query(self):
        if not self.url or self.process is not None:
            return
        process = QProcess(self)
        self.process = process
        root = Path(__file__).resolve().parents[2]
        interpreter = Path(sys.executable)
        if interpreter.name.lower() == 'pythonw.exe':
            interpreter = interpreter.with_name('python.exe')
        env = QProcessEnvironment.systemEnvironment()
        env.insert('PYTHONPATH', str(root/'src'))
        process.setProcessEnvironment(env)
        process.setWorkingDirectory(str(root))
        process.setStandardErrorFile(QProcess.nullDevice())
        timeout = QTimer(process)
        timeout.setSingleShot(True)
        timeout.timeout.connect(process.kill)
        def done(*args):
            timeout.stop()
            if self.process is process:
                self.process = None
                try:
                    result = json.loads(bytes(process.readAllStandardOutput()))
                    formats = (result['qualities'] if 'qualities' in result else
                               result['resolutions'])
                    if not isinstance(formats, list):
                        raise ValueError()
                except (ValueError, KeyError, TypeError):
                    formats = []
                self.set_formats(formats)
            process.deleteLater()
        process.finished.connect(done)
        process.errorOccurred.connect(lambda error: done() if error == QProcess.ProcessError.FailedToStart else None)
        process.start(str(interpreter), ['-m', 'langbuffer.stream_formats', self.url])
        timeout.start(55000)
