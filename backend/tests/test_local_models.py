import os
import sys
import types
import unittest
from unittest import mock


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

if "huggingface_hub" not in sys.modules:
    huggingface_hub = types.ModuleType("huggingface_hub")
    huggingface_hub.snapshot_download = lambda *args, **kwargs: None
    sys.modules["huggingface_hub"] = huggingface_hub

if "celery_app" not in sys.modules:
    celery_app = types.ModuleType("celery_app")

    class _Celery:
        def task(self, *args, **kwargs):
            def _decorator(func):
                return func

            return _decorator

    celery_app.celery = _Celery()
    sys.modules["celery_app"] = celery_app

from app.services.inference_client import InferenceServiceError  # noqa: E402
from app.tasks import local_models  # noqa: E402


class LocalModelTaskTests(unittest.TestCase):
    def test_remote_download_polling_tolerates_transient_retrieval_restart(self):
        start_client = mock.Mock()
        start_client.post_json.return_value = {
            "task_id": "remote-task-1",
            "status": "pending",
            "downloaded_files": 0,
            "total_files": 0,
        }
        status_client = mock.Mock()
        status_client.get_json.side_effect = [
            InferenceServiceError("temporary restart", status_code=502),
            {
                "task_id": "remote-task-1",
                "status": "running",
                "variant": "2B",
                "repo_id": "Qwen/Qwen3-VL-Embedding-2B",
                "target_path": "/data/models/qwen3-vl-embedding-2b",
                "downloaded_files": 1,
                "total_files": 2,
            },
            {
                "task_id": "remote-task-1",
                "status": "success",
                "variant": "2B",
                "repo_id": "Qwen/Qwen3-VL-Embedding-2B",
                "target_path": "/data/models/qwen3-vl-embedding-2b",
                "downloaded_files": 2,
                "total_files": 2,
            },
        ]

        with (
            mock.patch.object(local_models, "make_retrieval_client", return_value=start_client),
            mock.patch.object(local_models, "make_retrieval_control_client", return_value=status_client),
            mock.patch.object(local_models, "_update_task_log") as update_task_log,
            mock.patch.object(local_models.time, "sleep"),
        ):
            result = local_models._mirror_remote_model_download(
                object(),
                123,
                {},
                model_type="embedding",
                variant="2B",
            )

        self.assertEqual(result["remote_task_id"], "remote-task-1")
        self.assertEqual(result["repo_id"], "Qwen/Qwen3-VL-Embedding-2B")
        self.assertTrue(
            any(
                call.kwargs.get("meta_updates", {}).get("status_text") == "等待 retrieval-api 恢复后继续同步下载进度"
                for call in update_task_log.mock_calls
            ),
        )


if __name__ == "__main__":
    unittest.main()
