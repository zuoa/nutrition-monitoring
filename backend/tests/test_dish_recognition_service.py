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
    "dish_recognition.py",
)


def load_dish_recognition_module():
    app_module = types.ModuleType("app")
    app_module.__path__ = []
    services_module = types.ModuleType("app.services")
    services_module.__path__ = []

    inference_client_module = types.ModuleType("app.services.inference_client")

    class DefaultInferenceServiceError(RuntimeError):
        pass

    inference_client_module.InferenceServiceError = DefaultInferenceServiceError
    inference_client_module.make_detector_client = lambda config: None
    inference_client_module.make_retrieval_client = lambda config: None

    qwen_vl_module = types.ModuleType("app.services.qwen_vl")

    class DefaultQwenVLService:
        def __init__(self, config):
            self.config = dict(config)

        def recognize_dishes(self, image_path, candidate_dishes):
            return {"dishes": [{"name": "清蒸鱼", "confidence": 0.77}], "notes": "qwen"}

    qwen_vl_module.QwenVLService = DefaultQwenVLService

    recognition_modes_module = types.ModuleType("app.services.recognition_modes")
    recognition_modes_module.LOCAL_RECOGNITION_MODE = "local_embedding"

    def _normalize_mode(mode):
        raw = str(mode or "").strip()
        if raw in {"local_embedding", "yolo_embedding_local"}:
            return "local_embedding"
        return raw

    recognition_modes_module.normalize_recognition_mode = _normalize_mode

    runtime_config_module = types.ModuleType("app.services.runtime_config")
    runtime_config_module.get_effective_config = lambda config: dict(config)

    stubbed_modules = {
        "app": app_module,
        "app.services": services_module,
        "app.services.inference_client": inference_client_module,
        "app.services.qwen_vl": qwen_vl_module,
        "app.services.recognition_modes": recognition_modes_module,
        "app.services.runtime_config": runtime_config_module,
    }

    with mock.patch.dict(sys.modules, stubbed_modules, clear=False):
        spec = importlib.util.spec_from_file_location("test_dish_recognition", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module


class DishRecognitionServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_dish_recognition_module()

    def test_local_embedding_mode_uses_remote_inference_services(self):
        detector_calls = []
        retrieval_calls = []

        class FakeDetectorClient:
            def post_file(self, path, *, image_path, data=None):
                detector_calls.append({
                    "path": path,
                    "image_path": image_path,
                    "data": data,
                })
                return {
                    "backend": "yolo",
                    "regions": [{
                        "index": 2,
                        "bbox": {"x1": 10, "y1": 20, "x2": 110, "y2": 160},
                        "score": 0.91,
                        "source": "yolo",
                    }],
                }

        class FakeRetrievalClient:
            def post_file(self, path, *, image_path, data=None):
                retrieval_calls.append({
                    "path": path,
                    "image_path": image_path,
                    "data": data,
                })
                return {
                    "recognized_dishes": [{"name": "红烧肉", "confidence": 0.95}],
                    "region_results": [{"index": 2, "bbox": {"x1": 10, "y1": 20, "x2": 110, "y2": 160}}],
                    "raw_response": {"mode": "local_embedding"},
                    "notes": "yolo local embedding 模式，区域数 1",
                    "model_version": "qwen3_vl_embedding+reranker",
                }

        with mock.patch.object(self.module, "make_detector_client", return_value=FakeDetectorClient()), \
             mock.patch.object(self.module, "make_retrieval_client", return_value=FakeRetrievalClient()):
            service = self.module.DishRecognitionService({
                "DISH_RECOGNITION_MODE": "local_embedding",
                "YOLO_MAX_REGIONS": 3,
            })
            result = service.recognize_dishes(
                "/tmp/meal.jpg",
                [{"id": 1, "name": "红烧肉", "description": ""}],
            )

        self.assertEqual(detector_calls, [{
            "path": "/v1/detect",
            "image_path": "/tmp/meal.jpg",
            "data": {"max_regions": 3},
        }])
        self.assertEqual(retrieval_calls, [{
            "path": "/v1/full",
            "image_path": "/tmp/meal.jpg",
            "data": {
                "candidate_dishes": [{"id": 1, "name": "红烧肉", "description": ""}],
                "regions": [{"x1": 10, "y1": 20, "x2": 110, "y2": 160}],
            },
        }])
        self.assertEqual(result["dishes"], [{"name": "红烧肉", "confidence": 0.95}])
        self.assertEqual(result["detector_backend"], "yolo")
        self.assertEqual(result["model_version"], "qwen3_vl_embedding+reranker")

    def test_local_embedding_mode_falls_back_to_full_image_when_detector_fails(self):
        retrieval_calls = []
        inference_error = self.module.InferenceServiceError

        class FailingDetectorClient:
            def post_file(self, path, *, image_path, data=None):
                raise inference_error("detector unavailable")

        class FakeRetrievalClient:
            def post_file(self, path, *, image_path, data=None):
                retrieval_calls.append({
                    "path": path,
                    "image_path": image_path,
                    "data": data,
                })
                return {
                    "recognized_dishes": [{"name": "番茄炒蛋", "confidence": 0.82}],
                    "region_results": [{"index": 1, "bbox": None}],
                    "raw_response": {"mode": "local_embedding"},
                    "notes": "full_image local embedding 模式，区域数 1",
                    "model_version": "qwen3_vl_embedding+reranker",
                }

        with mock.patch.object(self.module, "make_detector_client", return_value=FailingDetectorClient()), \
             mock.patch.object(self.module, "make_retrieval_client", return_value=FakeRetrievalClient()):
            service = self.module.DishRecognitionService({
                "DISH_RECOGNITION_MODE": "local_embedding",
            })
            result = service.recognize_dishes(
                "/tmp/meal.jpg",
                [{"id": 9, "name": "番茄炒蛋", "description": ""}],
            )

        self.assertEqual(retrieval_calls, [{
            "path": "/v1/full",
            "image_path": "/tmp/meal.jpg",
            "data": {
                "candidate_dishes": [{"id": 9, "name": "番茄炒蛋", "description": ""}],
            },
        }])
        self.assertEqual(result["dishes"], [{"name": "番茄炒蛋", "confidence": 0.82}])
        self.assertEqual(result["detector_backend"], "full_image")

    def test_non_local_mode_still_uses_qwen_vl(self):
        service = self.module.DishRecognitionService({
            "DISH_RECOGNITION_MODE": "vl",
            "QWEN_MODEL": "qwen-vl-max",
        })

        result = service.recognize_dishes("/tmp/meal.jpg", [{"name": "清蒸鱼"}])

        self.assertEqual(result["dishes"], [{"name": "清蒸鱼", "confidence": 0.77}])
        self.assertEqual(result["model_version"], "qwen-vl-max")


if __name__ == "__main__":
    unittest.main()
