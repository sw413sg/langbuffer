"""Build the portable Windows release without local preferences or metrics.

Run from the repository root with: Langbuffer/runtime/python.exe tools/build_release.py
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "Langbuffer"
DIST = ROOT / "dist"
ARCHIVE = DIST / "Langbuffer-v0.1.1-beta.1-windows-x64.zip"
LIMIT = 2 * 1024**3

DIRECTORIES = (
    APP / "runtime",
    APP / "src",
    APP / "third_party",
    APP / "data" / "models" / "small.en",
    APP / "data" / "translation" / "opus-en-es",
)
FILES = (
    APP / "Iniciar Langbuffer.cmd",
    APP / "Start Langbuffer.cmd",
    APP / "README.md",
    APP / "THIRD_PARTY.md",
    APP / "requirements-lock.txt",
    ROOT / "LICENSE",
)


def release_files():
    for folder in DIRECTORIES:
        if not folder.is_dir():
            raise FileNotFoundError(folder)
        for path in sorted(folder.rglob("*")):
            if path.is_dir():
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Unexpected link or special file: {path}")
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            yield path
    for path in FILES:
        if not path.is_file():
            raise FileNotFoundError(path)
        yield path


def member_name(path):
    if path == ROOT / "LICENSE":
        return "Langbuffer/LICENSE"
    return path.relative_to(ROOT).as_posix()


def main():
    if sys.platform != "win32":
        raise RuntimeError("This release contains Windows x64 executables")
    files = tuple(release_files())
    names = [member_name(path) for path in files]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate ZIP member")
    DIST.mkdir(exist_ok=True)
    partial = ARCHIVE.with_suffix(".zip.partial")
    try:
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6, allowZip64=True) as bundle:
            for index, path in enumerate(files, 1):
                bundle.write(path, member_name(path))
                if index % 500 == 0:
                    print(f"Packed {index}/{len(files)} files", flush=True)
        size = partial.stat().st_size
        if size >= LIMIT:
            raise ValueError(f"Release ZIP is {size:,} bytes; GitHub's asset limit is under 2 GiB")
        with partial.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        partial.replace(ARCHIVE)
        (DIST / (ARCHIVE.name + ".sha256")).write_text(
            f"{digest}  {ARCHIVE.name}\n", encoding="ascii")
        print(f"Created {ARCHIVE} ({size:,} bytes, SHA-256 {digest})")
    finally:
        partial.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
