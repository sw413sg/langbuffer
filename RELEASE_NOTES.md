# Live Translate v0.1.0-beta.1

First public Windows x64 portable build of Live Translate.

## Download

Download `LiveTranslate-v1.zip` from this Release, extract the full archive to a writable folder, and open `LiveTranslate/Start Live Translate.cmd`. Do not use GitHub's source-code ZIP as the app download.

The package includes Python, the app's dependencies, local English recognition (`small.en`), and English-to-Spanish OPUS translation. Other recognition models and language pairs can be downloaded in the Language manager. No Python installation, Windows Live Captions setup, or system Node.js installation is required.

## Included features

- Delayed audio and video playback with stable translated subtitles prepared by local speech recognition.
- Public X broadcasts, Twitch and Kick channels, active YouTube live links, and compatible public Facebook links.
- Nine input and output languages through local downloadable packages.
- Playback quality, pause, volume, fullscreen, subtitle appearance, manual subtitle timing offset, and source DVR where available.
- Temporary media buffers in RAM. The app does not save audio, video, or transcripts.

## Current limits

This is a beta package. It was checked after extraction on the development computer, but has not been tested on a clean Windows installation. Stream availability and formats can change; Facebook has synthetic playback coverage but no real link validation. Subtitle timing is estimated, and simultaneous recognition performance has not been measured with two real streams. See the README for supported URL forms and other limits.

The portable archive is about 903 MB. Its SHA-256 is in `LiveTranslate-v1.zip.sha256` attached to this Release.
