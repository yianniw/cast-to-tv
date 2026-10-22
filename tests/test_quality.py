import unittest

from cast_to_tv.quality import VideoQuality


class VideoQualityTests(unittest.TestCase):
    def test_limits_preserve_aspect_ratio_and_do_not_upscale(self):
        for source, expected_720, expected_1080 in (
            ((3840, 2160), (1280, 720), (1920, 1080)),
            ((1080, 1920), (404, 720), (606, 1080)),
            ((3440, 1440), (1280, 534), (1920, 802)),
            ((800, 600), (800, 600), (800, 600)),
            ((801, 601), (800, 600), (800, 600)),
        ):
            display = dict(width=source[0], height=source[1])
            for resolution, expected in (("720p", expected_720), ("1080p", expected_1080)):
                for rate in (15, 30):
                    with self.subTest(source=source, resolution=resolution, rate=rate):
                        self.assertEqual(VideoQuality(resolution, rate).output_size(display), expected)

    def test_rejects_unsupported_limits_and_frame_rates(self):
        for resolution, rate in (("4K", 30), ("720p", 60), ("1080p", True), ("720p", 15.0)):
            with self.subTest(resolution=resolution, rate=rate), self.assertRaises(ValueError):
                VideoQuality(resolution, rate)
