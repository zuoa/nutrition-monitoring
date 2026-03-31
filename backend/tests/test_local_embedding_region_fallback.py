import importlib.util
import os
import sys
import types
import unittest
from unittest import mock

import numpy as np
import tempfile


MODULE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "app",
    "services",
    "local_embedding.py",
)


def load_local_embedding_module():
    app_module = types.ModuleType("app")
    app_module.__path__ = []
    services_module = types.ModuleType("app.services")
    services_module.__path__ = []

    models_module = types.ModuleType("app.models")
    models_module.Dish = type("Dish", (), {})
    models_module.DishSampleImage = type("DishSampleImage", (), {})
    models_module.EmbeddingStatusEnum = types.SimpleNamespace(
        processing="processing",
        ready="ready",
        failed="failed",
    )

    wrappers_module = types.ModuleType("app.services.qwen3_vl_local_wrappers")
    wrappers_module.Qwen3VLEmbedder = type("Qwen3VLEmbedder", (), {})
    wrappers_module.Qwen3VLReranker = type("Qwen3VLReranker", (), {})

    inference_client_module = types.ModuleType("app.services.inference_client")

    class DefaultInferenceServiceError(RuntimeError):
        pass

    class DefaultDetectorClient:
        def post_file(self, path: str, *, image_path: str, data=None):
            return {"backend": "yolo", "regions": []}

    inference_client_module.InferenceServiceError = DefaultInferenceServiceError
    inference_client_module.make_detector_client = lambda config: DefaultDetectorClient()

    runtime_config_module = types.ModuleType("app.services.runtime_config")
    runtime_config_module.get_effective_config = lambda config: dict(config)

    stubbed_modules = {
        "app": app_module,
        "app.models": models_module,
        "app.services": services_module,
        "app.services.inference_client": inference_client_module,
        "app.services.qwen3_vl_local_wrappers": wrappers_module,
        "app.services.runtime_config": runtime_config_module,
    }

    with mock.patch.dict(sys.modules, stubbed_modules, clear=False):
        spec = importlib.util.spec_from_file_location("test_local_embedding", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module


class RegionProposalFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_local_embedding_module()
        cls.inference_error = cls.module.InferenceServiceError

    def test_detect_regions_falls_back_when_detector_raises(self):
        inference_error = self.inference_error

        class FailingDetectorClient:
            def post_file(self, path: str, *, image_path: str, data=None):
                raise inference_error("detector unavailable")

        service = self.module.LocalEmbeddingIndexService({})
        service._last_region_backend = "stale"

        with mock.patch.object(self.module, "make_detector_client", return_value=FailingDetectorClient()):
            self.assertEqual(service.detect_regions("/tmp/meal.jpg"), [])
            self.assertEqual(service._build_region_backend_label(), "full_image")

    def test_detect_regions_maps_detector_regions(self):
        class FakeDetectorClient:
            def post_file(self, path: str, *, image_path: str, data=None):
                return {
                    "backend": "yolo",
                    "regions": [
                        {
                            "index": 3,
                            "bbox": {"x1": 10, "y1": 20, "x2": 110, "y2": 160},
                            "score": 0.9,
                            "source": "yolo",
                        },
                    ],
                }

        service = self.module.LocalEmbeddingIndexService({})

        with mock.patch.object(self.module, "make_detector_client", return_value=FakeDetectorClient()):
            regions = service.detect_regions("/tmp/meal.jpg")

        self.assertEqual(regions, [{
            "index": 3,
            "bbox": {"x1": 10, "y1": 20, "x2": 110, "y2": 160},
            "confidence": 0.9,
            "source": "yolo",
        }])
        self.assertEqual(service._build_region_backend_label(), "yolo")

    def test_detect_regions_passes_backend_configured_max_regions(self):
        calls = []

        class FakeDetectorClient:
            def post_file(self, path: str, *, image_path: str, data=None):
                calls.append({
                    "path": path,
                    "image_path": image_path,
                    "data": data,
                })
                return {"backend": "yolo", "regions": []}

        service = self.module.LocalEmbeddingIndexService({"YOLO_MAX_REGIONS": 9})

        with mock.patch.object(self.module, "make_detector_client", return_value=FakeDetectorClient()):
            service.detect_regions("/tmp/meal.jpg")

        self.assertEqual(calls, [{
            "path": "/v1/detect",
            "image_path": "/tmp/meal.jpg",
            "data": {"max_regions": 9},
        }])

    def test_to_numpy_vector_casts_bfloat16_tensor_before_numpy(self):
        service = self.module.LocalEmbeddingIndexService({})
        tensor = FakeBFloat16Tensor([[1.0, 2.0, 3.0]])

        vector = service._to_numpy_vector(tensor)

        self.assertEqual(vector.dtype.name, "float32")
        self.assertEqual(vector.tolist(), [1.0, 2.0, 3.0])

    def test_coerce_scores_casts_bfloat16_tensor_before_numpy(self):
        service = self.module.LocalEmbeddingIndexService({})
        tensor = FakeBFloat16Tensor([0.25, 0.75])

        scores = service._coerce_scores(tensor, expected=3)

        self.assertEqual(scores, [0.25, 0.75, 0.0])

    def test_embedder_is_cached_by_model_path(self):
        created = []

        class FakeEmbedder:
            def __init__(self, model_name_or_path):
                created.append(model_name_or_path)

        self.module.Qwen3VLEmbedder = FakeEmbedder
        self.module._EMBEDDER_CACHE.clear()

        first = self.module.LocalEmbeddingIndexService({
            "LOCAL_QWEN3_VL_EMBEDDING_MODEL_PATH": "/tmp/models/embedder",
        })
        second = self.module.LocalEmbeddingIndexService({
            "LOCAL_QWEN3_VL_EMBEDDING_MODEL_PATH": "/tmp/models/embedder",
        })

        first_embedder = first._get_embedder()
        second_embedder = second._get_embedder()

        self.assertIs(first_embedder, second_embedder)
        self.assertEqual(created, ["/tmp/models/embedder"])

    def test_reranker_is_cached_by_model_path(self):
        created = []

        class FakeReranker:
            def __init__(self, model_name_or_path):
                created.append(model_name_or_path)

        self.module.Qwen3VLReranker = FakeReranker
        self.module._RERANKER_CACHE.clear()

        first = self.module.LocalEmbeddingIndexService({
            "LOCAL_QWEN3_VL_RERANKER_MODEL_PATH": "/tmp/models/reranker",
        })
        second = self.module.LocalEmbeddingIndexService({
            "LOCAL_QWEN3_VL_RERANKER_MODEL_PATH": "/tmp/models/reranker",
        })

        first_reranker = first._get_reranker()
        second_reranker = second._get_reranker()

        self.assertIs(first_reranker, second_reranker)
        self.assertEqual(created, ["/tmp/models/reranker"])

    def test_embed_regions_returns_bbox_and_vector(self):
        service = self.module.LocalEmbeddingIndexService({})
        service.embed_image_file = lambda image_path, instruction=None: np.asarray([1.0, 2.0], dtype=np.float32)

        with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
            embedded = service.embed_regions(tmp.name, bboxes=[None])

        self.assertEqual(len(embedded), 1)
        self.assertEqual(embedded[0]["bbox"], None)
        self.assertEqual(embedded[0]["vector"].tolist(), [1.0, 2.0])

    def test_build_model_version_uses_current_components_only(self):
        service = self.module.LocalEmbeddingIndexService({})

        self.assertEqual(service._build_model_version(), "qwen3_vl_embedding")


class FakeBFloat16Tensor:
    def __init__(self, data, *, casted: bool = False):
        self._data = data
        self._casted = casted

    def detach(self):
        return self

    def cpu(self):
        return self

    def float(self):
        return FakeBFloat16Tensor(self._data, casted=True)

    def numpy(self):
        if not self._casted:
            raise TypeError("Got unsupported ScalarType BFloat16")
        return np.asarray(self._data, dtype=np.float32)


if __name__ == "__main__":
    unittest.main()
