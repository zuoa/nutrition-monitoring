import importlib.util
import os
import sys
import types
import unittest
from unittest import mock


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


if __name__ == "__main__":
    unittest.main()
