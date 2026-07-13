import os
import tempfile
import unittest

from app.services.model_downloads import validate_model_snapshot
from app.services.local_model_manager import is_managed_model_ready


class ModelDownloadValidationTests(unittest.TestCase):
    def test_snapshot_requires_config_and_weights(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "config.json"), "w", encoding="utf-8") as file:
                file.write("{}")
            with self.assertRaisesRegex(RuntimeError, "权重"):
                validate_model_snapshot(tmpdir)

            with open(os.path.join(tmpdir, "model.safetensors"), "wb") as file:
                file.write(b"weights")
            validate_model_snapshot(tmpdir)
            self.assertFalse(is_managed_model_ready(tmpdir))
            with open(os.path.join(tmpdir, ".download_complete"), "w", encoding="utf-8") as file:
                file.write("ok\n")
            self.assertTrue(is_managed_model_ready(tmpdir))


if __name__ == "__main__":
    unittest.main()
