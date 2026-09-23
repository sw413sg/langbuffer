# Live Translate

This is the only current version of Live Translate. It uses local speech recognition, local translation, and delayed audiovisual playback to prepare subtitles before the viewer hears the corresponding speech.

## Start

Double-click **Start Live Translate.cmd**. The portable release includes Python, Qt, the `small.en` recognition model, and English-to-Spanish OPUS translation. You do not need to install Python or use Windows Live Captions. This development installation may contain additional models and language packages. The original Spanish-named launcher remains available.

Paste a supported public link, choose input and output languages in **Language**, check the playback delay, and press **Start**. Playback continues until you stop it, the source ends, or an error occurs. If the same stream is also playing in your browser, pause or mute the browser player to avoid duplicate audio.

## Supported sources

- **X:** `/i/broadcasts/...` replays or streams. X video posts are currently disabled.
- **Twitch and Kick:** public channel links. Twitch ad handling waits for marked content to return, up to 360 seconds; it cannot recover content replaced by ads.
- **YouTube:** active live links in `youtube.com/watch?v=ID`, `youtube.com/live/ID`, or `youtu.be/ID` form. Recorded videos, scheduled streams, and playlists are outside the current scope.
- **Facebook:** direct public `facebook.com/watch/?v=ID`, `facebook.com/watch/live/?v=ID`, `facebook.com/video.php?v=ID`, and `facebook.com/PAGE/videos/ID` links when the source offers compatible HLS or range-capable MP4. Short `fb.watch` links, private content, and DASH-only sources are unsupported. Facebook playback has synthetic coverage but has not been tested with a real user-provided link.

Source availability and formats can change. Playback is not guaranteed for every public link or indefinitely.

## Controls and languages

The settings menu includes playback quality, volume and mute, pause, restart, fullscreen, window frame, subtitle appearance, and a manual subtitle timing offset. X and Kick expose remote DVR where the source offers history. Seeking starts a new delay countdown; the latest seek wins if several are made during preparation.

You can change the input language, output language, and recognition model during playback. If all required packages are already installed, playback restarts its delay from the current position on X/Kick or from the available live segment on sources without DVR. If a package is missing, download it from **Language**; the change applies when installation finishes. Packages cannot be removed while a session is active.

The portable release includes `small.en` and OPUS English → Spanish. `base.en`, multilingual `small`, and other translation directions are available through the Language manager. Selecting a language does not download it automatically. Nine input and output languages are supported through local packages; English is the interface fallback for output languages other than English, Spanish, Portuguese, and French.

## Files and privacy

| Path | Purpose |
| --- | --- |
| `runtime/` | Bundled Python 3.12 x64, pinned dependencies, and local Node.js for YouTube resolution |
| `src/live_translate/` | Application source code |
| `data/models/` | `small.en` included; other recognition models can be downloaded |
| `data/translation/opus-en-es/` | Included, read-only English → Spanish OPUS model |
| `data/language-packages/` | Optional packages installed from the Language manager |
| `data/preferences.json` | Preferences created on use; omitted from the public archive |
| `outputs/` | Content-free session metrics created on use; omitted from the public archive |
| `tests/` | Development checks in the source repository |

The advanced audio feed goes to recognition in RAM and is not played. Only the delayed feed is audible. Media buffers are temporary and bounded; the app does not save audio, video, or recognized text. It stores preferences, models, and content-free metrics. It does not change default audio devices or install drivers. You can move the whole folder while the app is closed, provided its new location is writable.

Two windows may run at once and use different sources. Package operations coordinate across processes; you can install a new package during playback, but must stop all sessions before removing or replacing an existing package. Shared preferences use the last saved value. Simultaneous live recognition has not been performance-tested with two real streams.

Recognition timing is estimated, so perfect subtitle synchronization is not promised. Under load, the app may skip a subtitle block to keep video moving; metrics count those drops. Isolated recognition and translation errors are contained, while repeated or startup failures end the session.

The YouTube and Facebook resolvers run in cancelable child processes. YouTube uses the bundled Node.js and `yt-dlp-ejs`; no system Node.js installation is required. Temporary media URLs are not written to metrics. A short real YouTube sample was checked; Facebook media playback was checked synthetically. See the [source notes](docs/105-youtube-facebook.md) for the exact scope and limits.

## Verify and develop

From the portable app folder:

```powershell
.\runtime\python.exe -B -m live_translate
.\runtime\python.exe -B -m pip check
```

The source repository contains `check.py` and `tests/` for the complete development installation. `check.py` uses synthetic media in RAM and local tests; its `runtime_check` group expects the additional models and inventory from that installation. It does not contact live streams or save transcripts.

Dependencies are pinned in `requirements-lock.txt`. For source development, use Python 3.12 x64, install that lock file, and add `src/` to `PYTHONPATH` before running `python -m live_translate`. The portable runtime is a local assembled distribution, not a Windows installer.

The app's own code is GPLv3. Third-party components keep their own licenses; see [THIRD_PARTY.md](THIRD_PARTY.md). This package has been smoke-tested after extraction on the development computer. A clean Windows installation remains untested.
