"""Reintentos acotados para cambios de formato; sin URLs ni estado persistente."""
from collections import deque


FORMAT_CHANGES = frozenset({
    'source_unsupported_hls_feature_map_change',
    'source_unsupported_hls_feature_discontinuity',
})


class TwitchRecovery:
    """Como máximo seis reconstrucciones en cualquier ventana de tres minutos."""
    delays = (1, 2, 4, 8, 15, 15)
    window_seconds = 180

    def __init__(self):
        self.attempts = deque()

    def reserve(self, now):
        while self.attempts and now-self.attempts[0] >= self.window_seconds:
            self.attempts.popleft()
        if len(self.attempts) >= len(self.delays):
            return None
        delay = self.delays[len(self.attempts)]
        self.attempts.append(now)
        return delay
