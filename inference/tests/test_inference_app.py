import json
import os
import sys
import tempfile
import types
import unittest


INFERENCE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if INFERENCE_DIR not in sys.path:
    sys.path.insert(0, INFERENCE_DIR)

if "pythonjsonlogger" not in sys.modules:
    pythonjsonlogger = types.ModuleType("pythonjsonlogger")
    jsonlogger = types.ModuleType("jsonlogger")

    class _JsonFormatter:
        def __init__(self, *args, **kwargs):
            pass

    jsonlogger.JsonFormatter = _JsonFormatter
    pythonjsonlogger.jsonlogger = jsonlogger
    sys.modules["pythonjsonlogger"] = pythonjsonlogger

from app.inference_app import create_inference_app  # noqa: E402


class InferenceAppTests(unittest.TestCase):
    def test_startup_restores_persisted_runtime_pipeline(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_path = os.path.join(tmpdir, "runtime_config.json")
            with open(runtime_path, "w", encoding="utf-8") as f:
                json.dump({"LOCAL_RETRIEVAL_PIPELINE": "visual"}, f)

            config_class = type("TestConfig", (), {
                "INFERENCE_SERVICE_ROLE": "none",
                "LOCAL_MODEL_STORAGE_PATH": tmpdir,
                "LOCAL_RETRIEVAL_PIPELINE": "qwen",
                "LOCAL_RUNTIME_CONFIG_PATH": runtime_path,
            })
            app = create_inference_app(config_class)

        self.assertEqual(app.config["LOCAL_RETRIEVAL_PIPELINE"], "visual")


if __name__ == "__main__":
    unittest.main()
