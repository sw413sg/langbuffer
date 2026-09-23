"""Offline language inventory/removal checks confined to tiny temporary fixtures."""
from contextlib import ExitStack
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from langbuffer import language_packages as packages
class PackageRemovalChecks(unittest.TestCase):
    def setUp(self):
        self.owner = packages.WORK.resolve()
        self.temporary = tempfile.TemporaryDirectory(dir=self.owner, prefix='package-removal-check-')
        self.root = Path(self.temporary.name).resolve()
        self.assertTrue(self.root.is_relative_to(self.owner))
        self.work = self.root/'prototype'
        self.work.mkdir()
        self.patches = ExitStack()
        self.patches.enter_context(patch.object(packages, 'ROOT', self.root))
        self.patches.enter_context(patch.object(packages, 'WORK', self.work))
        self.patches.enter_context(patch.object(packages, 'BUNDLED_OPUS_FILES', {
            name: dict(bytes=7, sha256=hashlib.sha256(b'fixture').hexdigest())
            for name in packages.BUNDLED_OPUS_FILES}))
        catalog = copy.deepcopy(packages.CATALOG)
        catalog['asr_small']['files'] = {'model.bin': {'bytes': 1, 'sha256': 'fixture'}}
        self.patches.enter_context(patch.object(packages, 'CATALOG', catalog))
        pins = {name: dict(revision='fixture', files={'model.bin': (1, 'fixture')})
                for name in ('small.en', 'base.en')}
        self.patches.enter_context(patch.object(packages, 'PINS', pins))
        self.patches.enter_context(patch.object(packages.urllib.request, 'urlopen',
                                              side_effect=AssertionError('network forbidden')))

    def tearDown(self):
        self.patches.close()
        # TemporaryDirectory's recursive cleanup is restricted to this verified root.
        self.assertEqual(self.root, Path(self.temporary.name).resolve())
        self.assertTrue(self.root.is_relative_to(self.owner))
        self.temporary.cleanup()

    def assert_rejected(self, spec, code='language_package_path_invalid'):
        with self.assertRaisesRegex(RuntimeError, '^'+code+'$'):
            packages.remove_package(spec)

    def write_asr(self, name='small'):
        spec = packages.asr_package(name)
        spec['path'].mkdir(parents=True)
        (spec['path']/'model.bin').write_bytes(b'x')
        self.assertTrue(packages.package_ready(spec))
        return spec

    def legacy(self, complete):
        root = self.work/'translation/opus-en-es'
        root.mkdir(parents=True)
        for name in ('model.bin', 'source.spm', 'target.spm', 'config.json'):
            (root/name).write_bytes(b'fixture')
        if complete:
            (root/'shared_vocabulary.json').write_bytes(b'fixture')
        return root

    def make_link(self, link, target):
        link.parent.mkdir(parents=True, exist_ok=True)
        if os.name == 'nt':
            # Directory junctions work without changing system settings or symlink privileges.
            result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(target)],
                                    capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        else:
            link.symlink_to(target, target_is_directory=True)

    def clear_link(self, link):
        if link.is_symlink():
            link.unlink()
        else:
            link.rmdir()  # Removes only the junction, never its target.

    def test_complete_inventory_keeps_shared_asr_once(self):
        catalog = packages.catalog_packages()
        self.assertEqual(len(catalog), 19)
        self.assertEqual(len({spec['id'] for spec in catalog}), 19)
        self.assertEqual([spec['model'] for spec in catalog if spec['kind'] == 'asr'],
                         ['small.en', 'base.en', 'small'])
        self.assertEqual({(spec['source'], spec['target']) for spec in catalog
                          if spec['kind'] == 'translation'}, set(packages._ARGOS))
        self.assertTrue(all(not packages.package_present(spec) for spec in catalog))

    def test_remove_shared_asr_updates_all_affected_languages(self):
        spec = self.write_asr('small')
        english = self.write_asr('small.en')
        for language in packages.LANGUAGES:
            if language != 'en':
                needed = packages.required_packages('small.en', language, language)
                self.assertEqual([item['id'] for item in needed], [spec['id']])
                self.assertTrue(packages.package_ready(needed[0]))
        self.assertTrue(packages.remove_package(spec))
        self.assertFalse(packages.package_present(spec))
        self.assertFalse(packages.remove_package(spec))
        for language in packages.LANGUAGES:
            if language != 'en':
                self.assertFalse(packages.package_ready(
                    packages.required_packages('small.en', language, language)[0]))
        self.assertTrue(packages.package_ready(english))

    def test_incomplete_owned_package_is_visible_and_removable(self):
        spec = packages._argos_translation_package('en', 'pt')
        spec['path'].mkdir(parents=True)
        (spec['path']/'unfinished.bin').write_bytes(b'partial')
        neighbor = spec['path'].parent/'unrelated'
        neighbor.mkdir()
        marker = neighbor/'keep.bin'
        marker.write_bytes(b'keep')
        self.assertTrue(packages.package_present(spec))
        self.assertFalse(packages.package_ready(spec))
        self.assertTrue(packages.remove_package(spec))
        self.assertEqual(marker.read_bytes(), b'keep')

    def test_complete_legacy_is_readonly_and_does_not_hide_owned_package(self):
        legacy_path = self.legacy(complete=True)
        catalog = packages.catalog_packages()
        self.assertEqual(len(catalog), 20)
        legacy = next(item for item in catalog if item.get('readonly'))
        owned = next(item for item in catalog
                     if (item['source'], item['target']) == ('en', 'es')
                     and not item.get('readonly'))
        self.assertEqual(packages.translation_route('en', 'es')[0]['id'], legacy['id'])
        self.assertTrue(packages.package_ready(legacy))
        self.assert_rejected(legacy, 'language_package_readonly')
        self.assert_rejected(dict(legacy, readonly=False), 'language_package_readonly')
        owned['path'].mkdir(parents=True)
        (owned['path']/'partial').write_bytes(b'x')
        self.assertTrue(packages.remove_package(owned))
        self.assertEqual((legacy_path/'model.bin').read_bytes(), b'fixture')
        self.assertTrue(packages.package_ready(legacy))

    def test_incomplete_legacy_visible_readonly_and_route_uses_fallback(self):
        self.legacy(complete=False)
        legacy = next(item for item in packages.catalog_packages() if item.get('readonly'))
        self.assertTrue(packages.package_present(legacy))
        self.assertFalse(packages.package_ready(legacy))
        self.assertEqual(packages.translation_route('en', 'es')[0]['engine'], 'argos')
        self.assert_rejected(legacy, 'language_package_readonly')

    def test_unrecognized_and_forged_paths_preserve_installation(self):
        spec = self.write_asr()
        invalid = [self.work, self.root, self.root/'outside', spec['path'].parent,
                   spec['path'].parent/'small.en', Path('models/small'),
                   spec['path'].parent/'small'/'..'/'small']
        for path in invalid:
            with self.subTest(path=path):
                self.assert_rejected(dict(spec, path=path))
        self.assert_rejected(dict(spec, kind='translation'))
        self.assert_rejected(dict(spec, id='arbitrary'), 'language_package_unrecognized')
        self.assert_rejected(dict(spec, id=[]), 'language_package_unrecognized')
        self.assert_rejected(None, 'language_package_unrecognized')
        self.assertEqual((spec['path']/'model.bin').read_bytes(), b'x')

    def test_known_location_occupied_by_file_is_not_deleted(self):
        spec = packages.asr_package('small')
        spec['path'].parent.mkdir(parents=True)
        spec['path'].write_bytes(b'not a package directory')
        self.assertTrue(packages.package_present(spec))
        self.assert_rejected(spec)
        self.assertEqual(spec['path'].read_bytes(), b'not a package directory')

    def test_package_root_link_rejected(self):
        spec = packages.asr_package('small')
        target = self.root/'outside'
        target.mkdir()
        (target/'keep').write_bytes(b'keep')
        self.make_link(spec['path'], target)
        try:
            self.assertTrue(packages.package_present(spec))
            self.assert_rejected(spec)
            self.assertEqual((target/'keep').read_bytes(), b'keep')
        finally:
            self.clear_link(spec['path'])

    def test_parent_link_rejected(self):
        spec = packages.asr_package('small')
        target = self.root/'outside'
        (target/'small').mkdir(parents=True)
        (target/'small'/'keep').write_bytes(b'keep')
        self.make_link(spec['path'].parent, target)
        try:
            self.assert_rejected(spec)
            self.assertEqual((target/'small'/'keep').read_bytes(), b'keep')
        finally:
            self.clear_link(spec['path'].parent)

    def test_descendant_link_rejected_before_any_deletion(self):
        spec = self.write_asr()
        target = self.root/'outside'
        target.mkdir()
        (target/'keep').write_bytes(b'keep')
        link = spec['path']/'nested'
        self.make_link(link, target)
        try:
            self.assert_rejected(spec)
            self.assertEqual((spec['path']/'model.bin').read_bytes(), b'x')
            self.assertEqual((target/'keep').read_bytes(), b'keep')
        finally:
            self.clear_link(link)

    def test_active_installation_prevents_removal(self):
        spec = self.write_asr()
        with packages._INSTALL_LOCK:
            self.assert_rejected(spec, 'language_download_already_running')
        self.assertTrue(packages.package_ready(spec))

    def test_removal_holds_lock_against_installation(self):
        spec = self.write_asr()
        entered = threading.Event()
        release = threading.Event()
        actual_rmtree = packages.shutil.rmtree
        outcomes = []

        def held_remove(path):
            entered.set()
            if not release.wait(5):
                raise AssertionError('lock check timed out')
            actual_rmtree(path)

        def remove():
            try:
                outcomes.append(packages.remove_package(spec))
            except Exception as error:
                outcomes.append(error)

        with patch.object(packages.shutil, 'rmtree', side_effect=held_remove):
            worker = threading.Thread(target=remove)
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaisesRegex(RuntimeError, '^language_download_already_running$'):
                    packages.install_packages([], threading.Event(), lambda event: None)
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcomes, [True])
        self.assertFalse(packages._INSTALL_LOCK.locked())


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PackageRemovalChecks)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    summary = dict(passed=result.wasSuccessful(), checks=result.testsRun,
                   network_used=False, real_models_deleted=False,
                   failures=len(result.failures), errors=len(result.errors))
    (packages.ROOT/'outputs/local-asr-package-removal-check.json').write_text(
        json.dumps(summary, indent=2), encoding='utf-8')
    raise SystemExit(0 if result.wasSuccessful() else 1)
