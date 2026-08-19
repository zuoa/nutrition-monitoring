import importlib.util
import os
import sys
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np


MODULE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "app",
    "services",
    "osd_time_calibration.py",
)
SPEC = importlib.util.spec_from_file_location("osd_time_calibration", MODULE_PATH)
OSD_CALIBRATION = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = OSD_CALIBRATION
SPEC.loader.exec_module(OSD_CALIBRATION)

OSDTimeCalibrator = OSD_CALIBRATION.OSDTimeCalibrator
parse_osd_timestamp = OSD_CALIBRATION.parse_osd_timestamp


class OSDTimestampParsingTests(unittest.TestCase):
    def setUp(self):
        self.tz = ZoneInfo("Asia/Shanghai")

    def test_parses_full_hikvision_osd_timestamp(self):
        expected = datetime(2026, 8, 16, 5, 44, 52, tzinfo=self.tz)

        parsed, date_from_ocr = parse_osd_timestamp("2026-08-16 星期日 05:44:50", expected)

        self.assertEqual(parsed, datetime(2026, 8, 16, 5, 44, 50, tzinfo=self.tz))
        self.assertTrue(date_from_ocr)

    def test_time_only_uses_nearest_expected_date_across_midnight(self):
        expected = datetime(2026, 8, 17, 0, 0, 1, tzinfo=self.tz)

        parsed, date_from_ocr = parse_osd_timestamp("23:59:59", expected)

        self.assertEqual(parsed, datetime(2026, 8, 16, 23, 59, 59, tzinfo=self.tz))
        self.assertFalse(date_from_ocr)


class OSDTimeCalibratorTests(unittest.TestCase):
    def setUp(self):
        self.tz = ZoneInfo("Asia/Shanghai")
        self.reported_start = datetime(2026, 8, 16, 5, 44, 52, tzinfo=self.tz)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.video_path = os.path.join(self.temp_dir.name, "recording.mp4")
        with open(self.video_path, "wb") as handle:
            handle.write(b"video")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _calibrator(self, texts):
        responses = iter((text, 0.95) for text in texts)
        return OSDTimeCalibrator(
            {
                "VIDEO_OSD_TIME_CALIBRATION_ENABLED": True,
                "VIDEO_OSD_OCR_SAMPLE_OFFSETS": "0,1,2,4",
                "VIDEO_TIMEZONE": "Asia/Shanghai",
            },
            frame_loader=lambda _path, offset: np.array([[[offset]]], dtype=np.uint8),
            ocr_reader=lambda _frame: next(responses),
        )

    def test_consistent_samples_calibrate_media_start(self):
        calibrator = self._calibrator([
            "2026-08-16 05:44:50",
            "2026-08-16 05:44:51",
            "2026-08-16 05:44:52",
            "2026-08-16 05:44:54",
        ])

        result = calibrator.calibrate(self.video_path, self.reported_start)

        self.assertTrue(result.calibrated)
        self.assertEqual(result.media_start, datetime(2026, 8, 16, 5, 44, 50, tzinfo=self.tz))
        self.assertEqual(result.offset_seconds, -2.0)
        self.assertEqual(result.inlier_sample_count, 3)
        self.assertGreater(result.confidence, 0.9)

    def test_inconsistent_samples_do_not_change_reported_start(self):
        calibrator = self._calibrator([
            "2026-08-16 05:44:50",
            "2026-08-16 05:45:01",
            "2026-08-16 05:45:22",
            "2026-08-16 05:45:54",
        ])

        result = calibrator.calibrate(self.video_path, self.reported_start)

        self.assertFalse(result.calibrated)
        self.assertEqual(result.status, "inconsistent_samples")
        self.assertEqual(result.media_start, self.reported_start)

    def test_calibration_is_disabled_by_default_and_does_not_load_frames(self):
        calibrator = OSDTimeCalibrator(
            {},
            frame_loader=lambda *_args: self.fail("frame loader should not run"),
        )

        result = calibrator.calibrate(self.video_path, self.reported_start)

        self.assertEqual(result.status, "disabled")
        self.assertEqual(result.media_start, self.reported_start)


if __name__ == "__main__":
    unittest.main()
