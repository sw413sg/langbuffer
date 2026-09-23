"""Clean a supplied portable ZIP before attaching it to a public Release.

Run with: Langbuffer/runtime/python.exe tools/prepare_supplied_release.py INPUT.zip
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path, PurePosixPath
import shutil
import zipfile


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "Langbuffer"
OUTPUT = ROOT / "dist" / "Langbuffer-v0.1.1-beta.2-windows-x64.zip"
OVERLAYS = {
    "Langbuffer/src/langbuffer/assets/langbuffer.png": APP / "src" / "langbuffer" / "assets" / "langbuffer.png",
    "Langbuffer/src/langbuffer/assets/langbuffer-light.png": APP / "src" / "langbuffer" / "assets" / "langbuffer-light.png",
    "Langbuffer/README.md": APP / "README.md",
    "Langbuffer/THIRD_PARTY.md": APP / "THIRD_PARTY.md",
    "Langbuffer/LICENSE": ROOT / "LICENSE",
    "Langbuffer/Start Langbuffer.cmd": APP / "Start Langbuffer.cmd",
    "Langbuffer/Iniciar Langbuffer.cmd": APP / "Iniciar Langbuffer.cmd",
    "Langbuffer/third_party/whisper-model-LICENSE": APP / "third_party" / "whisper-model-LICENSE",
}


def skip(name):
    parts = PurePosixPath(name).parts
    if not parts or parts[0] != "Langbuffer" or ".." in parts:
        raise ValueError(f"Unexpected ZIP path: {name}")
    if "__pycache__" in parts or name.endswith(".pyc"):
        return True
    if name in {"Langbuffer/data/preferences.json", "Langbuffer/README.md"}:
        return name not in OVERLAYS
    if (name.startswith("Langbuffer/outputs/")
            or name.startswith("Langbuffer/data/package-coordination/")
            or name.startswith("Langbuffer/data/language-downloads/")
            or name.startswith("Langbuffer/tests/")
            or name.startswith("Langbuffer/docs/")):
        return True
    return name in OVERLAYS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_zip", type=Path)
    args = parser.parse_args()
    source = args.input_zip.resolve(strict=True)
    OUTPUT.parent.mkdir(exist_ok=True)
    if source == OUTPUT.resolve():
        raise ValueError("Input and output ZIP must differ")
    partial = OUTPUT.with_suffix(".zip.partial")
    copied = removed = 0
    try:
        with zipfile.ZipFile(source, "r") as original, zipfile.ZipFile(
                partial, "w", compression=zipfile.ZIP_DEFLATED,
                compresslevel=6, allowZip64=True) as clean:
            names = original.namelist()
            if len(names) != len(set(names)):
                raise ValueError("Input ZIP has duplicate paths")
            required = {"Langbuffer/runtime/pythonw.exe",
                        "Langbuffer/data/models/small.en/model.bin",
                        "Langbuffer/data/translation/opus-en-es/model.bin"}
            if not required.issubset(names):
                raise ValueError(f"Input ZIP is missing: {required - set(names)}")
            for entry in original.infolist():
                if skip(entry.filename):
                    removed += 1
                    continue
                if entry.is_dir():
                    clean.writestr(entry, b"")
                else:
                    with original.open(entry) as reader, clean.open(entry, "w", force_zip64=True) as writer:
                        shutil.copyfileobj(reader, writer, length=1024 * 1024)
                copied += 1
            for name, path in OVERLAYS.items():
                clean.write(path, name)
        if partial.stat().st_size >= 2 * 1024**3:
            raise ValueError("Release asset must be smaller than 2 GiB")
        with zipfile.ZipFile(partial) as clean:
            if clean.testzip() is not None:
                raise ValueError("Release ZIP failed CRC verification")
            paths = clean.namelist()
            if any("preferences.json" in name or "/outputs/" in name or "__pycache__" in name
                   for name in paths):
                raise ValueError("Release ZIP still contains local state")
        with partial.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        partial.replace(OUTPUT)
        (OUTPUT.parent / (OUTPUT.name + ".sha256")).write_text(
            f"{digest}  {OUTPUT.name}\n", encoding="ascii")
        print(f"Prepared {OUTPUT} ({OUTPUT.stat().st_size:,} bytes, {copied} copied, "
              f"{removed} removed, SHA-256 {digest})")
    finally:
        partial.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
