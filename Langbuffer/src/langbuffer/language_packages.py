"""Pinned language packages stored inside the application data directory.

The bundled OPUS package is read-only; downloadable packages remain removable."""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
import shutil
import stat
import threading
import urllib.request
import uuid
import zipfile

from langbuffer.model_catalog import PINS, BUNDLED_OPUS_FILES
from langbuffer.package_coordination import changing_packages, preparing_packages

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'data'
LANGUAGES = {'en': 'Inglés', 'es': 'Español', 'pt': 'Portugués',
             'fr': 'Francés', 'de': 'Alemán', 'it': 'Italiano',
             'ja': 'Japonés', 'ko': 'Coreano', 'zh': 'Chino simplificado'}
CATALOG = json.loads(Path(__file__).with_name('package_catalog.json').read_text(encoding='utf-8'))
_ARGOS = {(item['source'], item['target']): item for item in CATALOG['argos']}
_INSTALL_LOCK = threading.Lock()
_rejected_bundled_signature = None


def _bundled_signature():
    path = WORK/'translation/opus-en-es'
    try:
        return (str(path.resolve()), tuple((name, (path/name).stat().st_size,
                    (path/name).stat().st_mtime_ns) for name in BUNDLED_OPUS_FILES))
    except OSError:
        return None


def reject_bundled_package():
    """Remember a worker's integrity failure until the installation changes."""
    global _rejected_bundled_signature
    _rejected_bundled_signature = _bundled_signature()


def bundled_package_rejected():
    return (_rejected_bundled_signature is not None
            and _rejected_bundled_signature == _bundled_signature())


class DownloadCancelled(RuntimeError):
    def __init__(self):
        super().__init__('language_download_cancelled')


def _cancelled(cancel):
    if cancel.is_set():
        raise DownloadCancelled()


def effective_asr_model(name, source):
    if source not in LANGUAGES:
        raise ValueError('unsupported_source_language')
    if name not in ('small.en', 'base.en', 'small'):
        raise ValueError('unsupported_local_asr_model')
    return ('small.en' if name == 'small' else name) if source == 'en' else 'small'


def asr_package(name):
    if name == 'small':
        data = CATALOG['asr_small']
    elif name in PINS:
        pin = PINS[name]
        data = dict(repository=f'Systran/faster-whisper-{name}',
                    revision=pin['revision'], license='MIT',
                    files={key: dict(bytes=value[0], sha256=value[1])
                           for key, value in pin['files'].items()})
    else:
        raise ValueError('unsupported_local_asr_model')
    return dict(data, id='asr-'+name, kind='asr', model=name,
                label='Reconocimiento '+name+(' · multilingüe' if name == 'small' else ''),
                path=WORK/'models'/name, model_path=WORK/'models'/name,
                source='*' if name == 'small' else 'en', target=None,
                download_bytes=sum(item['bytes'] for item in data['files'].values()))


def _legacy_translation(include_incomplete=False):
    path = WORK/'translation/opus-en-es'
    complete = all((path/name).is_file() for name in BUNDLED_OPUS_FILES)
    if not complete and not (include_incomplete and os.path.lexists(path)):
        return None
    spec = dict(id='opus-en-es-existing', kind='translation', engine='opus',
                source='en', target='es', label='Traducción Inglés → Español',
                download_bytes=0, path=path, model_path=path, readonly=True,
                tokenizer='sentencepiece', tokenizer_path=path/'source.spm',
                target_tokenizer_path=path/'target.spm', target_prefix=None,
                files=BUNDLED_OPUS_FILES)
    return spec if include_incomplete or package_ready(spec) else None


def _translation_package(source, target, verify=False):
    if (source, target) == ('en', 'es'):
        legacy = _legacy_translation()
        if legacy is not None:
            if not verify or verify_bundled_package(legacy):
                return legacy
    return _argos_translation_package(source, target)


def _argos_translation_package(source, target):
    data = _ARGOS[source, target]
    path = WORK/'language-packages'/(data['id']+'-'+data['version'])
    return dict(data, kind='translation', engine='argos', path=path,
                model_path=path/'model', tokenizer='sentencepiece',
                tokenizer_path=path/'sentencepiece.model',
                target_tokenizer_path=path/'sentencepiece.model', target_prefix=None,
                label=f'Traducción {LANGUAGES[source]} → {LANGUAGES[target]}',
                download_bytes=data['bytes'])


def translation_route(source, target, *, verify=False):
    if source not in LANGUAGES or target not in LANGUAGES:
        raise ValueError('unsupported_translation_language')
    if source == target:
        return []
    pairs = [(source, target)] if 'en' in (source, target) else [(source, 'en'), ('en', target)]
    return [_translation_package(a, b, verify=verify) for a, b in pairs]


def required_packages(asr_model, source, target):
    return [asr_package(effective_asr_model(asr_model, source)), *translation_route(source, target)]


def catalog_packages():
    """All independently managed packages, including unused and incomplete ones.

    Multilingual ASR appears once because all non-English inputs share it. Both
    owned English-to-Spanish Argos and any shared OPUS installation are exposed,
    so installing the shared model never hides an owned package from management.
    """
    result = [asr_package(name) for name in ('small.en', 'base.en', 'small')]
    for language in LANGUAGES:
        if language != 'en':
            result.extend((_argos_translation_package(language, 'en'),
                           _argos_translation_package('en', language)))
    legacy = _legacy_translation(include_incomplete=True)
    if legacy is not None:
        result.append(legacy)
    return result


def package_present(spec):
    """Whether any installation occupies this package's location, even partial."""
    try:
        return os.path.lexists(Path(spec['path']))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def package_ready(spec):
    """Fast offline readiness; installed manifests are written only after checks."""
    try:
        path = Path(spec['path'])
        if not all((path/name).is_file() and (path/name).stat().st_size == data['bytes']
                   for name, data in spec['files'].items()):
            return False
        if spec.get('engine') == 'opus' and bundled_package_rejected():
            return False
        if spec.get('readonly') or spec['kind'] == 'asr':
            return True
        manifest = json.loads((path/'download-manifest.json').read_text(encoding='utf-8'))
        return (manifest.get('id') == spec['id'] and manifest.get('archive_md5') == spec['md5']
                and set(manifest.get('files', {})) == set(spec['files']))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def verify_bundled_package(spec):
    """Hash in the recognition worker, never in a GUI readiness check."""
    try:
        cancel = threading.Event()
        if package_ready(spec) and all(_hash_file(Path(spec['path'])/name, cancel) == data['sha256']
                                     for name, data in BUNDLED_OPUS_FILES.items()):
            return True
    except OSError:
        pass
    reject_bundled_package()
    return False


def _reject_link(path):
    """Do not follow either portable symlinks or Windows junction/reparse points."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if (stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata, 'st_file_attributes', 0)
            & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)):
        raise RuntimeError('language_package_path_invalid')


def _removal_path(spec):
    # The caller cannot turn a catalog ID into permission to delete another path.
    if not isinstance(spec, dict) or not isinstance(spec.get('id'), str):
        raise RuntimeError('language_package_unrecognized')
    known = {item['id']: item for item in catalog_packages()}
    canonical = known.get(spec.get('id'))
    if canonical is None:
        raise RuntimeError('language_package_unrecognized')
    if canonical.get('readonly') or spec.get('readonly'):
        raise RuntimeError('language_package_readonly')
    try:
        requested = Path(spec['path'])
        expected = Path(canonical['path']).absolute()
        base = WORK.absolute()
        if (not requested.is_absolute() or '..' in requested.parts
                or requested != expected or not expected.is_relative_to(base)
                or expected == base or spec.get('kind') != canonical['kind']):
            raise RuntimeError('language_package_path_invalid')
        # Check before resolving: resolving a junction first conceals its presence.
        for path in (expected, *expected.parents):
            _reject_link(path)
        resolved = expected.resolve()
        if resolved != expected or not resolved.is_relative_to(base.resolve()):
            raise RuntimeError('language_package_path_invalid')
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RuntimeError('language_package_path_invalid') from error
    return expected


def _check_removal_tree(path):
    """Validate every descendant before removal; never traverse a reparse point."""
    _reject_link(path)
    if not path.is_dir():
        raise RuntimeError('language_package_path_invalid')
    pending = [path]
    while pending:
        folder = pending.pop()
        with os.scandir(folder) as entries:
            for entry in entries:
                child = Path(entry.path)
                _reject_link(child)
                if entry.is_dir(follow_symlinks=False):
                    pending.append(child)


def remove_package(spec):
    """Remove exactly one catalog-owned package, including partial installs.

    Returns False when already absent. Shared legacy OPUS is read-only. The same
    lock used for installation prevents deletion while any download is active.
    Callers must keep package actions disabled during a playback session.
    """
    if not _INSTALL_LOCK.acquire(blocking=False):
        raise RuntimeError('language_download_already_running')
    try:
        with changing_packages(WORK):
            return _remove_package_locked(spec)
    finally:
        _INSTALL_LOCK.release()


def _remove_package_locked(spec):
    path = _removal_path(spec)
    if not os.path.lexists(path):
        return False
    _check_removal_tree(path)
    # Recheck the exact absolute target immediately before the recursive call.
    if _removal_path(spec) != path:
        raise RuntimeError('language_package_path_invalid')
    shutil.rmtree(path)
    return True


def _safe_root(path):
    path = Path(path).resolve()
    if not path.is_relative_to(WORK.resolve()) or path == WORK.resolve():
        raise RuntimeError('language_package_path_invalid')
    return path


def _download(url, destination, size, expected, algorithm, cancel, progress):
    _cancelled(cancel)
    count = 0
    digest = hashlib.new(algorithm)
    # The Argos download host rejects the default urllib user agent.
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 Langbuffer/1.0'})
    with urllib.request.urlopen(request, timeout=15) as response, destination.open('wb') as output:
        if response.status != 200 or response.geturl().split(':', 1)[0] != 'https':
            raise RuntimeError('language_download_response_invalid')
        length = response.headers.get('Content-Length')
        if length is not None and int(length) != size:
            raise RuntimeError('language_package_size_changed')
        # HTTPResponse.read1 performs at most one raw read, so a trickling
        # response cannot hold cancellation hostage until 256 KiB accumulate.
        # A silent connection remains bounded by the socket's 15 s timeout.
        while True:
            _cancelled(cancel)
            block = response.read1(64*1024)
            if not block:
                break
            _cancelled(cancel)
            count += len(block)
            if count > size:
                raise RuntimeError('language_package_exceeds_expected_size')
            output.write(block)
            digest.update(block)
            progress(count)
    _cancelled(cancel)
    if count != size or digest.hexdigest() != expected:
        raise RuntimeError('language_package_integrity_failed')


def _hash_file(path, cancel):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while block := source.read(1024*1024):
            _cancelled(cancel)
            digest.update(block)
    return digest.hexdigest()


def _extract_argos(archive, spec, target, cancel):
    """Extract only the pinned model/tokenizer/metadata; never package scripts."""
    with zipfile.ZipFile(archive) as package:
        for name, expected in spec['files'].items():
            _cancelled(cancel)
            relative = PurePosixPath(name)
            if relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name:
                raise RuntimeError('language_package_member_invalid')
            member = package.getinfo(spec['root']+'/'+name)
            if member.file_size != expected['bytes'] or member.CRC != expected['crc32']:
                raise RuntimeError('language_package_member_changed')
            destination = (target/name).resolve()
            if not destination.is_relative_to(target.resolve()):
                raise RuntimeError('language_package_member_invalid')
            destination.parent.mkdir(parents=True, exist_ok=True)
            count = 0
            with package.open(member) as source, destination.open('wb') as output:
                while block := source.read(256*1024):
                    _cancelled(cancel)
                    count += len(block)
                    if count > expected['bytes']:
                        raise RuntimeError('language_package_member_oversized')
                    output.write(block)
            if count != expected['bytes']:
                raise RuntimeError('language_package_member_truncated')
        metadata = json.loads((target/'metadata.json').read_text(encoding='utf-8'))
        if (metadata.get('from_code'), metadata.get('to_code')) != (spec['source'], spec['target']):
            raise RuntimeError('language_package_wrong_pair')


def _publish(staged, destination):
    destination = _safe_root(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if destination.exists():
        backup = _safe_root(destination.with_name(destination.name+'.old-'+uuid.uuid4().hex))
        destination.rename(backup)
    try:
        staged.rename(destination)
    except BaseException:
        if backup is not None:
            backup.rename(destination)
        raise
    if backup is not None:
        shutil.rmtree(_safe_root(backup))


@contextmanager
def _staging_directory(root):
    # mkdtemp/TemporaryDirectory use owner-only permissions on Windows. Renaming
    # such a directory into the model folder preserves those private ACLs and
    # can make a sandbox-installed package unreadable to the desktop user.
    # A UUID directory created normally inherits the application directory ACLs.
    temporary = _safe_root(root/('package-'+uuid.uuid4().hex))
    temporary.mkdir()
    try:
        yield temporary
    finally:
        if temporary.exists():
            shutil.rmtree(_safe_root(temporary))


def install_packages(specs, cancel, progress):
    """Install missing catalog packages; cancellation raises DownloadCancelled.

    progress receives {label, done, total, phase}. Counters cover this invocation
    and measure compressed downloaded bytes. No partial package becomes ready.
    """
    if not _INSTALL_LOCK.acquire(blocking=False):
        raise RuntimeError('language_download_already_running')
    try:
        with preparing_packages(WORK, specs, package_ready) as missing:
            return _install_packages_locked(missing, cancel, progress)
    finally:
        _INSTALL_LOCK.release()


def _install_packages_locked(missing, cancel, progress):
    _cancelled(cancel)
    total = sum(spec['download_bytes'] for spec in missing)
    completed = 0
    temporary_root = _safe_root(WORK/'language-downloads')
    temporary_root.mkdir(parents=True, exist_ok=True)
    for spec in missing:
        _cancelled(cancel)
        if spec.get('readonly'):
            raise RuntimeError('legacy_translation_missing')
        def report(phase, count=0):
            progress(dict(label=spec['label'], done=completed+count, total=total, phase=phase))
        with _staging_directory(temporary_root) as temporary:
            stage = _safe_root(Path(temporary)/'ready')
            stage.mkdir()
            manifest = dict(id=spec['id'], files={})
            report('download')
            if spec['kind'] == 'asr':
                file_completed = 0
                for name, data in spec['files'].items():
                    url = f'https://huggingface.co/{spec["repository"]}/resolve/{spec["revision"]}/{name}'
                    _download(url, stage/name, data['bytes'], data['sha256'], 'sha256',
                              cancel, lambda count: report('download', file_completed+count))
                    file_completed += data['bytes']
                manifest.update(repository=spec['repository'], revision=spec['revision'])
            else:
                archive = Path(temporary)/'package.zip'
                _download(spec['url'], archive, spec['bytes'], spec['md5'], 'md5',
                          cancel, lambda count: report('download', count))
                report('install', spec['download_bytes'])
                _extract_argos(archive, spec, stage, cancel)
                manifest.update(url=spec['url'], archive_md5=spec['md5'],
                                archive_sha256=_hash_file(archive, cancel),
                                index_revision=spec.get('index_revision', CATALOG['index_revision']))
            report('verify', spec['download_bytes'])
            for name, data in spec['files'].items():
                manifest['files'][name] = dict(bytes=data['bytes'], sha256=_hash_file(stage/name, cancel))
            (stage/'download-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
            _cancelled(cancel)
            _publish(stage, spec['path'])
        completed += spec['download_bytes']
        report('ready')
    if not missing:
        progress(dict(label='Paquetes disponibles', done=0, total=0, phase='ready'))
