import base64
import logging
import time
import json
import re
import requests
from prompt_defaults import (
    QWEN_DESCRIPTION_SYSTEM_PROMPT as DEFAULT_QWEN_DESCRIPTION_SYSTEM_PROMPT,
    QWEN_DESCRIPTION_USER_PROMPT as DEFAULT_QWEN_DESCRIPTION_USER_PROMPT,
    QWEN_RECOGNITION_SYSTEM_PROMPT as DEFAULT_QWEN_RECOGNITION_SYSTEM_PROMPT,
    QWEN_RECOGNITION_USER_PROMPT_TEMPLATE as DEFAULT_QWEN_RECOGNITION_USER_PROMPT_TEMPLATE,
)

logger = logging.getLogger(__name__)

LOW_CONFIDENCE_THRESHOLD = 0.6


class QwenVLService:
    def __init__(self, config: dict):
        self.api_key = config.get("QWEN_API_KEY", "")
        self.api_url = config.get("QWEN_API_URL", "")
        self.model = config.get("QWEN_MODEL", "qwen-vl-max")
        self.timeout = int(config.get("QWEN_TIMEOUT", 30))
        self.max_qps = int(config.get("QWEN_MAX_QPS", 10))
        self.recognition_system_prompt = config.get(
            "QWEN_RECOGNITION_SYSTEM_PROMPT",
            DEFAULT_QWEN_RECOGNITION_SYSTEM_PROMPT,
        )
        self.recognition_user_prompt_template = config.get(
            "QWEN_RECOGNITION_USER_PROMPT_TEMPLATE",
            DEFAULT_QWEN_RECOGNITION_USER_PROMPT_TEMPLATE,
        )
        self.description_system_prompt = config.get(
            "QWEN_DESCRIPTION_SYSTEM_PROMPT",
            DEFAULT_QWEN_DESCRIPTION_SYSTEM_PROMPT,
        )
        self.description_user_prompt = config.get(
            "QWEN_DESCRIPTION_USER_PROMPT",
            DEFAULT_QWEN_DESCRIPTION_USER_PROMPT,
        )
        self._last_request_times: list[float] = []

    def recognize_dishes(self, image_path: str, candidate_dishes: list[dict]) -> dict:
        """Recognize dishes in image. Returns {dishes: [{name, confidence}], notes, raw_response}

        Args:
            candidate_dishes: List of dict with 'name' and optional 'description' keys
        """
        image_url = self._build_image_url(image_path)
        dish_list_with_desc = self._format_candidate_dishes(candidate_dishes)
        user_prompt = self.recognition_user_prompt_template.format(
            dish_list_with_desc=dish_list_with_desc,
        )
        raw = self._request_model(
            self.recognition_system_prompt,
            user_prompt,
            image_url,
        )
        return self._parse_response(raw)

    def _parse_response(self, raw: dict) -> dict:
        try:
            data = self._parse_json_content(raw, {"dishes": [], "notes": "Failed to parse response"})

            return {
                "dishes": data.get("dishes", []),
                "notes": data.get("notes", ""),
                "raw_response": raw,
            }
        except Exception as e:
            logger.warning(f"Failed to parse Qwen response: {e}")
            return {"dishes": [], "notes": str(e), "raw_response": raw}

    def _parse_json_content(self, raw: dict, fallback: dict) -> dict:
        content = self._extract_content(raw)

        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if not json_match:
            return fallback

        data = json.loads(json_match.group())
        return data if isinstance(data, dict) else fallback

    def _rate_limit(self):
        now = time.time()
        window = 1.0
        self._last_request_times = [t for t in self._last_request_times if now - t < window]

        if len(self._last_request_times) >= self.max_qps:
            sleep_time = window - (now - self._last_request_times[0]) + 0.01
            if sleep_time > 0:
                time.sleep(sleep_time)

        self._last_request_times.append(time.time())

    def _build_payload(self, system_prompt: str, user_prompt: str, image_url: str) -> dict:
        if self._uses_openai_chat_completions():
            return {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
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
                    {"role": "system", "content": system_prompt},
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

    def _build_image_url(self, image_path: str) -> str:
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        ext = image_path.rsplit(".", 1)[-1].lower()
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
        return f"data:{mime};base64,{image_data}"

    def _format_candidate_dishes(self, candidate_dishes: list[dict]) -> str:
        if not candidate_dishes:
            return "所有菜品"

        dish_lines = []
        for d in candidate_dishes:
            name = d.get("name", "")
            desc = d.get("description", "")
            if desc:
                dish_lines.append(f"- {name}（{desc}）")
            else:
                dish_lines.append(f"- {name}")
        return "\n".join(dish_lines)

    def _request_model(self, system_prompt: str, user_prompt: str, image_url: str) -> dict:
        self._rate_limit()
        payload = self._build_payload(system_prompt, user_prompt, image_url)
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
                return resp.json()
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

        raise RuntimeError("Qwen request failed without response")

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

    def describe_dishes(self, image_path: str) -> dict:
        """Describe dishes in image without identifying them. Returns visual descriptions.

        This method is used to help admins better understand the visual features of dishes
        in an image, which can help write better dish descriptions.

        Returns:
            {
                "description": "Natural language description of dishes in the image",
                "raw_response": raw API response
            }
        """
        self._rate_limit()

        # Read and encode image
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        # Detect image type
        ext = image_path.rsplit(".", 1)[-1].lower()
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
        image_url = f"data:{mime};base64,{image_data}"

        # Build payload with describe prompts
        if self._uses_openai_chat_completions():
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": self.description_system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": self.description_user_prompt},
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ],
                    },
                ],
            }
        else:
            payload = {
                "model": self.model,
                "input": {
                    "messages": [
                        {"role": "system", "content": self.description_system_prompt},
                        {
                            "role": "user",
                            "content": [
                                {"image": image_url},
                                {"text": self.description_user_prompt},
                            ],
                        },
                    ]
                },
                "parameters": {"result_format": "message"},
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
                content = self._extract_content(raw)
                return {
                    "description": content,
                    "raw_response": raw,
                }
            except requests.Timeout:
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
            except requests.RequestException as e:
                resp = getattr(e, "response", None)
                if resp is not None:
                    logger.error(
                        "Qwen describe request failed: status=%s url=%s body=%s",
                        resp.status_code,
                        self.api_url,
                        resp.text[:1000],
                    )
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)

        return {"description": "", "raw_response": None}
