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


if __name__ == "__main__":
    unittest.main()
