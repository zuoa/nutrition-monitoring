import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

import numpy as np
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

if "huggingface_hub" not in sys.modules:
    huggingface_hub = types.ModuleType("huggingface_hub")
    huggingface_hub.snapshot_download = lambda *args, **kwargs: None
    sys.modules["huggingface_hub"] = huggingface_hub

if "app.services.inference_pipeline" not in sys.modules:
    inference_pipeline = types.ModuleType("app.services.inference_pipeline")

    class _EmbeddingRetrievalService:
        def __init__(self, *args, **kwargs):
            pass

    inference_pipeline.EmbeddingRetrievalService = _EmbeddingRetrievalService
    sys.modules["app.services.inference_pipeline"] = inference_pipeline

if "app.services.local_embedding" not in sys.modules:
    local_embedding = types.ModuleType("app.services.local_embedding")

    class _LocalEmbeddingIndexService:
        MATRIX_FILENAME = "dish_sample_embeddings.npy"
        METADATA_FILENAME = "dish_sample_metadata.json"

        def __init__(self, config):
            self.index_dir = config.get("LOCAL_EMBEDDING_INDEX_DIR", "")

        def _normalize(self, vector):
            return vector

        def _load_index(self):
            matrix_path = os.path.join(self.index_dir, self.MATRIX_FILENAME)
            metadata_path = os.path.join(self.index_dir, self.METADATA_FILENAME)
            matrix = np.load(matrix_path) if os.path.exists(matrix_path) else np.empty((0, 0), dtype=np.float32)
            if os.path.exists(metadata_path):
                with open(metadata_path, "r", encoding="utf-8") as f:
                    metadata = json.load(f)
            else:
                metadata = []
            return matrix, metadata

    local_embedding.LocalEmbeddingIndexService = _LocalEmbeddingIndexService
    sys.modules["app.services.local_embedding"] = local_embedding

from app.inference_api.model_download_tasks import (  # noqa: E402
    get_remote_download_state_dir,
    write_remote_download_state,
)
from app.inference_api.retrieval import bp as retrieval_bp  # noqa: E402


class RetrievalApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.index_dir = os.path.join(cls.tmpdir.name, "index")
        cls.app = Flask(__name__)
        cls.app.config.update(
            TESTING=True,
            INFERENCE_API_TOKEN="test-token",
            LOCAL_EMBEDDING_INDEX_DIR=cls.index_dir,
            LOCAL_MODEL_STORAGE_PATH=os.path.join(cls.tmpdir.name, "models"),
            LOCAL_QWEN3_VL_EMBEDDING_MODEL_PATH=os.path.join(cls.tmpdir.name, "models", "embedding"),
            LOCAL_QWEN3_VL_RERANKER_MODEL_PATH=os.path.join(cls.tmpdir.name, "models", "reranker"),
        )
        cls.app.register_blueprint(retrieval_bp)
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.tmpdir.cleanup()

    def setUp(self):
        shutil.rmtree(self.index_dir, ignore_errors=True)
        shutil.rmtree(get_remote_download_state_dir(self.app.config), ignore_errors=True)
        os.makedirs(os.path.join(self.index_dir, "sample_images", "dish_1"), exist_ok=True)
        self.old_sample_path = os.path.join(self.index_dir, "sample_images", "dish_1", "sample_1.jpg")
        with open(self.old_sample_path, "wb") as f:
            f.write(b"old-sample")

        np.save(
            os.path.join(self.index_dir, "dish_sample_embeddings.npy"),
            np.asarray([[1.0, 0.0]], dtype=np.float32),
        )
        with open(os.path.join(self.index_dir, "dish_sample_metadata.json"), "w", encoding="utf-8") as f:
            json.dump([{
                "image_id": 1,
                "dish_id": 1,
                "dish_name": "旧样图",
                "image_path": self.old_sample_path,
            }], f, ensure_ascii=False, indent=2)

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer test-token"}

    def test_failed_upload_keeps_current_sample_images(self):
        matrix_buf = io.BytesIO()
        np.save(matrix_buf, np.asarray([[0.0, 1.0]], dtype=np.float32))
        matrix_buf.seek(0)
        metadata_buf = io.BytesIO(json.dumps([{
            "image_id": 2,
            "dish_id": 2,
            "dish_name": "新样图",
            "relative_image_path": "dish_2/sample_2.jpg",
        }]).encode("utf-8"))

        res = self.client.post(
            "/v1/index/upload",
            headers=self._auth_headers(),
            data={
                "matrix_file": (matrix_buf, "matrix.npy"),
                "metadata_file": (metadata_buf, "metadata.json"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(res.status_code, 400)
        self.assertTrue(os.path.exists(self.old_sample_path))
        with open(self.old_sample_path, "rb") as f:
            self.assertEqual(f.read(), b"old-sample")

        with open(os.path.join(self.index_dir, "dish_sample_metadata.json"), "r", encoding="utf-8") as f:
            metadata = json.load(f)
        self.assertEqual(metadata[0]["image_path"], self.old_sample_path)

    def test_get_download_status_uses_persisted_task_state(self):
        task_id = "persisted-download"
        write_remote_download_state(self.app.config, task_id, {
            "task_id": task_id,
            "model_type": "embedding",
            "variant": "2B",
            "repo_id": "Qwen/Qwen3-VL-Embedding-2B",
            "target_path": os.path.join(self.tmpdir.name, "models", "qwen3-vl-embedding-2b"),
            "hf_endpoint": "https://huggingface.co",
            "status": "running",
            "progress_percent": 42.0,
            "downloaded_bytes": 42,
            "total_bytes": 100,
            "downloaded_files": 1,
            "total_files": 2,
            "status_text": "正在下载模型文件",
            "error_message": "",
            "started_at": "2026-04-02T00:00:00Z",
            "finished_at": None,
            "created_at": "2026-04-02T00:00:00Z",
        })

        with mock.patch("app.inference_api.retrieval.ensure_remote_download_worker") as ensure_worker:
            res = self.client.get(
                f"/v1/models/download/{task_id}",
                headers=self._auth_headers(),
            )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()["data"]
        self.assertEqual(payload["task_id"], task_id)
        self.assertEqual(payload["status"], "running")
        ensure_worker.assert_called_once()

    def test_full_without_regions_falls_back_to_full_image(self):
        captured = {}

        class FakeEmbeddingRetrievalService:
            def __init__(self, config):
                self.config = dict(config)

            def full(self, image_path, *, candidate_dishes, regions):
                captured["image_path"] = image_path
                captured["candidate_dishes"] = candidate_dishes
                captured["regions"] = regions
                return {
                    "recognized_dishes": [{"name": "红烧肉", "confidence": 0.9}],
                    "region_results": [{"index": 1, "bbox": None}],
                    "raw_response": {"mode": "local_embedding"},
                    "model_version": "qwen3_vl_embedding+reranker",
                    "notes": "full_image local embedding 模式，区域数 1",
                }

        with mock.patch("app.inference_api.retrieval.EmbeddingRetrievalService", FakeEmbeddingRetrievalService):
            res = self.client.post(
                "/v1/full",
                headers=self._auth_headers(),
                data={
                    "candidate_dishes": json.dumps([{"id": 1, "name": "红烧肉", "description": ""}]),
                    "image_file": (io.BytesIO(b"fake-image"), "meal.jpg"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(captured["candidate_dishes"], [{"id": 1, "name": "红烧肉", "description": "", "structured_description": None}])
        self.assertEqual(captured["regions"], [{
            "index": 1,
            "bbox": None,
            "source": "full_image",
        }])
        payload = res.get_json()["data"]
        self.assertEqual(payload["recognized_dishes"], [{"name": "红烧肉", "confidence": 0.9}])


if __name__ == "__main__":
    unittest.main()
