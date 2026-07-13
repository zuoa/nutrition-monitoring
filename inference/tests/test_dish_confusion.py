import os
import sys
import types
import unittest

import numpy as np


if "pythonjsonlogger" not in sys.modules:
    pythonjsonlogger = types.ModuleType("pythonjsonlogger")
    jsonlogger = types.ModuleType("jsonlogger")
    jsonlogger.JsonFormatter = object
    pythonjsonlogger.jsonlogger = jsonlogger
    sys.modules["pythonjsonlogger"] = pythonjsonlogger


INFERENCE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if INFERENCE_DIR not in sys.path:
    sys.path.insert(0, INFERENCE_DIR)

from app.services.dish_confusion import analyze_dish_confusion  # noqa: E402


class DishConfusionAnalysisTests(unittest.TestCase):
    def test_reports_cross_dish_risks_and_ignores_same_dish_similarity(self):
        matrix = np.asarray([
            [1.0, 0.0, 0.0],
            [0.99, 0.01, 0.0],
            [0.96, 0.28, 0.0],
            [0.78, 0.62, 0.0],
            [0.0, 0.0, 1.0],
        ], dtype=np.float32)
        metadata = [
            {"image_id": 1, "dish_id": 1, "dish_name": "红烧肉"},
            {"image_id": 2, "dish_id": 1, "dish_name": "红烧肉"},
            {"image_id": 3, "dish_id": 2, "dish_name": "糖醋排骨"},
            {"image_id": 4, "dish_id": 3, "dish_name": "宫保鸡丁"},
            {"image_id": 5, "dish_id": 4, "dish_name": "米饭"},
        ]

        report = analyze_dish_confusion(
            matrix,
            metadata,
            high_threshold=0.9,
            medium_threshold=0.7,
        )

        self.assertEqual(report["summary"]["indexed_dish_count"], 4)
        self.assertEqual(report["summary"]["analyzed_pair_count"], 6)
        self.assertEqual(report["summary"]["high_risk_pair_count"], 2)
        self.assertEqual(report["summary"]["medium_risk_pair_count"], 1)
        self.assertEqual(report["pairs"][0]["risk_level"], "high")
        self.assertEqual(report["pairs"][0]["left"]["dish_id"], 1)
        self.assertEqual(report["pairs"][0]["right"]["dish_id"], 2)
        self.assertEqual(report["pairs"][0]["left"]["sample_image_id"], 2)
        self.assertEqual(report["pairs"][0]["right"]["sample_image_id"], 3)

    def test_skips_invalid_vectors_and_limits_returned_pairs(self):
        matrix = np.asarray([
            [1.0, 0.0],
            [0.9, 0.1],
            [0.8, 0.2],
            [0.0, 0.0],
        ], dtype=np.float32)
        metadata = [
            {"image_id": 1, "dish_id": 1, "dish_name": "A"},
            {"image_id": 2, "dish_id": 2, "dish_name": "B"},
            {"image_id": 3, "dish_id": 3, "dish_name": "C"},
            {"image_id": 4, "dish_id": 4, "dish_name": "D"},
        ]

        report = analyze_dish_confusion(
            matrix,
            metadata,
            high_threshold=0.95,
            medium_threshold=0.7,
            max_pairs=1,
        )

        self.assertEqual(report["summary"]["invalid_sample_count"], 1)
        self.assertEqual(report["summary"]["returned_pair_count"], 1)
        self.assertGreater(report["summary"]["truncated_pair_count"], 0)
        self.assertEqual(len(report["pairs"]), 1)

    def test_rejects_mismatched_metadata(self):
        with self.assertRaisesRegex(ValueError, "数量不一致"):
            analyze_dish_confusion(np.asarray([[1.0, 0.0]], dtype=np.float32), [])


if __name__ == "__main__":
    unittest.main()
