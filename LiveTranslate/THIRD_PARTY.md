# Third-party components

The portable package retains installed dependency licenses in `runtime/Lib/site-packages`, their `.dist-info` directories, and `runtime/LICENSE.txt`. Exact runtime package versions are in `requirements-lock.txt`.

- **Python 3.12.14 x64:** bundled standard library, DLLs, and license, with paths relative to the app folder.
- **PySide6 / Qt / Shiboken 6.11.2:** user interface and media playback. The bundled PySide6 packages retain their libraries and license notices.
- **faster-whisper, CTranslate2, NumPy, ONNX Runtime, PyAV, SentencePiece, and tokenizers:** local recognition, translation, and media processing.
- **yt-dlp:** resolves public remote sources. The app does not use it to save media.
- **yt-dlp-ejs 0.8.0:** JavaScript component used by yt-dlp for YouTube live resolution. Its version is pinned in `requirements-lock.txt`; package metadata and license remain in `runtime/Lib/site-packages`.
- **Node.js 24.19.0 for Windows x64:** bundled at `runtime/javascript/node.exe` for the YouTube resolver only. Its license and notices are in `runtime/javascript/LICENSE`; see the [official download](https://nodejs.org/en/download) and [license](https://github.com/nodejs/node/blob/v24.19.0/LICENSE). Executable SHA-256: `3602F2BB1A10F2CBAB4C36886218A33C1AB3DB87290E73B033C46C77147D0237`.
- **Systran speech models:** `small.en` is bundled; `base.en` and multilingual `small` can be downloaded. The bundled model converts [OpenAI Whisper small.en](https://huggingface.co/Systran/faster-whisper-small.en), listed as MIT; the [original MIT notice](third_party/whisper-model-LICENSE) is included. Repositories, revisions, and integrity checks are in `src/live_translate/model_catalog.py`, `src/live_translate/package_catalog.json`, and download manifests.
- **OPUS-MT English → Spanish:** model converted to CTranslate2 INT8. Its `LICENSE` (CC-BY-4.0), `README.md`, and provenance manifest remain beside the model.
- **Optional translation packages:** each installed package retains its license, metadata, and manifest; the catalog pins origin and integrity.

The app's own code is GPLv3 (`../LICENSE` in the source repository; `LICENSE` in the portable package). Third-party components retain their own licenses. This inventory is not a complete legal review of all redistribution obligations.

Python uses relative paths in `runtime/python312._pth`, following the [official Python documentation for Windows](https://docs.python.org/3.12/using/windows.html#finding-modules). Existing models and packages were not updated during consolidation.
