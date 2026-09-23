"""Preferencias locales permitidas; sin historial de sesiones ni contenido."""
import json
import os
from pathlib import Path
import tempfile


DEFAULTS = dict(window='', source='', listener='', delay=10, caption_offset=0.0, duration=0, short_gaps=False,
                font_size=0, font_family='Arial', text_color='white', original_text_color='yellow',
                background_color='black',
                theme='dark', background_opacity=85, background_opacity_level=7, caption_width=86,
                text_alignment='center', caption_x=0, caption_y=0, always_on_top=False,
                auto_audio=True, show_original=False,
                font_size_mode='auto', caption_offset_calibrated=True)


def validated(data):
    result = dict(DEFAULTS)
    if not isinstance(data, dict):
        return result
    for key in ('window', 'source', 'listener'):
        value = data.get(key)
        if isinstance(value, str) and len(value) <= 512:
            result[key] = value
    for key, allowed in (('delay', range(1, 31)), ('duration', (0, 120, 600, 1800, 3600))):
        if type(data.get(key)) is int and data[key] in allowed:
            result[key] = data[key]
    caption_offset = data.get('caption_offset')
    if (type(caption_offset) in (int, float) and -5 <= caption_offset <= 5
            and caption_offset == caption_offset):
        result['caption_offset'] = round(float(caption_offset), 1)
    for key in ('short_gaps', 'always_on_top', 'auto_audio', 'show_original', 'caption_offset_calibrated'):
        if type(data.get(key)) is bool:
            result[key] = data[key]
    for key, low, high in (('font_size', 0, 40), ('background_opacity', 0, 100),
                           ('background_opacity_level', 0, 10), ('caption_width', 40, 100),
                           ('caption_x', -100, 100), ('caption_y', -100, 100)):
        if (type(data.get(key)) is int and low <= data[key] <= high
                and (key != 'font_size' or data[key] == 0 or data[key] >= 16)):
            result[key] = data[key]
    if data.get('theme') in ('dark', 'yellow', 'light'):
        result['theme'] = data['theme']
    for key, allowed in (('font_family', ('Arial', 'Segoe UI', 'Verdana')),
                         ('text_color', ('white', 'yellow', 'red')),
                         ('original_text_color', ('white', 'yellow', 'red')),
                         ('background_color', ('black', 'white', 'gray')),
                         ('text_alignment', ('left', 'center', 'right'))):
        if data.get(key) in allowed:
            result[key] = data[key]
    if data.get('font_size_mode') in ('auto', 'fixed'):
        result['font_size_mode'] = data['font_size_mode']
    for key in ('geometry', 'player_geometry', 'caption_geometry'):
        geometry = data.get(key)
        if (isinstance(geometry, list) and len(geometry) == 4
                and all(type(v) is int and abs(v) < 100000 for v in geometry)
                and geometry[2] >= 100 and geometry[3] >= 100):
            result[key] = list(geometry)
    return result


def fit_geometry(rect, screens):
    """Mantiene el rectángulo dentro de un monitor disponible."""
    if not screens:
        return rect
    x, y, w, h = rect
    cx, cy = x+w/2, y+h/2
    screen = next((s for s in screens if s[0] <= cx < s[0]+s[2] and s[1] <= cy < s[1]+s[3]), screens[0])
    sx, sy, sw, sh = screen
    w, h = min(w, sw), min(h, sh)
    return [max(sx, min(x, sx+sw-w)), max(sy, min(y, sy+sh-h)), w, h]


class SettingsStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else Path(__file__).resolve().parents[2]/'data/preferences.json'

    def load(self):
        try:
            if self.path.stat().st_size > 16384:
                raise ValueError('Preferencias demasiado grandes')
            raw = json.loads(self.path.read_text(encoding='utf-8'))
            return validated(raw), None
        except FileNotFoundError:
            return dict(DEFAULTS), None
        except (OSError, ValueError, UnicodeError):
            return dict(DEFAULTS), 'No se pudieron leer las preferencias; se usarán los valores iniciales.'

    def save(self, preferences):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                             prefix='preferences-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(validated(preferences), stream, ensure_ascii=False, indent=2)
                stream.flush()
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
