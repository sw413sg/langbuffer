import unittest

from langbuffer.video_captions import horizontal_position


class VideoCaptionPositionTests(unittest.TestCase):
    def test_horizontal_slider_moves_complete_block_across_image(self):
        self.assertEqual(horizontal_position(25, 1000, 300, -100), 25)
        self.assertEqual(horizontal_position(25, 1000, 300, 0), 375)
        self.assertEqual(horizontal_position(25, 1000, 300, 100), 725)

    def test_horizontal_position_is_bounded_when_text_fills_area(self):
        for offset in (-100, 0, 100):
            self.assertEqual(horizontal_position(10, 500, 500, offset), 10)
