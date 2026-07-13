import importlib.util
import os
import sys
import tempfile
import types
import unittest
from unittest import mock


MODULE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "app",
    "tasks",
    "embeddings.py",
)


def load_embeddings_task_module():
    celery_app_module = types.ModuleType("celery_app")

    class FakeCelery:
        def task(self, *args, **kwargs):
            def decorator(fn):
                return fn
            return decorator

    celery_app_module.celery = FakeCelery()

    app_module = types.ModuleType("app")
    app_module.db = types.SimpleNamespace(
        session=types.SimpleNamespace(
            add=lambda obj: None,
            commit=lambda: None,
        ),
    )

    models_module = types.ModuleType("app.models")
    models_module.Dish = type("Dish", (), {})
    models_module.DishSampleImage = type("DishSampleImage", (), {})
    models_module.EmbeddingStatusEnum = types.SimpleNamespace(
        processing="processing",
        ready="ready",
        failed="failed",
    )
    models_module.TaskLog = type("TaskLog", (), {})

    inference_client_module = types.ModuleType("app.services.inference_client")

    class _InferenceServiceError(RuntimeError):
        pass

    inference_client_module.InferenceServiceError = _InferenceServiceError
    inference_client_module.make_retrieval_client = lambda config: None

    runtime_config_module = types.ModuleType("app.services.runtime_config")
    runtime_config_module.get_effective_config = lambda config: dict(config)

    stubbed_modules = {
        "celery_app": celery_app_module,
        "app": app_module,
        "app.models": models_module,
        "app.services.inference_client": inference_client_module,
        "app.services.runtime_config": runtime_config_module,
    }

    with mock.patch.dict(sys.modules, stubbed_modules, clear=False):
        spec = importlib.util.spec_from_file_location("test_embeddings_task", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module


class EmbeddingTasksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_embeddings_task_module()

    def test_remote_rebuild_passes_embedding_instruction_to_embed_api(self):
        calls = []

        class FakeRetrievalClient:
            def post_file(self, path, *, image_path, data=None):
                calls.append({
                    "path": path,
                    "image_path": image_path,
                    "data": data,
                })
                return {
                    "embeddings": [{"vector": [1.0, 0.0]}],
                    "model_version": "qwen3_vl_embedding",
                }

        with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
            image = types.SimpleNamespace(
                id=1,
                dish_id=7,
                dish=types.SimpleNamespace(name="红烧肉"),
                image_path=tmp.name,
                original_filename="sample.jpg",
                embedding_status=None,
                embedding_model=None,
                embedding_version=None,
                embedding_input_hash=None,
                embedding_vector=None,
                embedding_updated_at=None,
                error_message=None,
            )
            task_log = types.SimpleNamespace(
                status=None,
                total_count=0,
                success_count=0,
                error_count=0,
                meta={},
                finished_at=None,
            )

            with mock.patch.object(self.module, "make_retrieval_client", return_value=FakeRetrievalClient()), \
                 mock.patch.object(self.module, "_build_active_sample_images", return_value=[image]), \
                 mock.patch.object(self.module, "_upload_remote_index", return_value={
                     "index_ready": True,
                     "embedding_count": 1,
                     "index_dir": "/tmp/index",
                     "sample_image_root": "/tmp/index/sample_images",
                 }):
                result = self.module._rebuild_sample_embeddings_remote(
                    {"LOCAL_QWEN3_VL_EMBEDDING_INSTRUCTION": "检索食堂菜品样图。"},
                    task_log,
                )

        self.assertEqual(calls, [{
            "path": "/v1/embed",
            "image_path": mock.ANY,
            "data": {"instruction": "检索食堂菜品样图。"},
        }])
        self.assertEqual(result["ready"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["generated"], 1)
        self.assertEqual(result["reused"], 0)
        self.assertEqual(image.embedding_vector, [1.0, 0.0])
        self.assertTrue(image.embedding_input_hash)
        self.assertEqual(task_log.meta["stage"], "completed")
        self.assertEqual(task_log.meta["progress_percent"], 100)
        self.assertEqual(task_log.meta["processed"], 1)

    def test_remote_rebuild_reuses_cached_embedding_vector(self):
        calls = []

        class FakeRetrievalClient:
            def post_file(self, path, *, image_path, data=None):
                calls.append(path)
                return {
                    "embeddings": [{"vector": [0.0, 1.0]}],
                    "model_version": "qwen3_vl_embedding",
                }

        with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
            config = {"LOCAL_QWEN3_VL_EMBEDDING_INSTRUCTION": "检索食堂菜品样图。"}
            input_hash = self.module._build_embedding_input_hash(config, tmp.name, "检索食堂菜品样图。")
            cached_image = types.SimpleNamespace(
                id=1,
                dish_id=7,
                dish=types.SimpleNamespace(name="红烧肉"),
                image_path=tmp.name,
                original_filename="sample.jpg",
                embedding_status=self.module.EmbeddingStatusEnum.ready,
                embedding_model="retrieval-api",
                embedding_version="qwen3_vl_embedding",
                embedding_input_hash=input_hash,
                embedding_vector=[1.0, 0.0],
                embedding_updated_at=None,
                error_message=None,
            )
            task_log = types.SimpleNamespace(
                status=None,
                total_count=0,
                success_count=0,
                error_count=0,
                meta={},
                finished_at=None,
            )

            uploaded = {}

            def fake_upload(config_arg, *, metadata, matrix):
                uploaded["metadata"] = metadata
                uploaded["matrix"] = matrix
                return {
                    "index_ready": True,
                    "embedding_count": int(matrix.shape[0]),
                    "index_dir": "/tmp/index",
                    "sample_image_root": "/tmp/index/sample_images",
                }

            with mock.patch.object(self.module, "make_retrieval_client", return_value=FakeRetrievalClient()), \
                 mock.patch.object(self.module, "_build_active_sample_images", return_value=[cached_image]), \
                 mock.patch.object(self.module, "_upload_remote_index", side_effect=fake_upload):
                result = self.module._rebuild_sample_embeddings_remote(config, task_log)

        self.assertEqual(calls, [])
        self.assertEqual(result["ready"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["generated"], 0)
        self.assertEqual(result["reused"], 1)
        self.assertEqual(uploaded["metadata"][0]["image_id"], cached_image.id)
        self.assertEqual(uploaded["matrix"].tolist(), [[1.0, 0.0]])

    def test_remote_rebuild_regenerates_when_cached_hash_is_stale(self):
        calls = []

        class FakeRetrievalClient:
            def post_file(self, path, *, image_path, data=None):
                calls.append(path)
                return {
                    "embeddings": [{"vector": [0.0, 1.0]}],
                    "model_version": "qwen3_vl_embedding",
                }

        with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
            image = types.SimpleNamespace(
                id=1,
                dish_id=7,
                dish=types.SimpleNamespace(name="红烧肉"),
                image_path=tmp.name,
                original_filename="sample.jpg",
                embedding_status=self.module.EmbeddingStatusEnum.ready,
                embedding_model="retrieval-api",
                embedding_version="qwen3_vl_embedding",
                embedding_input_hash="stale",
                embedding_vector=[1.0, 0.0],
                embedding_updated_at=None,
                error_message=None,
            )
            task_log = types.SimpleNamespace(
                status=None,
                total_count=0,
                success_count=0,
                error_count=0,
                meta={},
                finished_at=None,
            )

            with mock.patch.object(self.module, "make_retrieval_client", return_value=FakeRetrievalClient()), \
                 mock.patch.object(self.module, "_build_active_sample_images", return_value=[image]), \
                 mock.patch.object(self.module, "_upload_remote_index", return_value={
                     "index_ready": True,
                     "embedding_count": 1,
                     "index_dir": "/tmp/index",
                     "sample_image_root": "/tmp/index/sample_images",
                 }):
                result = self.module._rebuild_sample_embeddings_remote(
                    {"LOCAL_QWEN3_VL_EMBEDDING_INSTRUCTION": "检索食堂菜品样图。"},
                    task_log,
                )

        self.assertEqual(calls, ["/v1/embed"])
        self.assertEqual(result["generated"], 1)
        self.assertEqual(result["reused"], 0)
        self.assertEqual(image.embedding_vector, [0.0, 1.0])
        self.assertNotEqual(image.embedding_input_hash, "stale")

    def test_upload_remote_index_sends_only_changed_samples(self):
        calls = []

        class FakeRetrievalClient:
            def post_form_files(self, path, *, data=None, file_paths=None):
                archive_entries = []
                samples_archive = (file_paths or {}).get("samples_archive")
                if samples_archive:
                    import zipfile

                    with zipfile.ZipFile(samples_archive) as archive:
                        archive_entries = sorted(archive.namelist())
                calls.append({
                    "path": path,
                    "data": data,
                    "archive_entries": archive_entries,
                })
                return {"index_ready": True, "embedding_count": 2}

        with tempfile.TemporaryDirectory() as tmpdir:
            old_path = os.path.join(tmpdir, "old.jpg")
            new_path = os.path.join(tmpdir, "new.jpg")
            with open(old_path, "wb") as f:
                f.write(b"old")
            with open(new_path, "wb") as f:
                f.write(b"new")

            metadata = [
                {
                    "image_id": 1,
                    "dish_id": 1,
                    "dish_name": "旧样图",
                    "relative_image_path": "dish_1/sample_1.jpg",
                    "_source_image_path": old_path,
                    "_upload_sample": False,
                },
                {
                    "image_id": 2,
                    "dish_id": 2,
                    "dish_name": "新样图",
                    "relative_image_path": "dish_2/sample_2.jpg",
                    "_source_image_path": new_path,
                    "_upload_sample": True,
                },
            ]

            with mock.patch.object(self.module, "make_retrieval_client", return_value=FakeRetrievalClient()):
                result = self.module._upload_remote_index(
                    {},
                    metadata=metadata,
                    matrix=self.module.np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=self.module.np.float32),
                )

        self.assertEqual(result["embedding_count"], 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["path"], "/v1/index/upload")
        self.assertEqual(calls[0]["data"], {"reuse_existing_samples": "1"})
        self.assertEqual(calls[0]["archive_entries"], ["dish_2/sample_2.jpg"])

    def test_upload_remote_index_retries_full_upload_when_remote_cache_misses_sample(self):
        calls = []
        error_cls = self.module.InferenceServiceError

        class FakeRetrievalClient:
            def post_form_files(self, path, *, data=None, file_paths=None):
                archive_entries = []
                samples_archive = (file_paths or {}).get("samples_archive")
                if samples_archive:
                    import zipfile

                    with zipfile.ZipFile(samples_archive) as archive:
                        archive_entries = sorted(archive.namelist())
                calls.append(archive_entries)
                if len(calls) == 1:
                    raise error_cls("samples_archive 缺少文件: dish_1/sample_1.jpg")
                return {"index_ready": True, "embedding_count": 2}

        with tempfile.TemporaryDirectory() as tmpdir:
            old_path = os.path.join(tmpdir, "old.jpg")
            new_path = os.path.join(tmpdir, "new.jpg")
            with open(old_path, "wb") as f:
                f.write(b"old")
            with open(new_path, "wb") as f:
                f.write(b"new")

            metadata = [
                {
                    "image_id": 1,
                    "dish_id": 1,
                    "dish_name": "旧样图",
                    "relative_image_path": "dish_1/sample_1.jpg",
                    "_source_image_path": old_path,
                    "_upload_sample": False,
                },
                {
                    "image_id": 2,
                    "dish_id": 2,
                    "dish_name": "新样图",
                    "relative_image_path": "dish_2/sample_2.jpg",
                    "_source_image_path": new_path,
                    "_upload_sample": True,
                },
            ]

            with mock.patch.object(self.module, "make_retrieval_client", return_value=FakeRetrievalClient()):
                result = self.module._upload_remote_index(
                    {},
                    metadata=metadata,
                    matrix=self.module.np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=self.module.np.float32),
                )

        self.assertEqual(result["embedding_count"], 2)
        self.assertEqual(calls, [
            ["dish_2/sample_2.jpg"],
            ["dish_1/sample_1.jpg", "dish_2/sample_2.jpg"],
        ])


if __name__ == "__main__":
    unittest.main()
