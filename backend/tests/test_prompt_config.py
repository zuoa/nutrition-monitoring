import unittest

from prompt_defaults import (
    NUTRITION_PROMPT_TEMPLATE,
    NUTRITION_SYSTEM_PROMPT,
    QWEN_DESCRIPTION_SYSTEM_PROMPT,
    QWEN_DESCRIPTION_USER_PROMPT,
    QWEN_RECOGNITION_SYSTEM_PROMPT,
    QWEN_RECOGNITION_USER_PROMPT_TEMPLATE,
)
from prompt_utils import render_prompt_template


class PromptDefaultsTests(unittest.TestCase):
    def test_defaults_expose_expected_placeholders(self):
        self.assertIn("{dish_name}", NUTRITION_PROMPT_TEMPLATE)
        self.assertIn("{weight}", NUTRITION_PROMPT_TEMPLATE)
        self.assertIn("{ingredients_section}", NUTRITION_PROMPT_TEMPLATE)
        self.assertIn("structured_description", NUTRITION_PROMPT_TEMPLATE)
        self.assertIn("{dish_list_with_desc}", QWEN_RECOGNITION_USER_PROMPT_TEMPLATE)
        self.assertIn("主食材", QWEN_RECOGNITION_SYSTEM_PROMPT)
        self.assertIn("易混淆菜", QWEN_RECOGNITION_USER_PROMPT_TEMPLATE)
        self.assertIn("命中依据", QWEN_RECOGNITION_SYSTEM_PROMPT)
        self.assertIn("不确定因素", QWEN_RECOGNITION_USER_PROMPT_TEMPLATE)
        self.assertIn("structured_description", QWEN_DESCRIPTION_USER_PROMPT)
        self.assertIn('"dishes"', QWEN_DESCRIPTION_USER_PROMPT)
        self.assertTrue(NUTRITION_SYSTEM_PROMPT)
        self.assertTrue(QWEN_RECOGNITION_SYSTEM_PROMPT)
        self.assertTrue(QWEN_DESCRIPTION_SYSTEM_PROMPT)
        self.assertTrue(QWEN_DESCRIPTION_USER_PROMPT)


class PromptRenderingTests(unittest.TestCase):
    def test_render_prompt_template_replaces_known_placeholders(self):
        rendered = render_prompt_template(
            "dish={dish_name}, weight={weight}, extra={ingredients_section}",
            {
                "dish_name": "红烧肉",
                "weight": 120,
                "ingredients_section": "土豆",
            },
        )

        self.assertEqual(rendered, "dish=红烧肉, weight=120, extra=土豆")

    def test_render_prompt_template_keeps_json_braces(self):
        rendered = render_prompt_template(
            '输出 JSON: {"category": "", "dish": "{dish_name}"}',
            {"dish_name": "鱼香肉丝"},
        )

        self.assertEqual(rendered, '输出 JSON: {"category": "", "dish": "鱼香肉丝"}')

    def test_render_prompt_template_keeps_unknown_placeholders(self):
        rendered = render_prompt_template(
            "known={dish_name}, unknown={other_key}",
            {"dish_name": "宫保鸡丁"},
        )

        self.assertEqual(rendered, "known=宫保鸡丁, unknown={other_key}")


if __name__ == "__main__":
    unittest.main()
