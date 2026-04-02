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

from app.services import embedding_jobs  # noqa: E402
from app.services.inference_client import InferenceServiceError  # noqa: E402


class EmbeddingJobsTests(unittest.TestCase):
    def test_remote_mode_can_skip_health_probe_for_background_trigger(self):
        with mock.patch.object(embedding_jobs, "make_retrieval_control_client") as make_client:
            allowed, reason = embedding_jobs.can_trigger_local_embedding_rebuild({
                "DISH_RECOGNITION_MODE": "local_embedding",
                "LOCAL_MODEL_MANAGEMENT_MODE": "retrieval_api",
                "LOCAL_REBUILD_SAMPLE_EMBEDDINGS_ON_UPLOAD": True,
            }, check_remote_ready=False)

        self.assertTrue(allowed)
        self.assertIsNone(reason)
        make_client.assert_not_called()

    def test_remote_mode_allows_rebuild_when_remote_embedding_model_is_ready(self):
        fake_client = mock.Mock()
        fake_client.get_json.return_value = {"embedding_model_downloaded": True}

        with mock.patch.object(embedding_jobs, "make_retrieval_control_client", return_value=fake_client):
            allowed, reason = embedding_jobs.can_trigger_local_embedding_rebuild({
                "DISH_RECOGNITION_MODE": "local_embedding",
                "LOCAL_MODEL_MANAGEMENT_MODE": "retrieval_api",
                "LOCAL_REBUILD_SAMPLE_EMBEDDINGS_ON_UPLOAD": True,
            })

        self.assertTrue(allowed)
        self.assertIsNone(reason)

    def test_remote_mode_blocks_rebuild_when_retrieval_api_is_unavailable(self):
        fake_client = mock.Mock()
        fake_client.get_json.side_effect = InferenceServiceError("boom", status_code=502)

        with mock.patch.object(embedding_jobs, "make_retrieval_control_client", return_value=fake_client):
            allowed, reason = embedding_jobs.can_trigger_local_embedding_rebuild({
                "DISH_RECOGNITION_MODE": "local_embedding",
                "LOCAL_MODEL_MANAGEMENT_MODE": "retrieval_api",
                "LOCAL_REBUILD_SAMPLE_EMBEDDINGS_ON_UPLOAD": True,
            })

        self.assertFalse(allowed)
        self.assertIn("retrieval-api unavailable", str(reason))


if __name__ == "__main__":
    unittest.main()
