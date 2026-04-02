import importlib.util
import os
import sys
import unittest
from unittest import mock


BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
SERVICES_DIR = os.path.join(BACKEND_DIR, "app", "services")
if SERVICES_DIR not in sys.path:
    sys.path.insert(0, SERVICES_DIR)


def _load_module(name: str, relative_path: str):
    module_path = os.path.join(BACKEND_DIR, relative_path)
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


STRUCTURED_DESCRIPTION = _load_module(
    "structured_description",
    os.path.join("app", "services", "structured_description.py"),
)
DISH_ANALYZER = _load_module(
    "dish_analyzer",
    os.path.join("app", "services", "dish_analyzer.py"),
)
QWEN_VL = _load_module(
    "qwen_vl",
    os.path.join("app", "services", "qwen_vl.py"),
)


class StructuredDescriptionTests(unittest.TestCase):
    def test_normalize_accepts_aliases(self):
        normalized = STRUCTURED_DESCRIPTION.normalize_structured_description(
            {
                "main_ingredients": "排骨、土豆",
                "colors": "红褐色",
                "cuts": "块状",
                "texture": "油亮软烂",
                "sauce": "浓汁",
                "garnishes": "青椒",
                "confusable_with": "土豆烧鸡",
            }
        )

        self.assertEqual(normalized["mainIngredients"], "排骨、土豆")
        self.assertEqual(normalized["confusableWith"], "土豆烧鸡")

    def test_compose_appends_structured_section(self):
        text = STRUCTURED_DESCRIPTION.compose_structured_description(
            "红烧排骨呈红褐色，带浓汁。",
            {
                "mainIngredients": "排骨、土豆",
                "colors": "红褐色",
                "cuts": "块状",
            },
        )

        self.assertIn("红烧排骨呈红褐色，带浓汁。", text)
        self.assertIn(STRUCTURED_DESCRIPTION.STRUCTURED_DESCRIPTION_SECTION, text)
        self.assertIn("主食材：排骨、土豆", text)
        self.assertIn("颜色：红褐色", text)

    def test_parse_composed_description_extracts_summary_and_details(self):
        parsed = STRUCTURED_DESCRIPTION.parse_composed_description(
            "红烧排骨呈红褐色，带浓汁。\n\n【识别特征】\n主食材：排骨、土豆\n颜色：红褐色\n易混淆菜：土豆烧鸡"
        )

        self.assertEqual(parsed["summary"], "红烧排骨呈红褐色，带浓汁。")
        self.assertEqual(parsed["structured_description"]["mainIngredients"], "排骨、土豆")
        self.assertEqual(parsed["structured_description"]["confusableWith"], "土豆烧鸡")


class DishAnalyzerParseTests(unittest.TestCase):
    def test_parse_response_extracts_structured_description(self):
        service = DISH_ANALYZER.DishAnalyzerService({"OPENAI_API_KEY": "test-key"})
        raw = {
            "choices": [
                {
                    "message": {
                        "content": """
{
  "category": "荤菜",
  "calories": 320,
  "protein": 18,
  "fat": 22,
  "carbohydrate": 12,
  "sodium": 640,
  "fiber": 2,
  "description": "红烧排骨呈红褐色，排骨块明显，带浓汁。",
  "structured_description": {
    "mainIngredients": "排骨、土豆",
    "colors": "红褐色",
    "cuts": "块状",
    "texture": "表面油亮，肉质软烂",
    "sauce": "浓汁包裹",
    "garnishes": "青椒、葱花",
    "confusableWith": "土豆烧鸡"
  },
  "notes": "按常见食堂做法估算"
}
""".strip()
                    }
                }
            ]
        }

        result = service._parse_response(raw)

        self.assertEqual(result["category"], "荤菜")
        self.assertEqual(result["calories"], 320.0)
        self.assertEqual(result["structured_description"]["mainIngredients"], "排骨、土豆")
        self.assertEqual(result["structured_description"]["confusableWith"], "土豆烧鸡")


class QwenDescriptionParseTests(unittest.TestCase):
    def test_parse_response_extracts_recognition_position_and_bbox(self):
        service = QWEN_VL.QwenVLService(
            {
                "QWEN_API_KEY": "test-key",
                "QWEN_API_URL": "https://example.com/chat/completions",
            }
        )
        raw = {
            "choices": [
                {
                    "message": {
                        "content": """
{
  "dishes": [
    {
      "name": "红烧排骨",
      "confidence": 0.93,
      "position": "左上",
      "bbox": {"x1": 6, "y1": 8, "x2": 43, "y2": 41}
    }
  ],
  "notes": "命中依据：红褐色块状"
}
""".strip()
                    }
                }
            ]
        }

        result = service._parse_response(raw)

        self.assertEqual(result["notes"], "命中依据：红褐色块状")
        self.assertEqual(result["dishes"][0]["position"], "左上")
        self.assertEqual(result["dishes"][0]["bbox"]["x1"], 6.0)
        self.assertEqual(result["dishes"][0]["bbox"]["y2"], 41.0)

    def test_normalize_regions_derives_position_from_bbox(self):
        service = QWEN_VL.QwenVLService(
            {
                "QWEN_API_KEY": "test-key",
                "QWEN_API_URL": "https://example.com/chat/completions",
            }
        )

        regions = service._normalize_regions(
            [
                {
                    "index": 1,
                    "position": "下方",
                    "visual_hint": "红褐色块状",
                    "bbox": {"x1": 8, "y1": 10, "x2": 42, "y2": 40},
                }
            ]
        )

        self.assertEqual(regions[0]["position"], "左上")

    def test_normalize_recognition_bbox_recovers_reversed_coordinates(self):
        service = QWEN_VL.QwenVLService(
            {
                "QWEN_API_KEY": "test-key",
                "QWEN_API_URL": "https://example.com/chat/completions",
            }
        )

        bbox = service._normalize_recognition_bbox({"x1": 46, "y1": 42, "x2": 8, "y2": 10})

        self.assertEqual(
            bbox,
            {"x1": 8.0, "y1": 10.0, "x2": 46.0, "y2": 42.0},
        )

    def test_region_recognition_prompt_includes_note_schema(self):
        service = QWEN_VL.QwenVLService(
            {
                "QWEN_API_KEY": "test-key",
                "QWEN_API_URL": "https://example.com/chat/completions",
            }
        )

        prompt = service._build_region_recognition_prompt(
            "- 红烧排骨\n  视觉摘要：红褐色块状，带浓汁",
            {"index": 1, "position": "左上", "visual_hint": "红褐色块状，边缘有青椒"},
            3,
        )

        self.assertIn("命中依据：", prompt)
        self.assertIn("混淆项：", prompt)
        self.assertIn("不确定因素：", prompt)

    def test_attach_recognition_notes_fills_missing_item_notes(self):
        service = QWEN_VL.QwenVLService(
            {
                "QWEN_API_KEY": "test-key",
                "QWEN_API_URL": "https://example.com/chat/completions",
            }
        )

        dishes = service._attach_recognition_notes(
            [{"name": "红烧排骨", "confidence": 0.91, "notes": ""}],
            "命中依据：红褐色块状；不确定因素：边缘轻微遮挡",
        )

        self.assertEqual(
            dishes[0]["notes"],
            "命中依据：红褐色块状；不确定因素：边缘轻微遮挡",
        )

    def test_dedupe_dishes_drops_overlapping_duplicate_regions(self):
        service = QWEN_VL.QwenVLService(
            {
                "QWEN_API_KEY": "test-key",
                "QWEN_API_URL": "https://example.com/chat/completions",
            }
        )

        dishes = service._dedupe_dishes(
            [
                {
                    "name": "红烧排骨",
                    "confidence": 0.91,
                    "position": "左上",
                    "bbox": {"x1": 5, "y1": 8, "x2": 45, "y2": 42},
                    "notes": "",
                },
                {
                    "name": "土豆烧鸡",
                    "confidence": 0.72,
                    "position": "左上",
                    "bbox": {"x1": 7, "y1": 10, "x2": 44, "y2": 41},
                    "notes": "",
                },
            ]
        )

        self.assertEqual(len(dishes), 1)
        self.assertEqual(dishes[0]["name"], "红烧排骨")

    def test_format_candidate_dishes_uses_structured_features(self):
        service = QWEN_VL.QwenVLService(
            {
                "QWEN_API_KEY": "test-key",
                "QWEN_API_URL": "https://example.com/chat/completions",
            }
        )

        formatted = service._format_candidate_dishes(
            [
                {
                    "name": "红烧排骨",
                    "description": "红烧排骨呈红褐色，带浓汁。\n\n【识别特征】\n主食材：排骨、土豆\n颜色：红褐色\n易混淆菜：土豆烧鸡",
                }
            ]
        )

        self.assertIn("- 红烧排骨", formatted)
        self.assertIn("视觉摘要：红烧排骨呈红褐色，带浓汁。", formatted)
        self.assertIn("主食材=排骨、土豆", formatted)
        self.assertIn("易混淆菜=土豆烧鸡", formatted)

    def test_parse_description_response_extracts_structured_fields(self):
        service = QWEN_VL.QwenVLService(
            {
                "QWEN_API_KEY": "test-key",
                "QWEN_API_URL": "https://example.com/chat/completions",
            }
        )
        raw = {
            "choices": [
                {
                    "message": {
                        "content": """
{
  "description": "红烧排骨呈红褐色，块状明显，表面带浓汁。",
  "structured_description": {
    "mainIngredients": "排骨、土豆",
    "colors": "红褐色",
    "cuts": "块状",
    "texture": "表面油亮，肉质软烂",
    "sauce": "浓汁包裹",
    "garnishes": "青椒、葱花",
    "confusableWith": "土豆烧鸡"
  },
  "notes": "右侧有少量反光"
}
""".strip()
                    }
                }
            ]
        }

        result = service._parse_description_response(raw)

        self.assertEqual(result["description"], "红烧排骨呈红褐色，块状明显，表面带浓汁。")
        self.assertEqual(result["structured_description"]["mainIngredients"], "排骨、土豆")
        self.assertEqual(result["structured_description"]["confusableWith"], "土豆烧鸡")
        self.assertEqual(result["notes"], "右侧有少量反光")
        self.assertEqual(len(result["descriptions"]), 1)

    def test_parse_description_response_keeps_dishes_separated(self):
        service = QWEN_VL.QwenVLService(
            {
                "QWEN_API_KEY": "test-key",
                "QWEN_API_URL": "https://example.com/chat/completions",
            }
        )
        raw = {
            "choices": [
                {
                    "message": {
                        "content": """
{
  "dishes": [
    {
      "index": 1,
      "position": "左侧",
      "description": "红烧排骨呈红褐色，块状明显，表面带浓汁。",
      "structured_description": {
        "mainIngredients": "排骨、土豆",
        "colors": "红褐色",
        "cuts": "块状",
        "texture": "表面油亮，肉质软烂",
        "sauce": "浓汁包裹",
        "garnishes": "青椒、葱花",
        "confusableWith": "土豆烧鸡"
      },
      "notes": "边缘有少量遮挡"
    },
    {
      "index": 2,
      "position": "右侧",
      "description": "清炒白菜颜色浅绿，叶片明显，带少量清汁。",
      "structured_description": {
        "mainIngredients": "白菜",
        "colors": "浅绿、乳白",
        "cuts": "叶片状",
        "texture": "叶片柔软有光泽",
        "sauce": "少量清汁",
        "garnishes": "蒜末",
        "confusableWith": "清炒生菜"
      },
      "notes": ""
    }
  ],
  "notes": "餐盘边缘有反光"
}
""".strip()
                    }
                }
            ]
        }

        result = service._parse_description_response(raw)

        self.assertEqual(result["description"], "红烧排骨呈红褐色，块状明显，表面带浓汁。")
        self.assertEqual(result["notes"], "餐盘边缘有反光")
        self.assertEqual(len(result["descriptions"]), 2)
        self.assertEqual(result["descriptions"][0]["position"], "左侧")
        self.assertEqual(result["descriptions"][1]["position"], "右侧")
        self.assertEqual(
            result["descriptions"][1]["structured_description"]["confusableWith"],
            "清炒生菜",
        )

    def test_recognize_dishes_uses_region_flow_when_regions_available(self):
        service = QWEN_VL.QwenVLService(
            {
                "QWEN_API_KEY": "test-key",
                "QWEN_API_URL": "https://example.com/chat/completions",
            }
        )

        with mock.patch.object(service, "_build_image_url", return_value="data:image/jpeg;base64,full"), \
             mock.patch.object(
                 service,
                 "_detect_dish_regions",
                 return_value=(
                     {"source": "region-detector"},
                     {
                         "dish_count": 1,
                         "regions": [
                             {
                                 "index": 1,
                                 "position": "左上",
                                 "visual_hint": "红褐色块状",
                                 "bbox": {"x1": 5, "y1": 10, "x2": 45, "y2": 42},
                             }
                         ],
                         "notes": "边缘有轻微遮挡",
                     },
                 ),
             ) as detect_regions, \
             mock.patch.object(
                 service,
                 "_build_region_crops",
                 return_value=[
                     {
                         "index": 1,
                         "position": "左上",
                         "visual_hint": "红褐色块状",
                         "bbox": {"x1": 5, "y1": 10, "x2": 45, "y2": 42},
                         "bbox_pixels": {"x1": 64, "y1": 48, "x2": 320, "y2": 202},
                         "image_url": "data:image/jpeg;base64,crop1",
                     }
                 ],
             ) as build_region_crops, \
             mock.patch.object(
                 service,
                 "_request_model",
                 return_value={
                     "choices": [
                         {
                             "message": {
                                 "content": """
{
  "dishes": [
    {"name": "红烧排骨", "confidence": 0.93}
  ],
  "notes": "命中依据：红褐色块状"
}
""".strip()
                             }
                         }
                     ]
                 },
             ) as request_model, \
             mock.patch.object(
                 service,
                 "_recognize_single_stage",
                 return_value={
                     "dishes": [{"name": "不应该走到这里", "confidence": 0.1}],
                     "notes": "",
                     "raw_response": {"source": "full-image"},
                 },
             ) as recognize_single_stage, \
             mock.patch.object(service, "_canonicalize_dishes", wraps=service._canonicalize_dishes) as canonicalize_dishes:
            result = service.recognize_dishes(
                "/tmp/meal.jpg",
                [{"name": "红烧排骨", "description": ""}],
            )

        detect_regions.assert_called_once()
        build_region_crops.assert_called_once()
        request_model.assert_called_once()
        recognize_single_stage.assert_not_called()
        canonicalize_dishes.assert_called()
        self.assertEqual(result["dishes"], [{
            "name": "红烧排骨",
            "confidence": 0.93,
            "position": "左上",
            "bbox": {"x1": 64.0, "y1": 48.0, "x2": 320.0, "y2": 202.0},
            "bbox_source": "pixels",
            "notes": "命中依据：红褐色块状",
        }])
        self.assertEqual(result["raw_response"]["mode"], "region_two_stage")

    def test_recognize_dishes_falls_back_to_full_image_when_region_flow_empty(self):
        service = QWEN_VL.QwenVLService(
            {
                "QWEN_API_KEY": "test-key",
                "QWEN_API_URL": "https://example.com/chat/completions",
            }
        )

        with mock.patch.object(service, "_build_image_url", return_value="data:image/jpeg;base64,full"), \
             mock.patch.object(
                 service,
                 "_detect_dish_regions",
                 return_value=(
                     {"source": "region-detector"},
                     {"dish_count": 0, "regions": [], "notes": "整图里菜区边界不够稳定"},
                 ),
             ) as detect_regions, \
             mock.patch.object(
                 service,
                 "_recognize_single_stage",
                 return_value={
                     "dishes": [
                         {
                             "name": "红烧排骨",
                             "confidence": 0.88,
                             "position": "右下",
                             "bbox": {"x1": 70, "y1": 60, "x2": 95, "y2": 92},
                         }
                     ],
                     "notes": "命中依据：整图上下文完整",
                     "raw_response": {"source": "full-image"},
                 },
             ) as recognize_single_stage:
            result = service.recognize_dishes(
                "/tmp/meal.jpg",
                [{"name": "红烧排骨", "description": ""}],
            )

        detect_regions.assert_called_once()
        recognize_single_stage.assert_called_once()
        self.assertEqual(result["dishes"], [{
            "name": "红烧排骨",
            "confidence": 0.88,
            "position": "",
            "bbox": None,
            "bbox_source": "",
            "notes": "命中依据：整图上下文完整",
        }])
        self.assertIn("整图里菜区边界不够稳定", result["notes"])
        self.assertEqual(result["raw_response"]["mode"], "full_image_fallback")

    def test_recognize_dishes_propagates_full_image_failure(self):
        service = QWEN_VL.QwenVLService(
            {
                "QWEN_API_KEY": "test-key",
                "QWEN_API_URL": "https://example.com/chat/completions",
            }
        )

        with mock.patch.object(service, "_build_image_url", return_value="data:image/jpeg;base64,full"), \
             mock.patch.object(
                 service,
                 "_detect_dish_regions",
                 return_value=(
                     {"source": "region-detector"},
                     {"dish_count": 0, "regions": [], "notes": ""},
                 ),
             ) as detect_regions, \
             mock.patch.object(
                 service,
                 "_recognize_single_stage",
                 side_effect=RuntimeError("qwen timeout"),
             ):
            with self.assertRaisesRegex(RuntimeError, "qwen timeout"):
                service.recognize_dishes(
                    "/tmp/meal.jpg",
                    [{"name": "红烧排骨", "description": ""}],
                )

        detect_regions.assert_called_once()


if __name__ == "__main__":
    unittest.main()
