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

    local_model_manager_module = types.ModuleType("app.services.local_model_manager")
    local_model_manager_module.is_local_model_ready = lambda _: True

    wrappers_module = types.ModuleType("app.services.qwen3_vl_local_wrappers")
    wrappers_module.Qwen3VLEmbedder = type("Qwen3VLEmbedder", (), {})
    wrappers_module.Qwen3VLReranker = type("Qwen3VLReranker", (), {})

    region_proposal_module = types.ModuleType("app.services.region_proposal")

    class DefaultRegionProposalService:
        def __init__(self, config):
            self.config = config

        def propose_regions(self, image_path: str):
            return {"proposals": []}

    region_proposal_module.RegionProposalService = DefaultRegionProposalService

    runtime_config_module = types.ModuleType("app.services.runtime_config")
    runtime_config_module.get_effective_config = lambda config: dict(config)

    stubbed_modules = {
        "app": app_module,
        "app.models": models_module,
        "app.services": services_module,
        "app.services.local_model_manager": local_model_manager_module,
        "app.services.qwen3_vl_local_wrappers": wrappers_module,
        "app.services.region_proposal": region_proposal_module,
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

    def test_detect_regions_skips_when_model_not_ready(self):
        class FailingRegionProposalService:
            def __init__(self, config):
                raise AssertionError("RegionProposalService should not be constructed when model is not ready")

        self.module.is_local_model_ready = lambda _: False
        self.module.RegionProposalService = FailingRegionProposalService

        service = self.module.LocalEmbeddingIndexService(
            {"LOCAL_REGION_PROPOSAL_MODEL_PATH": "/tmp/grounding-dino-tiny"},
        )

        self.assertEqual(service.detect_regions("/tmp/meal.jpg"), [])

    def test_detect_regions_falls_back_when_region_service_raises(self):
        class FailingRegionProposalService:
            def __init__(self, config):
                self.config = config

            def propose_regions(self, image_path: str):
                raise RuntimeError("model files missing")

        self.module.is_local_model_ready = lambda _: True
        self.module.RegionProposalService = FailingRegionProposalService

        service = self.module.LocalEmbeddingIndexService(
            {"LOCAL_REGION_PROPOSAL_MODEL_PATH": "/tmp/grounding-dino-tiny"},
        )

        self.assertEqual(service.detect_regions("/tmp/meal.jpg"), [])

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
