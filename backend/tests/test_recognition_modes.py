import importlib.util
import os
import unittest


MODULE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "app",
    "services",
    "recognition_modes.py",
)
SPEC = importlib.util.spec_from_file_location("recognition_modes", MODULE_PATH)
RECOGNITION_MODES = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(RECOGNITION_MODES)


class RecognitionModesTests(unittest.TestCase):
    def test_legacy_local_mode_is_still_accepted(self):
        self.assertTrue(RECOGNITION_MODES.is_local_recognition_mode("yolo_embedding_local"))
        self.assertEqual(
            RECOGNITION_MODES.normalize_recognition_mode("yolo_embedding_local"),
            RECOGNITION_MODES.LOCAL_RECOGNITION_MODE,
        )

    def test_non_local_mode_is_unchanged(self):
        self.assertFalse(RECOGNITION_MODES.is_local_recognition_mode("vl"))
        self.assertEqual(RECOGNITION_MODES.normalize_recognition_mode("vl"), "vl")


if __name__ == "__main__":
    unittest.main()
