import json
import logging
import time
import requests
try:
    from app.services.structured_description import normalize_structured_description
except ModuleNotFoundError:
    from structured_description import normalize_structured_description
from prompt_defaults import (
    NUTRITION_PROMPT_TEMPLATE as DEFAULT_NUTRITION_PROMPT_TEMPLATE,
    NUTRITION_SYSTEM_PROMPT as DEFAULT_NUTRITION_SYSTEM_PROMPT,
)
from prompt_utils import render_prompt_template

logger = logging.getLogger(__name__)


class DishAnalyzerService:
    """Analyze dish nutrition using OpenAI-compatible API."""

    def __init__(self, config: dict):
        self.api_key = config.get("OPENAI_API_KEY", "")
        self.base_url = config.get("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
        self.model = config.get("OPENAI_MODEL", "deepseek-chat")
        self.timeout = int(config.get("OPENAI_TIMEOUT", 30))
        self.system_prompt = config.get("NUTRITION_SYSTEM_PROMPT", DEFAULT_NUTRITION_SYSTEM_PROMPT)
        self.prompt_template = config.get("NUTRITION_PROMPT_TEMPLATE", DEFAULT_NUTRITION_PROMPT_TEMPLATE)

        # Ensure base_url doesn't end with / and has chat/completions path
        self.base_url = self.base_url.rstrip("/")
        if not self.base_url.endswith("/chat/completions"):
            self.api_url = f"{self.base_url}/chat/completions"
        else:
            self.api_url = self.base_url

    def analyze_nutrition(self, dish_name: str, weight: int = 100, ingredients: str = "") -> dict:
        """Analyze nutrition for a dish. Returns parsed nutrition data.

        Args:
            dish_name: Name of the dish
            weight: Weight in grams (default 100g)
            ingredients: Optional ingredients description for more accurate analysis
        """
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY not configured")

        ingredients_section = f"\n配菜描述：{ingredients}\n" if ingredients else ""
        user_prompt = render_prompt_template(
            self.prompt_template,
            {
                "dish_name": dish_name,
                "weight": weight,
                "ingredients_section": ingredients_section,
            },
        )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
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
            result["structured_description"] = normalize_structured_description(
                data.get("structured_description")
            )
            result["category"] = data.get("category", "")
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
                "structured_description": normalize_structured_description(None),
                "category": "",
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
                "structured_description": normalize_structured_description(None),
                "category": "",
                "notes": f"Error: {str(e)}",
                "raw_response": raw,
            }
