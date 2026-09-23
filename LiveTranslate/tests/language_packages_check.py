"""Brief offline installer checks using tiny synthetic packages only."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch
import zipfile
import zlib

from live_translate import language_packages as packages
def fixture_payload(members):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr('fixture/'+name, data)
    return output.getvalue()


def fixture_spec(root, members, payload):
    return dict(id='fixture', kind='translation', source='xx', target='en',
                label='Paquete sintético', root='fixture', path=root/'installed',
                url='https://fixture.invalid/package', download_bytes=len(payload),
                bytes=len(payload), md5=hashlib.md5(payload).hexdigest(),
                files={name: dict(bytes=len(data), crc32=zlib.crc32(data))
                       for name, data in members.items()})


class Response(io.BytesIO):
    status = 200

    def __init__(self, payload, length=None):
        super().__init__(payload)
        self.headers = {'Content-Length': str(len(payload) if length is None else length)}

    def geturl(self):
        return 'https://fixture.invalid/package'


def main():
    result = dict(network_used=False, real_models_used=False, tests={})
    for source in packages.LANGUAGES:
        for target in packages.LANGUAGES:
            needed = packages.required_packages('small.en', source, target)
            assert needed[0]['model'] == ('small.en' if source == 'en' else 'small')
            route = needed[1:]
            expected = 0 if source == target else 1 if 'en' in (source, target) else 2
            assert len(route) == expected
            if route:
                assert route[0]['source'] == source and route[-1]['target'] == target
            if len(route) == 2:
                assert route[0]['target'] == route[1]['source'] == 'en'
            for item in needed:
                assert item['download_bytes'] >= 0
                if item['kind'] == 'translation' and not item.get('readonly'):
                    assert 'sentencepiece.model' in item['files']
    result['tests']['all_81_routes'] = True
    assert packages.effective_asr_model('small', 'en') == 'small.en'
    result['tests']['english_model_normalized'] = True

    with tempfile.TemporaryDirectory(dir=packages.WORK, prefix='language-package-check-') as temporary:
        root = Path(temporary).resolve()
        assert root.is_relative_to(packages.WORK.resolve())
        with patch.object(packages, 'WORK', root):
            members = {'model/model.bin': b'synthetic model bytes',
                       'sentencepiece.model': b'synthetic tokenizer bytes',
                       'metadata.json': b'{"from_code":"xx","to_code":"en"}'}
            payload = fixture_payload(members)
            spec = fixture_spec(root, members, payload)
            events = []

            def progress(event):
                events.append(event)
                if event['phase'] != 'ready':
                    assert not spec['path'].exists()
                    assert not packages.package_ready(spec)

            with patch.object(packages.urllib.request, 'urlopen',
                              side_effect=lambda *a, **k: Response(payload)) as network:
                packages.install_packages([spec], threading.Event(), progress)
                assert network.call_count == 1
            assert packages.package_ready(spec)
            assert events[-1]['phase'] == 'ready' and events[-1]['done'] == len(payload)
            assert (spec['path']/'model/model.bin').read_bytes() == members['model/model.bin']
            manifest = json.loads((spec['path']/'download-manifest.json').read_text())
            assert manifest['archive_sha256'] == hashlib.sha256(payload).hexdigest()
            assert not list((root/'language-downloads').iterdir())
            result['tests']['atomic_verified_install'] = True

            with patch.object(packages.urllib.request, 'urlopen',
                              side_effect=AssertionError('unexpected download')) as network:
                packages.install_packages([spec], threading.Event(), lambda event: None)
                assert not network.called
            result['tests']['installed_package_reused'] = True

            cancelled_spec = dict(spec, path=root/'cancelled')
            cancel = threading.Event()

            def cancel_progress(event):
                if event['phase'] == 'download' and event['done']:
                    cancel.set()

            with patch.object(packages.urllib.request, 'urlopen',
                              side_effect=lambda *a, **k: Response(payload)):
                try:
                    packages.install_packages([cancelled_spec], cancel, cancel_progress)
                except packages.DownloadCancelled:
                    pass
                else:
                    raise AssertionError('cancellation ignored')
            assert not cancelled_spec['path'].exists()
            assert not list((root/'language-downloads').iterdir())
            result['tests']['cancel_during_download_cleans_partial'] = True

            original = {str(path.relative_to(spec['path'])): path.read_bytes()
                        for path in spec['path'].rglob('*') if path.is_file()}
            changed = dict(spec, md5='0'*32)
            for case, response in [('wrong_hash', lambda: Response(payload)),
                                   ('wrong_size', lambda: Response(payload[:-1]))]:
                with patch.object(packages.urllib.request, 'urlopen',
                                  side_effect=lambda *a, **k: response()):
                    try:
                        packages.install_packages([changed], threading.Event(), lambda event: None)
                    except RuntimeError as error:
                        assert str(error) in ('language_package_integrity_failed',
                                              'language_package_size_changed')
                    else:
                        raise AssertionError('corrupt package accepted')
                assert original == {str(path.relative_to(spec['path'])): path.read_bytes()
                                    for path in spec['path'].rglob('*') if path.is_file()}
                assert packages.package_ready(spec)
                assert not list((root/'language-downloads').iterdir())
                result['tests'][case+'_preserves_previous_package'] = True

            malicious_members = {'../outside': b'not extracted'}
            malicious_payload = fixture_payload(malicious_members)
            malicious_spec = fixture_spec(root, malicious_members, malicious_payload)
            malicious_spec['path'] = root/'malicious'
            with patch.object(packages.urllib.request, 'urlopen',
                              side_effect=lambda *a, **k: Response(malicious_payload)):
                try:
                    packages.install_packages([malicious_spec], threading.Event(), lambda event: None)
                except RuntimeError as error:
                    assert str(error) == 'language_package_member_invalid'
                else:
                    raise AssertionError('path traversal accepted')
            assert not malicious_spec['path'].exists()
            assert not list(root.rglob('outside'))
            assert not list((root/'language-downloads').iterdir())
            result['tests']['archive_path_traversal_rejected'] = True

            fixture_pins = {name: dict(bytes=7, sha256=hashlib.sha256(b'fixture').hexdigest())
                            for name in packages.BUNDLED_OPUS_FILES}
            with patch.object(packages, 'BUNDLED_OPUS_FILES', fixture_pins):
                legacy = root/'translation/opus-en-es'
                legacy.mkdir(parents=True)
                for name in ('model.bin', 'source.spm', 'target.spm', 'config.json'):
                    (legacy/name).write_bytes(b'fixture')
                assert packages._legacy_translation() is None
                fallback = packages.translation_route('en', 'es')[0]
                assert fallback['engine'] == 'argos'
                assert 'sentencepiece.model' in fallback['files']
                (legacy/'shared_vocabulary.json').write_bytes(b'fixture')
                existing = packages._legacy_translation()
                assert existing['readonly'] and packages.package_ready(existing)
            result['tests']['incomplete_legacy_uses_downloadable_fallback'] = True

    result['passed'] = all(result['tests'].values())
    destination = packages.ROOT/'outputs/local-asr-language-packages-check.json'
    destination.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
