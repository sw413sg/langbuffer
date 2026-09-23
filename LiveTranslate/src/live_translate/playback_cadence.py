"""Intervalos de callbacks; no mide refresco físico ni almacena medios."""
class PlaybackCadence:
    def __init__(self, threshold=.2, capacity=32):
        self.threshold = threshold
        self.capacity = capacity
        self.previous = None
        self.previous_pts = None
        self.origin = None
        self.maximum = 0.0
        self.count = 0
        self.gaps = []

    def observe(self, now, pts_us):
        if self.origin is None:
            self.origin = now
        if self.previous is not None:
            gap = now - self.previous
            self.maximum = max(self.maximum, gap)
            if gap >= self.threshold:
                self.count += 1
                if len(self.gaps) < self.capacity:
                    delta = None if pts_us < 0 or self.previous_pts is None else (pts_us-self.previous_pts)/1000
                    self.gaps.append(dict(at_s=round(now-self.origin, 3), gap_ms=round(gap*1000, 3),
                                          pts_step_ms=delta))
        self.previous = now
        self.previous_pts = pts_us if pts_us >= 0 else None

    def summary(self):
        return dict(maximum_callback_gap_ms=round(self.maximum*1000, 3),
                    gaps_over_200ms=self.count, first_gaps=self.gaps)
