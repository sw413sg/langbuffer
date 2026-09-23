from synthetic import synthetic
"""Brief UI/control check with synthetic media, including manual caption offset."""
import hashlib
import json
import os
from types import SimpleNamespace
import time
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from live_translate import integrated
from live_translate import playback
from live_translate import transport as transport
from live_translate.pipeline import FrozenTimeline, ROOT
from synthetic import SyntheticWorker
from PySide6.QtCore import qInstallMessageHandler
from PySide6.QtGui import QFontDatabase, QFont
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QAudioOutput
from live_translate.integrated_window import IntegratedWindow
from live_translate.settings import DEFAULTS


def check_offset():
    cue = dict(id=0, start=2.0, end=4.0, text='Ejemplo', original='Example')
    for offset in (-1.0, 0.0, 1.0):
        timeline = FrozenTimeline(1)
        timeline.set_offset(offset)
        timeline.receive(1, [cue])
        assert timeline.tick(2+offset-.01) is None
        assert timeline.tick(2+offset).id == 0
        assert timeline.tick(4+offset) is None
    timeline = FrozenTimeline(1)
    timeline.receive(1, [cue, dict(cue, id=1, start=6, end=8)])
    active = timeline.tick(2)
    timeline.set_offset(1)
    assert timeline.tick(2.5) is active
    assert timeline.tick(4) is None
    assert timeline.tick(6.9) is None
    assert timeline.tick(7).id == 1
    timeline.receive(0, [dict(cue, id=3, start=7, end=8)])
    assert timeline.obsolete == 1


class MutedOutput(QAudioOutput):
    def setMuted(self, value):
        self.requested_mute = value
        super().setMuted(True)


def main():
    check_offset()
    qInstallMessageHandler(lambda *unused: None)
    app = QApplication([])
    for font in ('arial.ttf', 'segoeui.ttf', 'verdana.ttf'):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)
    app.setFont(QFont('Segoe UI', 10))
    args = SimpleNamespace(url=None, delay=5, seconds=0, stop_after=0, muted=True, auto_exit=False)
    store = integrated.MemorySettings(dict(DEFAULTS, delay=5))
    window = integrated.LocalWindow(args, store=store)
    original = integrated.LocalWindow(args, store=integrated.MemorySettings(store.values))
    window.show()
    original.show()
    window.open_section('subtitles')
    original.open_section('subtitles')
    window.setFocus()
    original.setFocus()
    app.processEvents()
    # Text now follows the output language. Keep checking the shared layout and
    # appearance contract without expecting Spanish and English pixels to match.
    same_panel_geometry = window.drawer.geometry() == original.drawer.geometry()
    assert same_panel_geometry, 'subtitles_panel_geometry_difference'
    assert window.panel.appearance_values() == original.panel.appearance_values()
    original.close()
    panel, engine = window.panel, window.engine
    panel.font_family.setCurrentIndex(panel.font_family.findData('Verdana'))
    panel.font_size.setCurrentIndex(panel.font_size.findData(30))
    panel.opacity.setValue(4)
    panel.position_x.setValue(-25)
    panel.position_y.setValue(10)
    panel.show_original.setChecked(True)
    assert window.caption_overlay.options['font_family'] == 'Verdana'
    assert window.caption_overlay.options['font_size'] == 30
    assert window.caption_overlay.options['background_opacity_level'] == 4
    assert window.caption_overlay.preview
    window.drawer.set_open(False, force=True)
    assert not window.caption_overlay.preview
    window.fullscreen_button.click()
    assert window.isFullScreen()
    window.fullscreen_button.click()
    assert not window.isFullScreen()
    assert window.resolution_action.isEnabled()
    window.open_section('settings')
    panel.content_url.setText('https://x.com/i/broadcasts/1AxRnZbVpjaxl')

    def fetch(url, headers, limit):
        if url == 'https://synthetic.test/list':
            text = '#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n'
            text += ''.join(f'#EXTINF:2,\n{i}.ts\n' for i in range(12))
            return (text+'#EXT-X-ENDLIST\n').encode(), url
        index = int(url.rsplit('/', 1)[1].split('.')[0])
        return synthetic(2, offset=index*2, height=180)[0].data, url

    def until(predicate, timeout=25):
        deadline = time.monotonic()+timeout
        while not predicate() and time.monotonic() < deadline:
            app.processEvents()
            if engine.running and engine.metrics['errors']:
                raise AssertionError(engine.metrics['errors'])
            time.sleep(.01)
        assert predicate(), 'check_timeout'

    results = []
    engine.completed.connect(lambda result: results.append(dict(result)))
    with patch.object(playback, 'LocalWorker', SyntheticWorker), \
            patch.object(playback, 'QAudioOutput', MutedOutput), \
            patch.object(transport, 'resolve_stream', return_value={'url': 'https://synthetic.test/list'}), \
            patch.object(transport, 'fetch_bounded', side_effect=fetch):
        try:
            panel.start.click()
            until(lambda: engine.metrics['caption_visible_frames'] > 0)
            assert not window.drawer.opened
            assert engine.ahead_player.audioOutput() is None
            assert bytes(engine.audio.device().id()) == bytes(window.listening_device().id())
            window.volume_slider.setValue(23)
            assert abs(engine.audio.volume()-.23) < .001 and engine.audio.requested_mute is False
            window.volume_button.click()
            assert engine.audio.requested_mute is True
            window.open_section('subtitles')
            panel.caption_offset.setValue(10)
            assert panel.effective_caption_offset() == 1.0 and engine.timeline.offset == 1.0
            window.drawer.set_open(False, force=True)
            window.pause_button.click()
            assert window.controller.state == 'paused'
            until(lambda: engine.clock.is_paused())
            positions = (engine.player.position(), engine.ahead_player.position())
            deadline = time.monotonic()+.3
            until(lambda: time.monotonic() >= deadline, timeout=1)
            assert positions == (engine.player.position(), engine.ahead_player.position())
            window.pause_button.click()
            assert window.controller.state == 'playing'
            overlay = window.caption_overlay
            panel.content_url.setText('https://www.twitch.tv/hasanabi')
            panel.restart.click()
            until(lambda: engine.generation == 2 and engine.metrics['caption_visible_frames'] > 0)
            assert window.caption_overlay is overlay
            assert engine.timeline.offset == 1.0
            window.close()
            until(lambda: not engine.running)
            until(lambda: not window.isVisible(), timeout=2)
        finally:
            if engine.running:
                engine.finish('check_cleanup')
                deadline = time.monotonic()+20
                while engine.running and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(.01)
            window.close()
    assert len(results) == 2 and all(result['passed'] for result in results)
    reopened = integrated.LocalWindow(args, store=store)
    assert reopened.panel.caption_offset.value() == 10
    assert reopened.panel.font_family.currentData() == 'Verdana'
    reopened.close()
    report = dict(passed=True, subtitles_panel_geometry_preserved=same_panel_geometry,
        offset_advance_delay_and_freeze=True, volume_mute_and_device=True,
        preview_and_appearance=True, fullscreen=True, pause_resume=True, restart=True,
        close_during_playback=True, separate_preferences=True, synthetic_media=True, synthetic_asr_translation=True, sessions=results)
    (ROOT / 'outputs/local-asr-integrated-check.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
