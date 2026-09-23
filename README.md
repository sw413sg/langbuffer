# Live Translate

Live Translate is a Windows desktop app that plays live streams with a configurable delay while local speech recognition prepares translated subtitles. The audio and video you see are delayed together; published subtitle text stays stable on screen.

## Download and run

Download **`LiveTranslate-windows-x64.zip` from [Releases](https://github.com/sw413sg/live-translate/releases)**, extract the entire archive to a writable folder, and open `LiveTranslate/Iniciar Live Translate.cmd`. The portable package includes Python, the app's dependencies, the `small.en` English speech model, and English-to-Spanish translation. Additional speech models and language pairs can be downloaded in the app's **Language** manager.

GitHub's **Code → Download ZIP** contains source code only. It does not contain the Python runtime or models and is not the ready-to-run download.

The first public package targets Windows x64. It has been checked after extraction on the development machine; a clean Windows installation has not yet been tested.

## What it does

- Plays supported public streams from X, Twitch, Kick, and active YouTube live links, plus compatible public Facebook video links.
- Offers configurable playback delay, local speech recognition and translation, and subtitles synchronized to the delayed playback.
- Supports nine input and output languages through downloadable local packages. English recognition and English-to-Spanish translation are included in the portable download.
- Provides quality selection, pause, volume, subtitle appearance, a manual timing offset, and remote DVR where the source provides it.
- Keeps media buffers in RAM. It does not save audio, video, or transcripts; it stores preferences, installed models, and content-free session metrics.

Source availability and stream formats can change. Some advertised sources and formats have only synthetic coverage; see [supported sources and limits](LiveTranslate/README.md) for details.

## Source code and licensing

The app's own code is licensed under [GPLv3](LICENSE). Bundled third-party components keep their respective licenses; see [third-party notices](LiveTranslate/THIRD_PARTY.md).

This repository excludes the runtime, models, personal preferences, and session metrics. To build a portable archive from a complete local installation:

```powershell
.\LiveTranslate\runtime\python.exe tools\build_release.py
```

The script writes the archive and its SHA-256 checksum to `dist/`. It omits local preferences, metrics, and optional downloaded packages.

To work from a source clone, install Python 3.12 x64 and `LiveTranslate/requirements-lock.txt`. From `LiveTranslate/`, set `PYTHONPATH=src` before running `python -m live_translate`; install the needed models in the app's Language manager. The package catalog pins download revisions and checksums.

- [Usage, dependencies, and verification](LiveTranslate/README.md)
- [Version consolidation notes (Spanish)](docs/103-version-unica.md)

The root `docs/` directory contains historical project notes in Spanish. The runnable app does not depend on those notes.
