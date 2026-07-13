import os
import tempfile
import unittest

from app.services.model_downloads import validate_model_snapshot
from app.services.local_model_manager import DINOV3_MODEL_TYPE, get_local_model_spec, is_managed_model_ready


class ModelDownloadValidationTests(unittest.TestCase):
    def test_dinov3_defaults_to_public_timm_checkpoint(self):
        spec = get_local_model_spec({"LOCAL_MODEL_STORAGE_PATH": "/data/models"}, DINOV3_MODEL_TYPE)

        self.assertEqual(spec["repo_id"], "timm/vit_base_patch16_dinov3.lvd1689m")
        self.assertEqual(spec["path"], "/data/models/vit-base-patch16-dinov3-lvd1689m")

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
