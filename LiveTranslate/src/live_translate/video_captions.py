"""Subtítulos configurables dentro del área real de video."""
from PySide6.QtCore import Qt, QEvent, QRectF, QPointF
from PySide6.QtGui import QColor, QFont, QPainter, QTextLayout, QTextOption
from PySide6.QtWidgets import QWidget, QLabel


PREVIEW_TEXT = ('Así se verán los subtítulos en el ejemplo,\n'
                'con esto puedes comprobar la opacidad y el tamaño, etc.')
TEXT_COLORS = {'white': QColor('#ffffff'), 'yellow': QColor('#ffe66d'), 'red': QColor('#ff5a5f')}
BACKGROUND_COLORS = {'black': (0, 0, 0), 'white': (255, 255, 255), 'gray': (96, 96, 96)}


def horizontal_position(left, area_width, group_width, offset):
    """Mueve el bloque completo: −100 izquierda, 0 centro, +100 derecha."""
    return left + max(0, area_width-group_width)*(offset+100)/200


class VideoCaptions(QWidget):
    def __init__(self, video, *, font_size=0, show_original=False, **options):
        super().__init__(video)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.translation = QLabel(self)
        self.translation.hide()
        self.original_text = ''
        self.preview = False
        self.aspect = 16/9
        self.options = {}
        self.configure(font_size=font_size, show_original=show_original, **options)
        video.installEventFilter(self)
        self.setGeometry(video.rect())

    def configure(self, **options):
        defaults = dict(font_size=0, font_family='Arial', text_color='white', original_text_color='yellow',
                        background_color='black', background_opacity_level=7,
                        caption_width=86, text_alignment='center', caption_x=0,
                        caption_y=0, show_original=False)
        values = dict(defaults)
        values.update(self.options)
        values.update({key: value for key, value in options.items() if key in defaults})
        if values['font_size'] not in (0, 18, 24, 30, 36, 40):
            values['font_size'] = 0
        if values['font_family'] not in ('Arial', 'Segoe UI', 'Verdana'):
            values['font_family'] = 'Arial'
        if values['text_color'] not in TEXT_COLORS:
            values['text_color'] = 'white'
        if values['original_text_color'] not in TEXT_COLORS or values['original_text_color'] == values['text_color']:
            values['original_text_color'] = next(color for color in ('yellow', 'white', 'red')
                                                 if color != values['text_color'])
        if values['background_color'] not in BACKGROUND_COLORS:
            values['background_color'] = 'black'
        if values['text_alignment'] not in ('left', 'center', 'right'):
            values['text_alignment'] = 'center'
        for key, low, high, fallback in (('background_opacity_level', 0, 10, 7),
                                         ('caption_width', 40, 100, 86),
                                         ('caption_x', -100, 100, 0),
                                         ('caption_y', -100, 100, 0)):
            if type(values[key]) is not int or not low <= values[key] <= high:
                values[key] = fallback
        values['show_original'] = bool(values['show_original'])
        self.options = values
        self.update()

    @property
    def font_size(self):
        return self.options['font_size']

    @property
    def show_original(self):
        return self.options['show_original']

    def set_preview(self, enabled, *unused):
        self.preview = bool(enabled)
        self.reveal()
        self.update()

    def set_aspect(self, width, height):
        if width > 0 and height > 0 and self.aspect != width / height:
            self.aspect = width / height
            self.update()

    def set_captions(self, translation, original=''):
        if translation != self.translation.text() or original != self.original_text:
            self.translation.setText(translation)
            self.original_text = original
            self.update()

    def place(self, *unused, **kwargs):
        self.setGeometry(self.parentWidget().rect())
        self.raise_()

    def reveal(self):
        self.place()
        self.show()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.place()
        return False

    def _image_area(self):
        area = QRectF(self.rect())
        width = min(area.width(), area.height()*self.aspect)
        height = width/self.aspect
        area = QRectF((area.width()-width)/2, (area.height()-height)/2, width, height)
        if hasattr(self.parentWidget(), 'frame_rect'):
            area = QRectF(self.parentWidget().frame_rect())
        return area

    def paintEvent(self, event):
        primary = PREVIEW_TEXT if self.preview else self.translation.text()
        if not primary:
            return
        paragraphs = [(paragraph, False) for paragraph in primary.splitlines()]
        if not self.preview and self.show_original and self.original_text:
            paragraphs.extend((paragraph, True) for paragraph in self.original_text.splitlines())
        area = self._image_area()
        size = self.font_size or max(12, min(24, round(area.height()*.06)))
        font = QFont(self.options['font_family'])
        font.setPixelSize(size)
        block_width = max(1, area.width()*self.options['caption_width']/100)
        layouts = []
        total = 0.0
        for paragraph, original in paragraphs:
            layout = QTextLayout(paragraph, font)
            option = QTextOption()
            option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
            layout.setTextOption(option)
            layout.beginLayout()
            while True:
                line = layout.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(max(1, block_width-12))
                line.setPosition(QPointF(0, total))
                total += line.height()+4
            layout.endLayout()
            layouts.append((layout, original))
        content_width = max((line.naturalTextWidth()+12 for layout, unused in layouts
                             for line in (layout.lineAt(index) for index in range(layout.lineCount()))), default=1)
        group_width = min(block_width, content_width)
        left = horizontal_position(area.left(), area.width(), group_width, self.options['caption_x'])
        bottom_margin = max(12, area.height()*.05)
        base_top = area.bottom()-bottom_margin-total
        vertical = self.options['caption_y']
        if vertical >= 0:
            top = base_top - max(0, base_top-area.top())*(vertical/100)
        else:
            lowest = area.bottom()-total
            top = base_top + max(0, lowest-base_top)*(-vertical/100)
        top = max(area.top(), min(area.bottom()-total, top))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipRect(area)
        r, g, b = BACKGROUND_COLORS[self.options['background_color']]
        background = QColor(r, g, b, round(self.options['background_opacity_level']*255/10))
        for layout, original in layouts:
            painter.setPen(TEXT_COLORS[self.options['original_text_color'] if original else self.options['text_color']])
            for index in range(layout.lineCount()):
                line = layout.lineAt(index)
                if self.options['text_alignment'] == 'left':
                    x = left+6
                elif self.options['text_alignment'] == 'right':
                    x = left+group_width-line.naturalTextWidth()-6
                else:
                    x = left+(group_width-line.naturalTextWidth())/2
                y = top+line.y()
                if background.alpha():
                    painter.fillRect(QRectF(x-6, y-2, line.naturalTextWidth()+12, line.height()+4), background)
                line.draw(painter, QPointF(x, top))
