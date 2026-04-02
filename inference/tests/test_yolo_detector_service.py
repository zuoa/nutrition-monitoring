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
    "yolo_detector.py",
)


def load_yolo_detector_module():
    app_module = types.ModuleType("app")
    app_module.__path__ = []
    services_module = types.ModuleType("app.services")
    services_module.__path__ = []

    runtime_config_module = types.ModuleType("app.services.runtime_config")
    runtime_config_module.get_effective_config = lambda config: dict(config)

    stubbed_modules = {
        "app": app_module,
        "app.services": services_module,
        "app.services.runtime_config": runtime_config_module,
    }

    with mock.patch.dict(sys.modules, stubbed_modules, clear=False):
        spec = importlib.util.spec_from_file_location("test_yolo_detector", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module


class YoloDetectorServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_yolo_detector_module()

    def test_detect_regions_parses_yolo_boxes(self):
        predict_calls = []

        class FakeScalar:
            def __init__(self, value):
                self.value = value

            def item(self):
                return self.value

        class FakeBox:
            def __init__(self, values):
                self.values = values

            def tolist(self):
                return list(self.values)

        class FakeTensor:
            def __init__(self, values, *, box_mode: bool = False):
                self.values = values
                self.box_mode = box_mode

            def __getitem__(self, index):
                if self.box_mode:
                    return FakeBox(self.values[index])
                return FakeScalar(self.values[index])

            def __len__(self):
                return len(self.values)

        class FakeBoxes:
            xyxy = FakeTensor([
                [10, 20, 100, 150],
                [0, 0, 10, 10],
            ], box_mode=True)
            conf = FakeTensor([0.8, 0.9])
            cls = FakeTensor([0, 0])

        class FakeResult:
            boxes = FakeBoxes()

        class FakeModel:
            names = {0: "food_region"}

            def predict(self, **kwargs):
                predict_calls.append(kwargs)
                return [FakeResult()]

        service = self.module.YoloRegionDetectorService({
            "YOLO_MODEL_PATH": "/tmp/models/yolo.pt",
            "YOLO_DEVICE": "cpu",
        })
        service._get_model = lambda: FakeModel()

        with mock.patch.object(self.module.os.path, "exists", return_value=True):
            result = service.detect_regions("/tmp/demo.jpg")

        self.assertEqual(result["backend"], "yolo")
        self.assertEqual(len(result["proposals"]), 1)
        self.assertEqual(result["proposals"][0]["bbox"], {"x1": 10, "y1": 20, "x2": 100, "y2": 150})
        self.assertEqual(result["proposals"][0]["class_name"], "food_region")
        self.assertEqual(predict_calls, [{
            "source": "/tmp/demo.jpg",
            "conf": 0.75,
            "iou": 0.45,
            "max_det": 6,
            "classes": [0],
            "device": "cpu",
            "verbose": False,
        }])


if __name__ == "__main__":
    unittest.main()
