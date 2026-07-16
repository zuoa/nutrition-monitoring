import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
import zipfile
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
    abort_active_remote_downloads,
    get_remote_download_state_dir,
    read_remote_download_state,
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
        self.app.config.update(
            LOCAL_QWEN3_VL_EMBEDDING_REPO_ID="Qwen/Qwen3-VL-Embedding-2B",
            LOCAL_QWEN3_VL_EMBEDDING_MODEL_PATH=os.path.join(self.tmpdir.name, "models", "embedding"),
            LOCAL_QWEN3_VL_RERANKER_REPO_ID="Qwen/Qwen3-VL-Reranker-2B",
            LOCAL_QWEN3_VL_RERANKER_MODEL_PATH=os.path.join(self.tmpdir.name, "models", "reranker"),
        )
        shutil.rmtree(self.index_dir, ignore_errors=True)
        shutil.rmtree(get_remote_download_state_dir(self.app.config), ignore_errors=True)
        runtime_config_path = os.path.join(self.app.config["LOCAL_MODEL_STORAGE_PATH"], "runtime_config.json")
        if os.path.exists(runtime_config_path):
            os.unlink(runtime_config_path)
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

    def test_invalidate_marks_index_stale(self):
        response = self.client.post(
            "/v1/index/invalidate",
            headers=self._auth_headers(),
            json={"pipeline": "qwen"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(os.path.exists(os.path.join(self.index_dir, ".stale")))

    def test_confusion_report_analyzes_current_index(self):
        np.save(
            os.path.join(self.index_dir, "dish_sample_embeddings.npy"),
            np.asarray([[1.0, 0.0], [0.92, 0.1]], dtype=np.float32),
        )
        with open(os.path.join(self.index_dir, "dish_sample_metadata.json"), "w", encoding="utf-8") as f:
            json.dump([
                {"image_id": 1, "dish_id": 1, "dish_name": "红烧肉"},
                {"image_id": 2, "dish_id": 2, "dish_name": "糖醋排骨"},
            ], f, ensure_ascii=False)

        response = self.client.post(
            "/v1/index/confusion-report",
            headers=self._auth_headers(),
            json={"pipeline": "qwen"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual(data["pipeline"], "qwen")
        self.assertTrue(data["index_ready"])
        self.assertEqual(data["summary"]["high_risk_pair_count"], 1)
        self.assertEqual(data["pairs"][0]["left"]["dish_name"], "红烧肉")
        self.assertEqual(data["pairs"][0]["right"]["dish_name"], "糖醋排骨")

    def test_confusion_report_returns_empty_report_when_index_is_missing(self):
        os.unlink(os.path.join(self.index_dir, "dish_sample_embeddings.npy"))
        os.unlink(os.path.join(self.index_dir, "dish_sample_metadata.json"))

        response = self.client.post(
            "/v1/index/confusion-report",
            headers=self._auth_headers(),
            json={"pipeline": "qwen"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertFalse(data["index_ready"])
        self.assertEqual(data["summary"]["indexed_dish_count"], 0)
        self.assertEqual(data["pairs"], [])

    def test_activate_embedding_invalidates_qwen_index(self):
        target_path = os.path.join(self.app.config["LOCAL_MODEL_STORAGE_PATH"], "qwen3-vl-embedding-8b")
        os.makedirs(target_path, exist_ok=True)
        with open(os.path.join(target_path, "config.json"), "w", encoding="utf-8") as f:
            json.dump({}, f)

        response = self.client.post(
            "/v1/models/activate",
            headers=self._auth_headers(),
            json={"model_type": "embedding", "variant": "8B"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["data"]
        self.assertTrue(payload["requires_index_rebuild"])
        self.assertEqual(payload["invalidated_pipeline"], "qwen")
        self.assertTrue(os.path.exists(os.path.join(self.index_dir, ".stale")))

    def test_activate_reranker_keeps_index_ready(self):
        target_path = os.path.join(self.app.config["LOCAL_MODEL_STORAGE_PATH"], "qwen3-vl-reranker-8b")
        os.makedirs(target_path, exist_ok=True)
        with open(os.path.join(target_path, "config.json"), "w", encoding="utf-8") as f:
            json.dump({}, f)

        response = self.client.post(
            "/v1/models/activate",
            headers=self._auth_headers(),
            json={"model_type": "reranker", "variant": "8B"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["data"]
        self.assertFalse(payload["requires_index_rebuild"])
        self.assertIsNone(payload["invalidated_pipeline"])
        self.assertFalse(os.path.exists(os.path.join(self.index_dir, ".stale")))

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

    def test_successful_upload_stages_and_backs_up_inside_index_dir(self):
        matrix_buf = io.BytesIO()
        np.save(matrix_buf, np.asarray([[0.0, 1.0]], dtype=np.float32))
        matrix_buf.seek(0)
        metadata_buf = io.BytesIO(json.dumps([{
            "image_id": 2,
            "dish_id": 2,
            "dish_name": "新样图",
            "relative_image_path": "dish_2/sample_2.jpg",
        }]).encode("utf-8"))
        archive_buf = io.BytesIO()
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(b"new-sample")
            sample_path = tmp.name
        try:
            with mock.patch("app.inference_api.retrieval.tempfile.mkdtemp", wraps=tempfile.mkdtemp) as mocked_mkdtemp:
                with zipfile.ZipFile(archive_buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.write(sample_path, "dish_2/sample_2.jpg")
                archive_buf.seek(0)

                res = self.client.post(
                    "/v1/index/upload",
                    headers=self._auth_headers(),
                    data={
                        "matrix_file": (matrix_buf, "matrix.npy"),
                        "metadata_file": (metadata_buf, "metadata.json"),
                        "samples_archive": (archive_buf, "samples.zip"),
                    },
                    content_type="multipart/form-data",
                )

            self.assertEqual(res.status_code, 200)
            self.assertGreaterEqual(mocked_mkdtemp.call_count, 2)
            for call in mocked_mkdtemp.call_args_list[:2]:
                self.assertEqual(call.kwargs.get("dir"), self.index_dir)

            with open(os.path.join(self.index_dir, "dish_sample_metadata.json"), "r", encoding="utf-8") as f:
                metadata = json.load(f)
            self.assertEqual(metadata[0]["dish_id"], 2)
            self.assertTrue(metadata[0]["image_path"].startswith(os.path.join(self.index_dir, "sample_images")))
        finally:
            os.unlink(sample_path)

    def test_upload_can_reuse_existing_sample_images(self):
        matrix_buf = io.BytesIO()
        np.save(matrix_buf, np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32))
        matrix_buf.seek(0)
        metadata_buf = io.BytesIO(json.dumps([
            {
                "image_id": 1,
                "dish_id": 1,
                "dish_name": "旧样图",
                "relative_image_path": "dish_1/sample_1.jpg",
            },
            {
                "image_id": 2,
                "dish_id": 2,
                "dish_name": "新样图",
                "relative_image_path": "dish_2/sample_2.jpg",
            },
        ]).encode("utf-8"))
        archive_buf = io.BytesIO()
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(b"new-sample")
            sample_path = tmp.name
        try:
            with zipfile.ZipFile(archive_buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(sample_path, "dish_2/sample_2.jpg")
            archive_buf.seek(0)

            res = self.client.post(
                "/v1/index/upload",
                headers=self._auth_headers(),
                data={
                    "reuse_existing_samples": "1",
                    "matrix_file": (matrix_buf, "matrix.npy"),
                    "metadata_file": (metadata_buf, "metadata.json"),
                    "samples_archive": (archive_buf, "samples.zip"),
                },
                content_type="multipart/form-data",
            )

            self.assertEqual(res.status_code, 200)
            reused_path = os.path.join(self.index_dir, "sample_images", "dish_1", "sample_1.jpg")
            new_path = os.path.join(self.index_dir, "sample_images", "dish_2", "sample_2.jpg")
            self.assertTrue(os.path.exists(reused_path))
            self.assertTrue(os.path.exists(new_path))
            with open(reused_path, "rb") as f:
                self.assertEqual(f.read(), b"old-sample")
            with open(new_path, "rb") as f:
                self.assertEqual(f.read(), b"new-sample")
        finally:
            os.unlink(sample_path)

    def test_upload_reuse_existing_requires_missing_sample_archive_members(self):
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
                "reuse_existing_samples": "1",
                "matrix_file": (matrix_buf, "matrix.npy"),
                "metadata_file": (metadata_buf, "metadata.json"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(res.status_code, 400)
        self.assertIn("samples_archive 缺少文件", res.get_json()["message"])
        self.assertTrue(os.path.exists(self.old_sample_path))

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

    def test_force_download_aborts_existing_active_task_and_starts_new(self):
        old_task_id = "stuck-running-task"
        write_remote_download_state(self.app.config, old_task_id, {
            "task_id": old_task_id,
            "model_type": "embedding",
            "variant": "2B",
            "repo_id": "Qwen/Qwen3-VL-Embedding-2B",
            "target_path": os.path.join(self.app.config["LOCAL_MODEL_STORAGE_PATH"], "qwen3-vl-embedding-2b"),
            "hf_endpoint": "https://hf-mirror.com",
            "status": "running",
            "progress_percent": 10.0,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "downloaded_files": 0,
            "total_files": 0,
            "status_text": "正在下载模型文件",
            "error_message": "",
            "started_at": "2026-04-02T00:00:00Z",
            "finished_at": None,
            "created_at": "2026-04-02T00:00:00Z",
            "worker_pid": 999999,
        })

        with (
            mock.patch("app.inference_api.retrieval.ensure_remote_download_worker") as ensure_worker,
            mock.patch("app.inference_api.model_download_tasks.os.kill") as kill_mock,
        ):
            res = self.client.post(
                "/v1/models/download",
                headers=self._auth_headers(),
                json={"model_type": "embedding", "variant": "2B", "force": True},
            )

        self.assertEqual(res.status_code, 202)
        data = res.get_json()["data"]
        new_task_id = data["task_id"]
        self.assertNotEqual(new_task_id, old_task_id)
        self.assertEqual(data["status"], "pending")

        kill_mock.assert_called_once_with(999999, mock.ANY)
        aborted = read_remote_download_state(self.app.config, old_task_id)
        self.assertEqual(aborted["status"], "failed")
        self.assertIn("强制", aborted["error_message"])
        # worker should only be spawned for the fresh task
        ensure_worker.assert_called_once()
        self.assertEqual(ensure_worker.call_args.args[1], new_task_id)

    def test_abort_active_remote_downloads_terminates_worker_and_marks_failed(self):
        task_id = "abort-target"
        write_remote_download_state(self.app.config, task_id, {
            "task_id": task_id,
            "model_type": "embedding",
            "variant": "2B",
            "repo_id": "Qwen/Qwen3-VL-Embedding-2B",
            "target_path": os.path.join(self.app.config["LOCAL_MODEL_STORAGE_PATH"], "qwen3-vl-embedding-2b"),
            "hf_endpoint": "https://hf-mirror.com",
            "status": "running",
            "worker_pid": 12345,
        })

        with mock.patch("app.inference_api.model_download_tasks.os.kill") as kill_mock:
            aborted = abort_active_remote_downloads(
                self.app.config,
                model_type="embedding",
                variant="2B",
            )

        self.assertEqual(len(aborted), 1)
        kill_mock.assert_called_once_with(12345, mock.ANY)
        state = read_remote_download_state(self.app.config, task_id)
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["error_message"], "被强制重新下载覆盖")

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

    def test_full_reports_actual_pipeline_when_index_is_empty(self):
        class FakeEmbeddingRetrievalService:
            pipeline = "visual"

            def __init__(self, config):
                pass

            def full(self, image_path, *, candidate_dishes, regions):
                raise ValueError("本地 embedding 索引为空，请先上传样图并生成 embedding")

        with mock.patch("app.inference_api.retrieval.EmbeddingRetrievalService", FakeEmbeddingRetrievalService):
            res = self.client.post(
                "/v1/full",
                headers=self._auth_headers(),
                data={
                    "candidate_dishes": json.dumps([{"id": 1, "name": "红烧肉"}]),
                    "image_file": (io.BytesIO(b"fake-image"), "meal.jpg"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(res.status_code, 400)
        self.assertIn("实际 pipeline=visual", res.get_json()["message"])


if __name__ == "__main__":
    unittest.main()
