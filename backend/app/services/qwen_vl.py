import base64
import logging
import time
import json
import re
import requests

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一个学校食堂菜品识别助手。请仔细观察图片中餐盘/餐具中的菜肴，
从给定的候选菜品列表中识别出当前餐盘中包含的所有菜品。

菜品描述信息已提供，请结合视觉特征和描述进行识别：
- 颜色：菜品呈现的主要颜色（如深红色、金黄色、翠绿色）
- 形状：食材的形状和切割方式（如块状、丝状、片状）
- 质地：表面特征（如酱汁浓稠、油炸酥脆、清汤透亮）
- 配菜：常见的搭配食材（如葱花、香菜、辣椒、芝麻）

只返回 JSON 格式，不要输出其他内容。"""

USER_PROMPT_TEMPLATE = """今日供应菜品列表：
{dish_list_with_desc}

请识别图中餐盘的菜品，返回格式：
{{
  "dishes": [
    {{"name": "菜品名", "confidence": 0.95}}
  ],
  "notes": "可选备注"
}}"""

LOW_CONFIDENCE_THRESHOLD = 0.6


class QwenVLService:
    def __init__(self, config: dict):
        self.api_key = config.get("QWEN_API_KEY", "")
        self.api_url = config.get("QWEN_API_URL", "")
        self.model = config.get("QWEN_MODEL", "qwen-vl-max")
        self.timeout = int(config.get("QWEN_TIMEOUT", 30))
        self.max_qps = int(config.get("QWEN_MAX_QPS", 10))
        self._last_request_times: list[float] = []

    def recognize_dishes(self, image_path: str, candidate_dishes: list[dict]) -> dict:
        """Recognize dishes in image. Returns {dishes: [{name, confidence}], notes, raw_response}

        Args:
            candidate_dishes: List of dict with 'name' and optional 'description' keys
        """
        self._rate_limit()

        # Build dish list with descriptions for better recognition
        if candidate_dishes:
            dish_lines = []
            for d in candidate_dishes:
                name = d.get("name", "")
                desc = d.get("description", "")
                if desc:
                    dish_lines.append(f"- {name}（{desc}）")
                else:
                    dish_lines.append(f"- {name}")
            dish_list_with_desc = "\n".join(dish_lines)
        else:
            dish_list_with_desc = "所有菜品"

        user_prompt = USER_PROMPT_TEMPLATE.format(dish_list_with_desc=dish_list_with_desc)

        # Read and encode image
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        # Detect image type
        ext = image_path.rsplit(".", 1)[-1].lower()
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
        image_url = f"data:{mime};base64,{image_data}"
        payload = self._build_payload(user_prompt, image_url)

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
                resp = getattr(e, "response", None)
                if resp is not None:
                    logger.error(
                        "Qwen request failed: status=%s url=%s body=%s",
                        resp.status_code,
                        self.api_url,
                        resp.text[:1000],
                    )
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)

    def _parse_response(self, raw: dict) -> dict:
        try:
            content = self._extract_content(raw)

            # Extract JSON from content
            json_match = re.search(r"\{.*\}", content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
            else:
                data = {"dishes": [], "notes": "Failed to parse response"}

            return {
                "dishes": data.get("dishes", []),
                "notes": data.get("notes", ""),
                "raw_response": raw,
            }
        except Exception as e:
            logger.warning(f"Failed to parse Qwen response: {e}")
            return {"dishes": [], "notes": str(e), "raw_response": raw}

    def _rate_limit(self):
        now = time.time()
        window = 1.0
        self._last_request_times = [t for t in self._last_request_times if now - t < window]

        if len(self._last_request_times) >= self.max_qps:
            sleep_time = window - (now - self._last_request_times[0]) + 0.01
            if sleep_time > 0:
                time.sleep(sleep_time)

        self._last_request_times.append(time.time())

    def _build_payload(self, user_prompt: str, image_url: str) -> dict:
        if self._uses_openai_chat_completions():
            return {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_prompt},
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ],
                    },
                ],
            }

        return {
            "model": self.model,
            "input": {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"image": image_url},
                            {"text": user_prompt},
                        ],
                    },
                ]
            },
            "parameters": {"result_format": "message"},
        }

    def _uses_openai_chat_completions(self) -> bool:
        normalized = self.api_url.lower()
        return "/chat/completions" in normalized

    def _extract_content(self, raw: dict) -> str:
        choices = raw.get("choices")
        if not choices:
            choices = raw.get("output", {}).get("choices", [])

        content = (
            (choices or [{}])[0]
            .get("message", {})
            .get("content", "")
        )

        if isinstance(content, list):
            parts = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if "text" in item:
                    parts.append(item.get("text", ""))
                elif item.get("type") == "text":
                    parts.append(item.get("text", ""))
            return " ".join(part for part in parts if part)

        return content if isinstance(content, str) else str(content)
