import threading
import unittest
from live_translate.media_buffer import CompressedBuffer, MediaChunk


class CompressedBufferTests(unittest.TestCase):
    def test_retirement_reuses_capacity_in_order(self):
        queue = CompressedBuffer(10, 4)
        for index in range(100):
            queue.push(MediaChunk(index * 2, 2, b'123456'))
            self.assertEqual(queue.pull().start, index * 2)
            self.assertEqual(queue.bytes, 0)
        self.assertEqual(queue.peak_bytes, 6)
        self.assertEqual(queue.retired, 100)

    def test_overflow_keeps_unconsumed_media(self):
        queue = CompressedBuffer(10, 3)
        queue.push(MediaChunk(0, 2, b'12345'))
        with self.assertRaises(BufferError):
            queue.push(MediaChunk(2, 2, b'67890'))
        self.assertEqual(queue.pull().data, b'12345')
        queue.push(MediaChunk(2, 1, b'1234567890'))
        with self.assertRaises(BufferError):
            queue.push(MediaChunk(3, 1, b'1'))

    def test_finish_drains_but_stop_clears_and_wakes(self):
        queue = CompressedBuffer(10, 10)
        queue.push(MediaChunk(0, 2, b'abc'))
        queue.finish()
        self.assertEqual(queue.pull().data, b'abc')
        self.assertIsNone(queue.pull())
        queue = CompressedBuffer(10, 10)
        result = []
        reader = threading.Thread(target=lambda: result.append(queue.pull(5)))
        reader.start()
        queue.clear()
        reader.join(1)
        self.assertFalse(reader.is_alive())
        self.assertEqual(result, [None])
        with self.assertRaises(RuntimeError):
            queue.push(MediaChunk(0, 2, b'abc'))

    def test_bounded_wait_resumes_after_consumer_retires_space(self):
        queue = CompressedBuffer(10, 3)
        stop = threading.Event()
        queue.push(MediaChunk(0, 2, b'first'))
        result = []
        writer = threading.Thread(target=lambda: result.append(
            queue.wait_and_push(MediaChunk(2, 2, b'next'), stop)))
        writer.start()
        self.assertTrue(writer.is_alive())
        self.assertEqual(queue.pull().data, b'first')
        writer.join(1)
        self.assertEqual(result, [True])
        self.assertEqual(queue.pull().data, b'next')

    def test_bounded_wait_is_cancelable_without_dropping_queued_media(self):
        queue = CompressedBuffer(10, 3)
        stop = threading.Event()
        queue.push(MediaChunk(0, 2, b'first'))
        result = []
        writer = threading.Thread(target=lambda: result.append(
            queue.wait_and_push(MediaChunk(2, 2, b'next'), stop)))
        writer.start()
        stop.set()
        writer.join(1)
        self.assertEqual(result, [False])
        self.assertEqual(queue.pull().data, b'first')

    def test_invalid_timeline_cannot_mix_epochs(self):
        queue = CompressedBuffer(10, 10)
        for start, duration in ((2, 1), (0, float('nan')), (0, -1)):
            with self.subTest(start=start, duration=duration), self.assertRaises(ValueError):
                queue.push(MediaChunk(start, duration, b'abc'))
        queue.push(MediaChunk(0, 2, b'abc'))
        queue.pull()
        with self.assertRaises(ValueError):
            queue.push(MediaChunk(0, 2, b'abc'))
