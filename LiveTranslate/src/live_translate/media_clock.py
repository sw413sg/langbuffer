"""Reloj compartido que excluye pausas; no conserva un historial creciente."""
import multiprocessing
import time


class MediaClock:
    def __init__(self):
        self.values = multiprocessing.get_context('spawn').Array('d', [0, 0])

    def now(self):
        with self.values.get_lock():
            offset, paused_at = self.values[:]
            return (paused_at or time.perf_counter()) - offset

    def is_paused(self):
        with self.values.get_lock():
            return bool(self.values[1])

    def pause(self):
        with self.values.get_lock():
            if not self.values[1]:
                self.values[1] = time.perf_counter()

    def resume(self):
        with self.values.get_lock():
            if self.values[1]:
                self.values[0] += time.perf_counter()-self.values[1]
                self.values[1] = 0

    def wait_until(self, target, stop):
        while not stop.is_set():
            remaining = target-self.now()
            if remaining <= 0 and not self.is_paused():
                return False
            stop.wait(min(.1, max(.01, remaining)))
        return True
