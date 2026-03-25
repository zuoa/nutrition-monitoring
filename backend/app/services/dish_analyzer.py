import json
import logging
import time
import requests

logger = logging.getLogger(__name__)

NUTRITION_SYSTEM_PROMPT = """你是一个专业的营养师，专门分析菜品营养成分。
请根据菜品名称和重量，估算菜品的营养成分。
只返回 JSON 格式，不要输出其他内容。"""

NUTRITION_PROMPT_TEMPLATE = """请分析菜品「{dish_name}」的营养成分。
重量：{weight}g

返回严格 JSON 格式：
{{
  "calories": 0,      // 热量 (kcal)
  "protein": 0,       // 蛋白质 (g)
  "fat": 0,           // 脂肪 (g)
  "carbohydrate": 0,  // 碳水化合物 (g)
  "sodium": 0,        // 钠 (mg)
  "fiber": 0,         // 膳食纤维 (g)
  "description": "",  // 菜品描述，用于视觉识别，50字以内，突出视觉特征
  "notes": ""         // 可选说明，如食材组成、估算依据等
}}

description 字段要求：
- 描述菜品的视觉特征，帮助图像识别模型辨认
- 包含：主要食材、颜色、形状、烹饪方式、常见配菜
- 示例："红烧肉块呈深红色，配葱花；肥瘦相间，酱汁浓稠"
- 控制在50字以内，简洁明了

注意事项：
- 所有数值为 {weight}g 重量的估算值
- 只返回 JSON，不要其他文字说明
- 如无把握，给出合理估算值并说明"""


class DishAnalyzerService:
    """Analyze dish nutrition using OpenAI-compatible API."""

    def __init__(self, config: dict):
        self.api_key = config.get("OPENAI_API_KEY", "")
        self.base_url = config.get("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
        self.model = config.get("OPENAI_MODEL", "deepseek-chat")
        self.timeout = int(config.get("OPENAI_TIMEOUT", 30))

        # Ensure base_url doesn't end with / and has chat/completions path
        self.base_url = self.base_url.rstrip("/")
        if not self.base_url.endswith("/chat/completions"):
            self.api_url = f"{self.base_url}/chat/completions"
        else:
            self.api_url = self.base_url

    def analyze_nutrition(self, dish_name: str, weight: int = 100) -> dict:
        """Analyze nutrition for a dish. Returns parsed nutrition data.

        Args:
            dish_name: Name of the dish
            weight: Weight in grams (default 100g)
        """
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY not configured")

        user_prompt = NUTRITION_PROMPT_TEMPLATE.format(dish_name=dish_name, weight=weight)

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": NUTRITION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 800,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        for attempt in range(3):
            try:
                resp = requests.post(
                    self.api_url, json=payload, headers=headers, timeout=self.timeout
                )
                resp.raise_for_status()
                raw = resp.json()
                return self._parse_response(raw)
            except requests.Timeout:
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
            except requests.RequestException as e:
                logger.warning(f"API request failed (attempt {attempt + 1}): {e}")
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)

    def _parse_response(self, raw: dict) -> dict:
        """Parse API response and extract nutrition data."""
        try:
            content = (
                raw.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )

            # Try to extract JSON from markdown code block
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            # Parse JSON
            data = json.loads(content)

            # Validate required fields
            required = ["calories", "protein", "fat", "carbohydrate", "sodium", "fiber"]
            result = {k: float(data.get(k, 0) or 0) for k in required}
            result["description"] = data.get("description", "")
            result["notes"] = data.get("notes", "")
            result["raw_response"] = raw

            return result
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse JSON response: {e}")
            return {
                "calories": 0,
                "protein": 0,
                "fat": 0,
                "carbohydrate": 0,
                "sodium": 0,
                "fiber": 0,
                "description": "",
                "notes": f"Parse error: {str(e)}",
                "raw_response": raw,
            }
        except Exception as e:
            logger.warning(f"Failed to parse response: {e}")
            return {
                "calories": 0,
                "protein": 0,
                "fat": 0,
                "carbohydrate": 0,
                "sodium": 0,
                "fiber": 0,
                "description": "",
                "notes": f"Error: {str(e)}",
                "raw_response": raw,
            }
