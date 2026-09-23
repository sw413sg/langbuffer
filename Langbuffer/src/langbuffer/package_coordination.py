"""Coordinate shared model changes with playback in separate GUI processes."""
from contextlib import contextmanager
import os
from pathlib import Path
import uuid

from PySide6.QtCore import QLockFile


def _lock(path, timeout=1000):
    lock = QLockFile(str(path))
    # A long download or viewing session must not expire by age. Qt still
    # recovers a lock whose owning process has exited.
    lock.setStaleLockTime(0)
    if not lock.tryLock(timeout):
        raise RuntimeError('language_packages_busy')
    return lock


@contextmanager
def _gate(directory):
    root = Path(directory).resolve() / 'package-coordination'
    root.mkdir(parents=True, exist_ok=True)
    lock = _lock(root / 'operations.lock')
    try:
        yield root
    finally:
        lock.unlock()


def acquire_playback(directory):
    """Register one playback atomically against installation and removal."""
    with _gate(directory) as root:
        marker = root / ('playing-' + uuid.uuid4().hex + '.lock')
        return _lock(marker, 0)


@contextmanager
def changing_packages(directory):
    """Exclude every playback while shared model files can be removed."""
    with _gate(directory) as root:
        if _active_playback(root):
            raise RuntimeError('language_package_in_use')
        yield


@contextmanager
def preparing_packages(directory, specs, ready):
    """Allow new packages during playback, but never replace a present one."""
    with _gate(directory) as root:
        missing = list({spec['id']: spec for spec in specs if not ready(spec)}.values())
        if (_active_playback(root)
                and any(os.path.lexists(spec['path']) for spec in missing)):
            raise RuntimeError('language_package_in_use')
        yield missing


def _active_playback(root):
    for marker in root.glob('playing-*.lock'):
        # A successful lock identifies a stale, unowned marker and removes it.
        try:
            probe = _lock(marker, 0)
        except RuntimeError:
            return True
        else:
            probe.unlock()
    return False
