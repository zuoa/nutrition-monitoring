import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock


INFERENCE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PIPELINE_PATH = os.path.join(INFERENCE_DIR, "app", "services", "inference_pipeline.py")
RUNTIME_CONFIG_PATH = os.path.join(INFERENCE_DIR, "app", "services", "runtime_config.py")


class _LocalEmbeddingIndexService:
    def __init__(self, config):
        self.config = dict(config)


class _VisualEmbeddingIndexService:
    def __init__(self, config):
        self.config = dict(config)


def _load_module(module_name: str, path: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InferencePipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime_config_module = _load_module(
            "runtime_config_under_test",
            RUNTIME_CONFIG_PATH,
        )

    def _load_pipeline_module(self):
        local_embedding = types.ModuleType("app.services.local_embedding")
        local_embedding.LocalEmbeddingIndexService = _LocalEmbeddingIndexService
        visual_embedding = types.ModuleType("app.services.visual_embedding")
        visual_embedding.VisualEmbeddingIndexService = _VisualEmbeddingIndexService

        with mock.patch.dict(sys.modules, {
            "app.services.local_embedding": local_embedding,
            "app.services.runtime_config": self.runtime_config_module,
            "app.services.visual_embedding": visual_embedding,
        }):
            return _load_module("inference_pipeline_under_test", PIPELINE_PATH)

    def test_persisted_runtime_pipeline_wins_after_process_restart(self):
        module = self._load_pipeline_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_path = os.path.join(tmpdir, "runtime_config.json")
            with open(runtime_path, "w", encoding="utf-8") as f:
                json.dump({"LOCAL_RETRIEVAL_PIPELINE": "visual"}, f)

            service = module.EmbeddingRetrievalService({
                "LOCAL_RETRIEVAL_PIPELINE": "qwen",
                "LOCAL_RUNTIME_CONFIG_PATH": runtime_path,
                "LOCAL_EMBEDDING_INDEX_DIR": os.path.join(tmpdir, "index"),
            })

        self.assertEqual(service.pipeline, "visual")
        self.assertIsInstance(service.index_service, _VisualEmbeddingIndexService)
        self.assertEqual(service.index_service.config["LOCAL_RETRIEVAL_PIPELINE"], "visual")

    def test_explicit_pipeline_still_overrides_persisted_runtime_pipeline(self):
        module = self._load_pipeline_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_path = os.path.join(tmpdir, "runtime_config.json")
            with open(runtime_path, "w", encoding="utf-8") as f:
                json.dump({"LOCAL_RETRIEVAL_PIPELINE": "visual"}, f)

            service = module.EmbeddingRetrievalService({
                "LOCAL_RETRIEVAL_PIPELINE": "visual",
                "LOCAL_RUNTIME_CONFIG_PATH": runtime_path,
            }, pipeline="qwen")

        self.assertEqual(service.pipeline, "qwen")
        self.assertIsInstance(service.index_service, _LocalEmbeddingIndexService)


if __name__ == "__main__":
    unittest.main()
