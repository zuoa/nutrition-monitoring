import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest

from flask import Flask


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

if "flask_migrate" not in sys.modules:
    flask_migrate = types.ModuleType("flask_migrate")

    class _Migrate:
        def init_app(self, *args, **kwargs):
            return None

    flask_migrate.Migrate = _Migrate
    sys.modules["flask_migrate"] = flask_migrate

if "pythonjsonlogger" not in sys.modules:
    pythonjsonlogger = types.ModuleType("pythonjsonlogger")
    jsonlogger = types.ModuleType("jsonlogger")

    class _JsonFormatter:
        def __init__(self, *args, **kwargs):
            pass

    jsonlogger.JsonFormatter = _JsonFormatter
    pythonjsonlogger.jsonlogger = jsonlogger
    sys.modules["pythonjsonlogger"] = pythonjsonlogger

if "redis" not in sys.modules:
    redis = types.ModuleType("redis")
    redis.from_url = lambda *args, **kwargs: object()
    sys.modules["redis"] = redis

from app.inference_api.detector import bp as detector_bp  # noqa: E402
from app.services import yolo_detector as yolo_detector_module  # noqa: E402


class DetectorApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.model_dir = os.path.join(cls.tmpdir.name, "models", "yolo")
        os.makedirs(cls.model_dir, exist_ok=True)
        cls.app = Flask(__name__)
        cls.app.config.update(
            TESTING=True,
            INFERENCE_API_TOKEN="test-token",
            YOLO_MODEL_PATH=os.path.join(cls.model_dir, "best.pt"),
            LOCAL_MODEL_STORAGE_PATH=cls.tmpdir.name,
            LOCAL_RUNTIME_CONFIG_PATH=os.path.join(cls.model_dir, "runtime_config.json"),
        )
        cls.app.register_blueprint(detector_bp)
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.tmpdir.cleanup()

    def setUp(self):
        shutil.rmtree(self.model_dir, ignore_errors=True)
        os.makedirs(self.model_dir, exist_ok=True)
        yolo_detector_module.clear_yolo_cache()

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer test-token"}

    def test_yolo_model_status_when_missing(self):
        res = self.client.get(
            "/v1/models/yolo/status",
            headers=self._auth_headers(),
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()["data"]
        self.assertEqual(data["yolo_model_path"], self.app.config["YOLO_MODEL_PATH"])
        self.assertFalse(data["yolo_model_ready"])
        self.assertEqual(data["yolo_model_filename"], "best.pt")

    def test_yolo_model_status_when_present(self):
        with open(self.app.config["YOLO_MODEL_PATH"], "wb") as f:
            f.write(b"fake model")

        res = self.client.get(
            "/v1/models/yolo/status",
            headers=self._auth_headers(),
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()["data"]
        self.assertTrue(data["yolo_model_ready"])

    def test_upload_yolo_model_requires_file(self):
        res = self.client.post(
            "/v1/models/yolo/upload",
            headers=self._auth_headers(),
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("请上传模型文件", res.get_json()["message"])

    def test_upload_yolo_model_rejects_non_pt_extension(self):
        data = {"model_file": (io.BytesIO(b"fake"), "model.onnx")}
        res = self.client.post(
            "/v1/models/yolo/upload",
            headers=self._auth_headers(),
            data=data,
            content_type="multipart/form-data",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("不支持的模型格式", res.get_json()["message"])

    def test_upload_yolo_model_saves_file_and_updates_runtime_config(self):
        data = {"model_file": (io.BytesIO(b"fake pt content"), "best.pt")}
        res = self.client.post(
            "/v1/models/yolo/upload",
            headers=self._auth_headers(),
            data=data,
            content_type="multipart/form-data",
        )
        self.assertEqual(res.status_code, 200)
        payload = res.get_json()["data"]
        self.assertEqual(payload["yolo_model_path"], self.app.config["YOLO_MODEL_PATH"])
        self.assertTrue(payload["yolo_model_ready"])
        self.assertEqual(payload["size"], len(b"fake pt content"))

        self.assertTrue(os.path.exists(self.app.config["YOLO_MODEL_PATH"]))
        with open(self.app.config["YOLO_MODEL_PATH"], "rb") as f:
            self.assertEqual(f.read(), b"fake pt content")

        runtime_path = self.app.config["LOCAL_RUNTIME_CONFIG_PATH"]
        self.assertTrue(os.path.exists(runtime_path))
        with open(runtime_path, "r", encoding="utf-8") as f:
            overrides = json.load(f)
        self.assertEqual(overrides.get("YOLO_MODEL_PATH"), self.app.config["YOLO_MODEL_PATH"])


if __name__ == "__main__":
    unittest.main()
