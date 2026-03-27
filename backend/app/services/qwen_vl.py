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
from prompt_utils import render_prompt_template

logger = logging.getLogger(__name__)

LOW_CONFIDENCE_THRESHOLD = 0.6

VISUAL_GROUNDING_SYSTEM_PROMPT = """你是一个学校食堂餐盘视觉拆解助手。你的任务不是直接报菜名，而是先把餐盘里可见的独立菜区尽可能完整地拆出来。

要求：
1. 先按餐盘区域扫描，尽量不要漏掉明显独立的菜区。
2. 每个区域只描述视觉特征，不要臆造候选列表之外的正式菜名。
3. 重点描述颜色、主要食材形态、切法、酱汁状态、烹饪方式痕迹、配菜特征和大致位置。
4. 若有遮挡、反光、重叠、模糊，要在备注里说明。
5. 只返回 JSON，不要输出其他文字。
"""

VISUAL_GROUNDING_USER_PROMPT = """请先做结构化观察，不要直接输出候选菜名。

返回格式：
{
  "regions": [
    {
      "index": 1,
      "position": "左上/中间/右下等",
      "visual_features": "颜色、形状、酱汁、配菜等视觉特征，30字以内",
      "ingredient_guess": "可选，主食材或类别判断，如鸡肉块/叶类青菜/炒蛋",
      "cooking_style": "可选，如清炒/红烧/带汤汁/油炸",
      "confidence_hint": "high/medium/low"
    }
  ],
  "global_notes": "可选，说明遮挡、重叠、反光、菜区边界不清等"
}"""

MATCH_FROM_STRUCTURE_PROMPT_TEMPLATE = """候选菜品列表：
{dish_list_with_desc}

餐盘结构化观察结果：
{region_summary}

请基于上面的区域观察结果，把每个区域映射到候选菜品列表中最可能的菜名。

要求：
1. 目标是尽量完整识别，不要只返回最显眼的菜。
2. 只允许输出候选列表里的菜名，不要臆造新菜名。
3. 同一菜品不要重复输出；如果两个区域明显是同一道菜，只保留一次。
4. 如果区域看起来像主食、青菜、荤菜、带汁菜，应尽量给出最接近的候选。
5. 对不太确定但值得保留的项，可以给较低 confidence，不要直接漏掉。

返回格式：
{{
  "dishes": [
    {{"name": "菜品名", "confidence": 0.95}}
  ],
  "notes": "说明哪些区域有遮挡、容易混淆、哪些菜区边界不清"
}}"""


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

        stage1_raw = None
        stage2_raw = None
        legacy_raw = None
        stage1_data = {"regions": [], "global_notes": ""}

        try:
            stage1_raw = self._request_model(
                VISUAL_GROUNDING_SYSTEM_PROMPT,
                VISUAL_GROUNDING_USER_PROMPT,
                image_url,
            )
            stage1_data = self._parse_json_content(
                stage1_raw,
                {"regions": [], "global_notes": ""},
            )
        except Exception as e:
            logger.warning("Stage-1 visual grounding failed: %s", e)

        region_summary = json.dumps(stage1_data, ensure_ascii=False, indent=2)
        stage2_prompt = render_prompt_template(
            MATCH_FROM_STRUCTURE_PROMPT_TEMPLATE,
            {
                "dish_list_with_desc": dish_list_with_desc,
                "region_summary": region_summary,
            },
        )

        try:
            stage2_raw = self._request_model(
                self.recognition_system_prompt,
                stage2_prompt,
                image_url,
            )
            stage2_result = self._parse_response(stage2_raw)
            stage2_result["dishes"] = self._dedupe_dishes(stage2_result.get("dishes", []))
            stage2_result["notes"] = self._merge_notes(
                stage1_data.get("global_notes", ""),
                stage2_result.get("notes", ""),
            )
            stage2_result["raw_response"] = {
                "stage1": stage1_raw,
                "stage2": stage2_raw,
            }

            if stage2_result.get("dishes"):
                return stage2_result
        except Exception as e:
            logger.warning("Stage-2 candidate matching failed: %s", e)

        try:
            legacy_raw = self._request_model(
                self.recognition_system_prompt,
                render_prompt_template(
                    self.recognition_user_prompt_template,
                    {"dish_list_with_desc": dish_list_with_desc},
                ),
                image_url,
            )
            legacy_result = self._parse_response(legacy_raw)
            legacy_result["notes"] = self._merge_notes(
                stage1_data.get("global_notes", ""),
                legacy_result.get("notes", ""),
            )
            legacy_result["dishes"] = self._dedupe_dishes(legacy_result.get("dishes", []))
            legacy_result["raw_response"] = {
                "stage1": stage1_raw,
                "stage2": stage2_raw,
                "legacy": legacy_raw,
            }
            return legacy_result
        except Exception:
            raise

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

    def _merge_notes(self, *notes: object) -> str:
        cleaned = []
        for note in notes:
            note = self._normalize_note(note)
            if note and note not in cleaned:
                cleaned.append(note)
        return "；".join(cleaned)

    def _normalize_note(self, note: object) -> str:
        if note is None:
            return ""
        if isinstance(note, str):
            return note.strip()
        if isinstance(note, (list, dict)):
            try:
                return json.dumps(note, ensure_ascii=False).strip()
            except (TypeError, ValueError):
                return str(note).strip()
        return str(note).strip()

    def _dedupe_dishes(self, dishes: list[dict]) -> list[dict]:
        best_by_name = {}
        for item in dishes:
            name = (item.get("name") or "").strip()
            if not name:
                continue
            confidence = float(item.get("confidence", 0) or 0)
            existing = best_by_name.get(name)
            if existing is None or confidence > float(existing.get("confidence", 0) or 0):
                best_by_name[name] = {
                    "name": name,
                    "confidence": confidence,
                }

        return sorted(
            best_by_name.values(),
            key=lambda x: float(x.get("confidence", 0) or 0),
            reverse=True,
        )

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
