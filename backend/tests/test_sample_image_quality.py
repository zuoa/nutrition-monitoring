import io
import importlib.util
import os
import unittest
from unittest import mock

from PIL import Image


MODULE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "app",
    "services",
    "sample_image_quality.py",
)
spec = importlib.util.spec_from_file_location("test_sample_image_quality_service", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
expand_bbox = module.expand_bbox
validate_sample_image_stream = module.validate_sample_image_stream


def build_image_stream(size: tuple[int, int]) -> io.BytesIO:
    stream = io.BytesIO()
    Image.new("RGB", size, color=(120, 80, 40)).save(stream, format="JPEG")
    stream.seek(0)
    return stream


class SampleImageQualityTests(unittest.TestCase):
    def test_accepts_valid_sample_and_restores_stream_position(self):
        stream = build_image_stream((320, 240))
        stream.seek(7)

        error = validate_sample_image_stream(
            stream,
            min_edge=128,
            max_aspect_ratio=3.0,
        )

        self.assertIsNone(error)
        self.assertEqual(stream.tell(), 7)

    def test_rejects_tiny_sample(self):
        error = validate_sample_image_stream(
            build_image_stream((96, 240)),
            min_edge=128,
            max_aspect_ratio=3.0,
        )

        self.assertIn("短边至少需要 128px", error)

    def test_rejects_extreme_aspect_ratio(self):
        error = validate_sample_image_stream(
            build_image_stream((512, 128)),
            min_edge=128,
            max_aspect_ratio=3.0,
        )

        self.assertIn("长宽比不能超过 3:1", error)

    def test_rejects_invalid_image_bytes(self):
        error = validate_sample_image_stream(
            io.BytesIO(b"not-an-image"),
            min_edge=128,
            max_aspect_ratio=3.0,
        )

        self.assertIn("无法读取图片内容", error)

    def test_rejects_excessive_pixels_before_decoding(self):
        oversized_image = mock.MagicMock()
        oversized_image.size = (5000, 4000)
        oversized_image.__enter__.return_value = oversized_image
        oversized_image.__exit__.return_value = False

        with mock.patch.object(module.Image, "open", return_value=oversized_image):
            error = validate_sample_image_stream(
                io.BytesIO(b"image-header"),
                min_edge=128,
                max_aspect_ratio=3.0,
                max_pixels=16_777_216,
            )

        self.assertIn("样图像素总数不能超过 16777216", error)
        oversized_image.load.assert_not_called()

    def test_handles_pillow_decompression_bomb_error(self):
        with mock.patch.object(
            module.Image,
            "open",
            side_effect=module.Image.DecompressionBombError("too many pixels"),
        ):
            error = validate_sample_image_stream(
                io.BytesIO(b"image-header"),
                min_edge=128,
                max_aspect_ratio=3.0,
            )

        self.assertIn("分辨率过高", error)

    def test_expand_bbox_adds_padding_and_clamps_to_image(self):
        self.assertEqual(
            expand_bbox(
                (10, 20, 50, 60),
                image_width=100,
                image_height=80,
                padding_ratio=0.1,
            ),
            (6, 16, 54, 64),
        )
        self.assertEqual(
            expand_bbox(
                (0, 0, 30, 30),
                image_width=100,
                image_height=80,
                padding_ratio=0.1,
            ),
            (0, 0, 33, 33),
        )


if __name__ == "__main__":
    unittest.main()
