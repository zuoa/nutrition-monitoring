import importlib.util
import os
import sys
import unittest


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


if __name__ == "__main__":
    unittest.main()
