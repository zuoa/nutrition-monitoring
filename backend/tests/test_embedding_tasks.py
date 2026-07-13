import importlib.util
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

import numpy  # noqa: F401 - keep NumPy loaded outside the temporary sys.modules patch


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
        pending="pending",
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

    def test_visual_rebuild_persists_visual_status_only_after_index_upload(self):
        upload_observation = {}

        class FakeRetrievalClient:
            def post_file(self, path, *, image_path, data=None):
                self_outer.assertEqual(path, "/v1/embed")
                self_outer.assertEqual(data, {"pipeline": "visual"})
                return {
                    "embeddings": [{
                        "vector": [1.0, 0.0],
                        "patch_vectors": [[1.0, 0.0], [0.0, 1.0]],
                    }],
                    "model_version": "siglip2+dinov3-v1",
                }

        self_outer = self
        with tempfile.TemporaryDirectory() as cache_root, tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
            image = types.SimpleNamespace(
                id=11,
                dish_id=7,
                dish=types.SimpleNamespace(name="红烧肉"),
                image_path=tmp.name,
                original_filename="sample.jpg",
                embedding_status=self.module.EmbeddingStatusEnum.ready,
                embedding_model="qwen",
                embedding_version="qwen-v1",
                embedding_input_hash="qwen-hash",
                embedding_vector=[0.0, 1.0],
                embedding_updated_at=None,
                error_message=None,
                visual_embedding_status=self.module.EmbeddingStatusEnum.pending,
                visual_embedding_input_hash=None,
                visual_embedding_version=None,
                visual_embedding_updated_at=None,
                visual_error_message=None,
            )
            task_log = types.SimpleNamespace(
                status=None,
                total_count=0,
                success_count=0,
                error_count=0,
                meta={},
                finished_at=None,
            )

            def fake_upload(config_arg, *, metadata, matrix, pipeline, patch_matrix):
                upload_observation["status_during_upload"] = image.visual_embedding_status
                upload_observation["pipeline"] = pipeline
                upload_observation["patch_shape"] = patch_matrix.shape
                return {
                    "index_ready": True,
                    "embedding_count": 1,
                    "index_dir": "/tmp/index/visual",
                    "sample_image_root": "/tmp/index/visual/sample_images",
                }

            with mock.patch.object(self.module, "make_retrieval_client", return_value=FakeRetrievalClient()), \
                 mock.patch.object(self.module, "_build_active_sample_images", return_value=[image]), \
                 mock.patch.object(self.module, "_upload_remote_index", side_effect=fake_upload):
                result = self.module._rebuild_sample_embeddings_remote(
                    {"IMAGE_STORAGE_PATH": cache_root},
                    task_log,
                    pipeline="visual",
                )

        self.assertEqual(upload_observation["status_during_upload"], self.module.EmbeddingStatusEnum.processing)
        self.assertEqual(upload_observation["pipeline"], "visual")
        self.assertEqual(upload_observation["patch_shape"], (2, 2))
        self.assertEqual(image.visual_embedding_status, self.module.EmbeddingStatusEnum.ready)
        self.assertIsNotNone(image.visual_embedding_input_hash)
        self.assertEqual(image.visual_embedding_version, "siglip2+dinov3-v1")
        self.assertIsNotNone(image.visual_embedding_updated_at)
        self.assertEqual(image.embedding_status, self.module.EmbeddingStatusEnum.ready)
        self.assertEqual(image.embedding_version, "qwen-v1")
        self.assertEqual(result["pipeline"], "visual")
        self.assertEqual(result["ready"], 1)

    def test_visual_rebuild_reuses_old_features_and_embeds_only_new_sample(self):
        embed_calls = []
        uploads = []

        class FakeRetrievalClient:
            def post_file(self, path, *, image_path, data=None):
                embed_calls.append(image_path)
                return {
                    "embeddings": [{
                        "vector": [1.0, 0.0] if image_path.endswith("old.jpg") else [0.0, 1.0],
                        "patch_vectors": (
                            [[1.0, 0.0], [0.5, 0.5]]
                            if image_path.endswith("old.jpg")
                            else [[0.0, 1.0], [0.25, 0.75]]
                        ),
                    }],
                    "model_version": "siglip2+dinov3-v1",
                }

        def make_image(image_id, image_path, name):
            return types.SimpleNamespace(
                id=image_id,
                dish_id=image_id,
                dish=types.SimpleNamespace(name=name),
                image_path=image_path,
                original_filename=os.path.basename(image_path),
                embedding_status=self.module.EmbeddingStatusEnum.ready,
                embedding_model="qwen",
                embedding_version="qwen-v1",
                embedding_input_hash="qwen-hash",
                embedding_vector=[1.0, 0.0],
                embedding_updated_at=None,
                error_message=None,
                visual_embedding_status=self.module.EmbeddingStatusEnum.pending,
                visual_embedding_input_hash=None,
                visual_embedding_version=None,
                visual_embedding_updated_at=None,
                visual_error_message=None,
            )

        def make_task_log():
            return types.SimpleNamespace(
                status=None,
                total_count=0,
                success_count=0,
                error_count=0,
                meta={},
                finished_at=None,
            )

        def fake_upload(config_arg, *, metadata, matrix, pipeline, patch_matrix):
            uploads.append({
                "metadata": [dict(item) for item in metadata],
                "matrix": matrix.copy(),
                "patch_matrix": patch_matrix.copy(),
            })
            return {
                "index_ready": True,
                "embedding_count": int(matrix.shape[0]),
                "index_dir": "/tmp/index/visual",
                "sample_image_root": "/tmp/index/visual/sample_images",
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            old_path = os.path.join(tmpdir, "old.jpg")
            new_path = os.path.join(tmpdir, "new.jpg")
            with open(old_path, "wb") as fh:
                fh.write(b"old-image")
            with open(new_path, "wb") as fh:
                fh.write(b"new-image")
            old_image = make_image(1, old_path, "旧样图")
            new_image = make_image(2, new_path, "新样图")
            config = {
                "IMAGE_STORAGE_PATH": tmpdir,
                "LOCAL_SIGLIP2_MODEL_PATH": "/models/siglip2",
                "LOCAL_DINOV3_MODEL_PATH": "/models/dinov3",
                "VISUAL_PATCH_MAX_TOKENS": 256,
            }

            with mock.patch.object(self.module, "make_retrieval_client", return_value=FakeRetrievalClient()), \
                 mock.patch.object(self.module, "_upload_remote_index", side_effect=fake_upload), \
                 mock.patch.object(self.module, "_build_active_sample_images", return_value=[old_image]):
                first_result = self.module._rebuild_sample_embeddings_remote(
                    config,
                    make_task_log(),
                    pipeline="visual",
                )

            with mock.patch.object(self.module, "make_retrieval_client", return_value=FakeRetrievalClient()), \
                 mock.patch.object(self.module, "_upload_remote_index", side_effect=fake_upload), \
                 mock.patch.object(self.module, "_build_active_sample_images", return_value=[old_image, new_image]):
                second_result = self.module._rebuild_sample_embeddings_remote(
                    config,
                    make_task_log(),
                    pipeline="visual",
                )

            old_cache_path = self.module._visual_embedding_cache_path(
                config,
                old_image.visual_embedding_input_hash,
            )
            self.assertTrue(os.path.exists(old_cache_path))

        self.assertEqual(first_result["generated"], 1)
        self.assertEqual(first_result["reused"], 0)
        self.assertEqual(second_result["generated"], 1)
        self.assertEqual(second_result["reused"], 1)
        self.assertEqual(embed_calls, [mock.ANY, mock.ANY])
        self.assertTrue(embed_calls[0].endswith("old.jpg"))
        self.assertTrue(embed_calls[1].endswith("new.jpg"))
        self.assertEqual(uploads[-1]["matrix"].tolist(), [[1.0, 0.0], [0.0, 1.0]])
        self.assertEqual(uploads[-1]["patch_matrix"].shape, (4, 2))
        self.assertEqual(
            [(item["patch_offset"], item["patch_count"]) for item in uploads[-1]["metadata"]],
            [(0, 2), (2, 2)],
        )

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
                embedding_status=self.module.EmbeddingStatusEnum.pending,
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
        self.assertEqual(cached_image.embedding_status, self.module.EmbeddingStatusEnum.ready)
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
