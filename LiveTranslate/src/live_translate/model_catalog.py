"""Pinned English ASR model metadata."""
COMMON_FILES = {
    'tokenizer.json': (2128466, '929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df'),
    'vocabulary.txt': (422309, 'ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf'),
}
PINS = {
    'base.en': {
        'revision': '3d3d5dee26484f91867d81cb899cfcf72b96be6c',
        'files': {
            'config.json': (2227, 'f3bc3821e9fc76a27bae538e11ae5b677dcdd352b4600429ce7951d398569aeb'),
            'model.bin': (145216508, '2a166925539a16005f14ff328359f9b9adb9dc4fb631bb3b227526862e93e2ef'),
            **COMMON_FILES,
        },
    },
    'small.en': {
        'revision': 'd1d751a5f8271d482d14ca55d9e2deeebbae577f',
        'files': {
            'config.json': (2657, '666a9605530ac1f61fa8177f3702b4dacec9966749e42610839fcc32661d5fae'),
            'model.bin': (483545366, '62b2a45b05ee59acb4a5341b33ee35e041395d378d418a18acfe4c9e768ee37a'),
            **COMMON_FILES,
        },
    },
}

# Bundled converted OPUS: pinned against the historical opus-model-manifest,
# also verified by the version-consolidation inventory. Never derive expected
# lengths from the potentially damaged installation itself.
BUNDLED_OPUS_FILES = {
    'model.bin': {'bytes': 82713357, 'sha256': '551cefdb78804acbf62242fcdb58e7ecfd271ea906c561876d7706e41e2348ca'},
    'source.spm': {'bytes': 801636, 'sha256': '4dd547c24816a335e7b0b2e63376a8f1b3cbfc671eda5ab808dd44fdadaa8791'},
    'target.spm': {'bytes': 825924, 'sha256': 'e236ee6d866b635c0142114f8647f39831f9d92534aa2aad75c942f6a78ad0e3'},
    'config.json': {'bytes': 231, 'sha256': '95dabd564e6abdb470e95f9106766647d8dc59e1bc2a1517d292c0cee5ca5812'},
    'shared_vocabulary.json': {'bytes': 1341137, 'sha256': '5d57da3a8899ca0a45f085eff04c41553d784a981544702996001911b9dd0af1'},
}
