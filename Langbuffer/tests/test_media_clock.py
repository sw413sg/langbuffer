import threading
import unittest
from unittest.mock import patch
from langbuffer.media_clock import MediaClock


class MediaClockTests(unittest.TestCase):
    def test_multiple_pauses_excluded_without_growing_history(self):
        clock = MediaClock()
        with patch('langbuffer.media_clock.time.perf_counter', return_value=100):
            clock.pause()
        with patch('langbuffer.media_clock.time.perf_counter', return_value=130):
            self.assertEqual(clock.now(), 100)
            clock.pause()
            clock.resume()
        with patch('langbuffer.media_clock.time.perf_counter', return_value=140):
            self.assertEqual(clock.now(), 110)
            clock.pause()
        with patch('langbuffer.media_clock.time.perf_counter', return_value=150):
            clock.resume()
            self.assertEqual(clock.now(), 110)
        self.assertEqual(len(clock.values), 2)

    def test_stop_interrupts_due_delivery_while_paused(self):
        clock = MediaClock()
        clock.pause()
        stop = threading.Event()
        completed = threading.Event()
        def wait():
            self.assertTrue(clock.wait_until(clock.now()-1, stop))
            completed.set()
        worker = threading.Thread(target=wait)
        worker.start()
        self.assertFalse(completed.wait(.05))
        stop.set()
        worker.join(1)
        self.assertTrue(completed.is_set())
