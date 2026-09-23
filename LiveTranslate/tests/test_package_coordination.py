"""Cross-process checks for shared models; no network or real media."""
from pathlib import Path
import hashlib
import io
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from live_translate import language_packages as packages
from live_translate.package_coordination import acquire_playback, changing_packages


class PackageCoordinationChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=packages.WORK, prefix='coordination-check-')
        self.root = Path(self.temporary.name).resolve()
        self.assertTrue(self.root.is_relative_to(packages.WORK.resolve()))

    def tearDown(self):
        self.assertTrue(self.root.is_relative_to(packages.WORK.resolve()))
        self.temporary.cleanup()

    def child(self, code):
        return subprocess.run([sys.executable, '-B', '-c', code, str(self.root)],
                              capture_output=True, text=True, timeout=10, check=True).stdout.strip()

    def test_two_processes_can_play_and_neither_can_change_packages(self):
        first = acquire_playback(self.root)
        second = acquire_playback(self.root)
        try:
            self.assertEqual(self.child(
                'from live_translate.package_coordination import acquire_playback; '
                'import sys; lock=acquire_playback(sys.argv[1]); print("ready"); lock.unlock()'), 'ready')
            with self.assertRaisesRegex(RuntimeError, '^language_package_in_use$'):
                with changing_packages(self.root):
                    pass
            with patch.object(packages, 'WORK', self.root):
                with self.assertRaisesRegex(RuntimeError, '^language_package_in_use$'):
                    packages.remove_package(packages.asr_package('small'))
        finally:
            second.unlock()
            first.unlock()
        with changing_packages(self.root):
            pass

    def test_new_package_can_install_during_playback_but_existing_one_cannot(self):
        payload = b'synthetic package only'
        spec = dict(id='tiny-fixture', kind='asr', label='Tiny fixture',
                    path=self.root/'models'/'tiny', repository='example/tiny',
                    revision='fixture', download_bytes=len(payload),
                    files={'model.bin': dict(bytes=len(payload),
                                             sha256=hashlib.sha256(payload).hexdigest())})
        class Response(io.BytesIO):
            status = 200
            headers = {'Content-Length': str(len(payload))}
            def geturl(self): return 'https://example.invalid/model.bin'

        lock = acquire_playback(self.root)
        try:
            with patch.object(packages, 'WORK', self.root), \
                    patch.object(packages.urllib.request, 'urlopen',
                                 side_effect=lambda *a, **k: Response(payload)):
                packages.install_packages([spec], threading.Event(), lambda event: None)
                self.assertEqual((spec['path']/'model.bin').read_bytes(), payload)
                # A present but incomplete package would be replaced, so the
                # active session excludes that mutation.
                (spec['path']/'model.bin').write_bytes(b'broken')
                with self.assertRaisesRegex(RuntimeError, '^language_package_in_use$'):
                    packages.install_packages([spec], threading.Event(), lambda event: None)
        finally:
            lock.unlock()

    def test_other_process_cannot_change_packages_during_playback(self):
        lock = acquire_playback(self.root)
        try:
            self.assertEqual(self.child(
                'from live_translate.package_coordination import changing_packages; '
                'import sys; '
                'exec("try:\\n with changing_packages(sys.argv[1]): pass\\nexcept RuntimeError as e: print(e)")'),
                'language_package_in_use')
        finally:
            lock.unlock()

    def test_active_mutation_blocks_new_playback(self):
        with changing_packages(self.root):
            self.assertEqual(self.child(
                'from live_translate.package_coordination import acquire_playback; '
                'import sys; '
                'exec("try:\\n acquire_playback(sys.argv[1])\\nexcept RuntimeError as e: print(e)")'),
                'language_packages_busy')
        lock = acquire_playback(self.root)
        lock.unlock()

    def test_dead_owner_does_not_leave_permanent_marker(self):
        self.child('from live_translate.package_coordination import acquire_playback; '
                   'import os,sys; lock=acquire_playback(sys.argv[1]); os._exit(0)')
        with changing_packages(self.root):
            pass
