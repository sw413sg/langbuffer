"""Detect a still-running pre-update GUI during the multi-window handoff."""
from pathlib import Path
from PySide6.QtCore import QLockFile


def acquire_instance(directory):
    path = Path(directory).resolve()
    path.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(path/'app.lock'))
    # Never mistake a long viewing session for a stale lock. QLockFile still
    # detects a dead owning process after an abnormal exit.
    lock.setStaleLockTime(0)
    # New windows only probe this legacy lock. Give a simultaneous probe time
    # to release it before treating an older app as still running.
    return lock if lock.tryLock(1000) else None
