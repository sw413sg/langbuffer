# Langbuffer v0.1.1-beta.2

This Windows x64 update adds the Langbuffer icon to the app window and a centered logo to the repository README. Both use transparent backgrounds; the README selects a light or dark colorway to remain visible with GitHub's theme. Playback, recognition, translation, supported sources, and local package handling retain their existing behavior.

## Download

Download `Langbuffer-v0.1.1-beta.2-windows-x64.zip` and its `.sha256` file from this Release. Extract the archive to a writable folder and open `Langbuffer/Start Langbuffer.cmd` or `Langbuffer/Iniciar Langbuffer.cmd`. GitHub's source-code ZIP does not include the runtime or models.

The package includes Python, Qt, local English recognition (`small.en`), and English-to-Spanish OPUS translation. Other recognition models and translation directions remain available through the Language manager.

## Upgrading from Live Translate

Close the old app before upgrading. To carry over installed optional models and preferences to a separate extraction, copy `LiveTranslate/data/preferences.json`, `LiveTranslate/data/models/`, and `LiveTranslate/data/language-packages/` into the corresponding `Langbuffer/data/` paths. Existing bundled model files may be left in place. Do not copy `outputs/` or temporary download folders.

## Scope and limits

The app uses a bounded RAM buffer for delayed playback and does not save media or transcripts. This is still a beta package. Stream formats may change, Facebook playback has synthetic coverage but no real link validation, subtitle timing is estimated, and a clean Windows installation remains untested. See the README for details.
