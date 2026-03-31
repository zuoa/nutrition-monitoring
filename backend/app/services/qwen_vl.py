import base64
from difflib import SequenceMatcher
from io import BytesIO
import logging
import time
import json
import re
import requests
from PIL import Image
try:
    from app.services.structured_description import (
        has_structured_description,
        normalize_structured_description,
        parse_composed_description,
    )
except ModuleNotFoundError:
    from structured_description import (
        has_structured_description,
        normalize_structured_description,
        parse_composed_description,
    )
from prompt_utils import render_prompt_template
from prompt_defaults import (
    QWEN_DESCRIPTION_SYSTEM_PROMPT as DEFAULT_QWEN_DESCRIPTION_SYSTEM_PROMPT,
    QWEN_DESCRIPTION_USER_PROMPT as DEFAULT_QWEN_DESCRIPTION_USER_PROMPT,
    QWEN_RECOGNITION_SYSTEM_PROMPT as DEFAULT_QWEN_RECOGNITION_SYSTEM_PROMPT,
    QWEN_RECOGNITION_USER_PROMPT_TEMPLATE as DEFAULT_QWEN_RECOGNITION_USER_PROMPT_TEMPLATE,
)

logger = logging.getLogger(__name__)

LOW_CONFIDENCE_THRESHOLD = 0.6
MAX_REGION_CROPS = 6

REGION_DETECTION_SYSTEM_PROMPT = """你是一个学校食堂餐盘分区助手。你的任务是先判断这张图里大约有多少个独立菜区，并给出每个菜区的大致位置。

要求：
1. 先估计整张图可见的独立菜区数量，再逐个输出区域。
2. 每个区域尽量包住一道完整菜品或一个独立主食区，不要只框住局部配菜。
3. 坐标使用整张图的相对百分比，范围 0 到 100。
4. 若存在遮挡、堆叠、边界不清，也要尽量划分并在 notes 里说明。
5. 只返回 JSON，不要输出其他文字。"""

REGION_DETECTION_USER_PROMPT = """请输出：
{
  "dish_count": 3,
  "regions": [
    {
      "index": 1,
      "position": "左上/中间/右下等",
      "bbox": {"x1": 5, "y1": 8, "x2": 45, "y2": 42},
      "visual_hint": "30字以内，描述该区域颜色、形状、酱汁、主食材特征"
    }
  ],
  "notes": "可选，说明遮挡、反光、重叠、边界不清"
}

注意：
1. bbox 必须覆盖整道菜的大致范围。
2. x1 < x2，y1 < y2。
3. 如果不确定精确边界，也要给出尽量合理的框。"""


class QwenVLService:
    def __init__(self, config: dict):
        self.api_key = config.get("QWEN_API_KEY", "")
        self.api_url = config.get("QWEN_API_URL", "")
        self.model = config.get("QWEN_MODEL", "qwen-vl-max")
        self.timeout = int(config.get("QWEN_TIMEOUT", 30))
        self.max_qps = int(config.get("QWEN_MAX_QPS", 10))
        self.temperature = self._resolve_temperature(config.get("QWEN_TEMPERATURE", 0.1))
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

    def _resolve_temperature(self, value: object) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.1

    def recognize_dishes(self, image_path: str, candidate_dishes: list[dict]) -> dict:
        """Recognize dishes in image. Returns {dishes: [{name, confidence}], notes, raw_response}

        Args:
            candidate_dishes: List of dict with 'name', optional 'description', and optional structured features
        """
        image_url = self._build_image_url(image_path)
        dish_list_with_desc = self._format_candidate_dishes(candidate_dishes)
        candidate_lookup = self._build_candidate_lookup(candidate_dishes)
        region_detection_raw = None
        region_match_raw: list[dict] = []

        try:
            region_detection_raw, region_data = self._detect_dish_regions(image_url)
            cropped_regions = self._build_region_crops(image_path, region_data.get("regions", []))
            cropped_dishes = []
            region_notes = [region_data.get("notes", "")]
            region_total = region_data.get("dish_count") or len(cropped_regions)

            for region in cropped_regions:
                region_raw = self._request_model(
                    self.recognition_system_prompt,
                    self._build_region_recognition_prompt(
                        dish_list_with_desc=dish_list_with_desc,
                        region=region,
                        dish_count=region_total,
                    ),
                    region["image_url"],
                )
                region_match_raw.append(region_raw)
                region_result = self._parse_response(region_raw)
                region_notes.append(region_result.get("notes", ""))
                dishes = self._canonicalize_dishes(
                    region_result.get("dishes", []),
                    candidate_lookup,
                )
                if dishes:
                    cropped_dishes.append({
                        **dishes[0],
                        "notes": self._merge_notes(region_result.get("notes", "")),
                    })

            cropped_dishes = self._dedupe_dishes(cropped_dishes)
            if cropped_dishes:
                return {
                    "dishes": cropped_dishes,
                    "notes": self._merge_notes(*region_notes),
                    "raw_response": {
                        "region_detection": region_detection_raw,
                        "region_matches": region_match_raw,
                    },
                }
        except Exception as e:
            logger.warning("Crop-based recognition failed: %s", e)

        fallback_result = self._recognize_single_stage(image_url, dish_list_with_desc)
        fallback_result["dishes"] = self._attach_recognition_notes(
            self._canonicalize_dishes(
                fallback_result.get("dishes", []),
                candidate_lookup,
            ),
            fallback_result.get("notes", ""),
        )
        if region_detection_raw or region_match_raw:
            fallback_result["raw_response"] = {
                "region_detection": region_detection_raw,
                "region_matches": region_match_raw,
                "fallback": fallback_result.get("raw_response"),
            }
        return fallback_result

    def _recognize_single_stage(self, image_url: str, dish_list_with_desc: str) -> dict:
        user_prompt = render_prompt_template(
            self.recognition_user_prompt_template,
            {
                "dish_list_with_desc": dish_list_with_desc,
                "dish_list_with_features": dish_list_with_desc,
            },
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

    def _build_payload(self, system_prompt: str, user_prompt: str, image_url: str, temperature: float | None = None) -> dict:
        system_prompt = (system_prompt or "").strip()
        if temperature is None:
            temperature = self.temperature
        if self._uses_openai_chat_completions():
            payload = {
                "model": self.model,
                "messages": [],
            }
            messages = payload["messages"]
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            )
            if temperature is not None:
                payload["temperature"] = temperature
            return payload

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append(
            {
                "role": "user",
                "content": [
                    {"image": image_url},
                    {"text": user_prompt},
                ],
            },
        )
        payload = {
            "model": self.model,
            "input": {
                "messages": messages
            },
            "parameters": {"result_format": "message"},
        }
        if temperature is not None:
            payload["parameters"]["temperature"] = temperature
        return payload

    def _guess_image_mime_type(self, image_path: str) -> str:
        ext = image_path.rsplit(".", 1)[-1].lower() if "." in image_path else ""
        if ext in ("jpg", "jpeg"):
            return "image/jpeg"
        if ext == "png":
            return "image/png"
        if ext == "webp":
            return "image/webp"
        if ext == "bmp":
            return "image/bmp"
        return "image/png"

    def _build_image_url(self, image_path: str) -> str:
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")
        mime = self._guess_image_mime_type(image_path)
        return f"data:{mime};base64,{image_data}"

    def _pil_image_to_data_url(self, image: Image.Image) -> str:
        buf = BytesIO()
        image.save(buf, format="JPEG", quality=92)
        image_data = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{image_data}"

    def _format_candidate_dishes(self, candidate_dishes: list[dict]) -> str:
        if not candidate_dishes:
            return "所有菜品"

        dish_lines = []
        for d in candidate_dishes:
            name = str(d.get("name", "") or "").strip()
            parsed = parse_composed_description(d.get("description"))
            summary = str(parsed.get("summary", "") or "").strip()

            structured = normalize_structured_description(d.get("structured_description"))
            if not has_structured_description(structured):
                structured = normalize_structured_description(parsed.get("structured_description"))

            feature_parts = []
            for key, label in [
                ("mainIngredients", "主食材"),
                ("colors", "颜色"),
                ("cuts", "形态"),
                ("texture", "质地"),
                ("sauce", "汁感"),
                ("garnishes", "配菜"),
                ("confusableWith", "易混淆菜"),
            ]:
                value = str(structured.get(key, "") or "").strip()
                if value:
                    feature_parts.append(f"{label}={value}")

            item_lines = [f"- {name}"]
            if summary:
                item_lines.append(f"  视觉摘要：{summary}")
            if feature_parts:
                item_lines.append(f"  识别特征：{'；'.join(feature_parts)}")
            dish_lines.append("\n".join(item_lines))
        return "\n".join(dish_lines)

    def _request_model(self, system_prompt: str, user_prompt: str, image_url: str) -> dict:
        self._rate_limit()
        payload = self._build_payload(system_prompt, user_prompt, image_url)
        return self._post_payload(payload)

    def _post_payload(self, payload: dict) -> dict:
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

    def _detect_dish_regions(self, image_url: str) -> tuple[dict, dict]:
        raw = self._request_model(
            REGION_DETECTION_SYSTEM_PROMPT,
            REGION_DETECTION_USER_PROMPT,
            image_url,
        )
        data = self._parse_json_content(
            raw,
            {"dish_count": 0, "regions": [], "notes": ""},
        )
        regions = self._normalize_regions(data.get("regions", []))
        return raw, {
            "dish_count": max(int(data.get("dish_count") or 0), len(regions)),
            "regions": regions,
            "notes": self._normalize_note(data.get("notes", "")),
        }

    def _normalize_regions(self, regions: object) -> list[dict]:
        if not isinstance(regions, list):
            return []

        normalized = []
        for idx, item in enumerate(regions):
            if not isinstance(item, dict):
                continue
            bbox = item.get("bbox")
            if not isinstance(bbox, dict):
                continue
            x1 = self._clamp_pct(bbox.get("x1"))
            y1 = self._clamp_pct(bbox.get("y1"))
            x2 = self._clamp_pct(bbox.get("x2"))
            y2 = self._clamp_pct(bbox.get("y2"))
            if x2 - x1 < 6 or y2 - y1 < 6:
                continue
            normalized.append({
                "index": int(item.get("index") or idx + 1),
                "position": self._normalize_note(item.get("position", "")),
                "visual_hint": self._normalize_note(item.get("visual_hint", "")),
                "bbox": {
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2,
                },
            })
            if len(normalized) >= MAX_REGION_CROPS:
                break

        normalized.sort(key=lambda item: item["index"])
        return normalized

    def _clamp_pct(self, value: object) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(100.0, numeric))

    def _build_region_crops(self, image_path: str, regions: list[dict]) -> list[dict]:
        cropped_regions = []
        with Image.open(image_path) as image:
            rgb_image = image.convert("RGB")
            width, height = rgb_image.size
            for region in regions:
                bbox = region.get("bbox", {})
                left = int(width * float(bbox.get("x1", 0)) / 100.0)
                top = int(height * float(bbox.get("y1", 0)) / 100.0)
                right = int(width * float(bbox.get("x2", 100)) / 100.0)
                bottom = int(height * float(bbox.get("y2", 100)) / 100.0)

                pad_x = max(int((right - left) * 0.08), 8)
                pad_y = max(int((bottom - top) * 0.08), 8)
                left = max(0, left - pad_x)
                top = max(0, top - pad_y)
                right = min(width, right + pad_x)
                bottom = min(height, bottom + pad_y)

                if right - left < 24 or bottom - top < 24:
                    continue

                crop = rgb_image.crop((left, top, right, bottom))
                cropped_regions.append({
                    **region,
                    "image_url": self._pil_image_to_data_url(crop),
                })
        return cropped_regions

    def _build_region_recognition_prompt(self, dish_list_with_desc: str, region: dict, dish_count: int) -> str:
        position = region.get("position") or "位置未标注"
        visual_hint = region.get("visual_hint") or "无额外视觉提示"
        return f"""候选菜品特征库：
{dish_list_with_desc}

你看到的是整张餐盘中第 {region.get("index", 1)} / {dish_count} 个菜区的裁剪图。
位置：{position}
局部视觉提示：{visual_hint}

请只判断这个局部菜区最可能对应的候选菜品。

要求：
1. 这是局部裁剪图，不要猜测其他区域的菜。
2. 先看主食材、形态、颜色，再用汁感、质地、配菜做二次确认。
3. 如果命中某候选的“易混淆菜”，必须补充说明你最终为何没有选混淆项。
4. 最多只返回 1 个最可能的候选菜名；如果完全无法判断，可以返回空数组。
5. 只允许输出候选列表里的菜名，不要臆造新菜名。
6. 若有遮挡、边界重叠或裁剪不完整，请写在 notes 里。
7. notes 尽量使用固定标签，推荐格式：命中依据：...；混淆项：...；不确定因素：...

返回格式：
{{
  "dishes": [
    {{"name": "菜品名", "confidence": 0.95}}
  ],
  "notes": "可选备注，建议使用固定标签"
}}"""

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
            name = self._normalize_note(item.get("name", ""))
            if not name:
                continue
            confidence = float(item.get("confidence", 0) or 0)
            existing = best_by_name.get(name)
            if existing is None or confidence > float(existing.get("confidence", 0) or 0):
                best_by_name[name] = {
                    "name": name,
                    "confidence": confidence,
                    "notes": self._normalize_note(item.get("notes", "")),
                }

        return sorted(
            best_by_name.values(),
            key=lambda item: float(item.get("confidence", 0) or 0),
            reverse=True,
        )

    def _build_candidate_lookup(self, candidate_dishes: list[dict]) -> list[dict]:
        lookup = []
        for item in candidate_dishes:
            name = self._normalize_note(item.get("name", ""))
            normalized = self._normalize_name(name)
            if not name or not normalized:
                continue
            lookup.append({
                "name": name,
                "normalized": normalized,
            })
        return lookup

    def _canonicalize_dishes(self, dishes: list[dict], candidate_lookup: list[dict]) -> list[dict]:
        if not candidate_lookup:
            return self._dedupe_dishes(dishes)

        canonicalized = []
        for item in dishes:
            raw_name = self._normalize_note(item.get("name", ""))
            candidate_name = self._match_candidate_name(raw_name, candidate_lookup)
            if not candidate_name:
                logger.info("Drop out-of-scope recognized dish: %s", raw_name)
                continue
            canonicalized.append({
                "name": candidate_name,
                "confidence": float(item.get("confidence", 0) or 0),
                "notes": self._normalize_note(item.get("notes", "")),
            })
        return self._dedupe_dishes(canonicalized)

    def _attach_recognition_notes(self, dishes: list[dict], notes: object) -> list[dict]:
        normalized_notes = self._normalize_note(notes)
        if not normalized_notes:
            return dishes

        enriched = []
        for item in dishes:
            enriched.append({
                **item,
                "notes": self._normalize_note(item.get("notes", "")) or normalized_notes,
            })
        return enriched

    def _match_candidate_name(self, raw_name: str, candidate_lookup: list[dict]) -> str:
        normalized = self._normalize_name(raw_name)
        if not normalized:
            return ""

        for item in candidate_lookup:
            if item["normalized"] == normalized:
                return item["name"]

        contains_matches = []
        for item in candidate_lookup:
            candidate_normalized = item["normalized"]
            if normalized in candidate_normalized or candidate_normalized in normalized:
                ratio = SequenceMatcher(None, normalized, candidate_normalized).ratio()
                contains_matches.append((ratio, -len(candidate_normalized), item["name"]))
        if contains_matches:
            contains_matches.sort(reverse=True)
            return contains_matches[0][2]

        best_ratio = 0.0
        best_name = ""
        for item in candidate_lookup:
            ratio = SequenceMatcher(None, normalized, item["normalized"]).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_name = item["name"]

        if best_ratio >= 0.72:
            return best_name
        return ""

    def _normalize_name(self, value: str) -> str:
        normalized = value.strip().lower()
        normalized = re.sub(r"[\s\u3000·•,，、;；:：（）()【】\[\]{}\-_/]+", "", normalized)
        return normalized

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
                "structured_description": {...},
                "notes": "",
                "raw_response": raw API response
            }
        """
        self._rate_limit()
        image_url = self._build_image_url(image_path)
        payload = self._build_payload(
            self.description_system_prompt,
            self.description_user_prompt,
            image_url,
        )
        raw = self._post_payload(payload)
        return self._parse_description_response(raw)

    def debug_image_prompt(
        self,
        image_path: str,
        user_prompt: str,
        system_prompt: str = "",
        temperature: float | None = None,
    ) -> dict:
        prompt = (user_prompt or "").strip()
        if not prompt:
            raise ValueError("用户提示词不能为空")

        self._rate_limit()
        image_url = self._build_image_url(image_path)
        payload = self._build_payload(system_prompt, prompt, image_url, temperature=temperature)
        raw = self._post_payload(payload)

        parsed_json = None
        json_parse_error = ""
        try:
            parsed = self._parse_json_content(raw, {})
            if parsed:
                parsed_json = parsed
        except Exception as e:
            json_parse_error = str(e)

        return {
            "content": self._extract_content(raw).strip(),
            "parsed_json": parsed_json,
            "json_parse_error": json_parse_error,
            "raw_response": raw,
            "model": self.model,
            "temperature": self.temperature if temperature is None else temperature,
            "request_format": "openai_chat_completions" if self._uses_openai_chat_completions() else "dashscope_message",
        }

    def _parse_description_response(self, raw: dict) -> dict:
        fallback_description = self._extract_content(raw).strip()
        fallback_item = {
            "position": "",
            "description": fallback_description,
            "structured_description": normalize_structured_description(None),
            "notes": "",
        }
        fallback_descriptions = [fallback_item] if fallback_description else []
        fallback = {
            "description": fallback_description,
            "structured_description": normalize_structured_description(None),
            "notes": "",
            "descriptions": fallback_descriptions,
        }
        try:
            data = self._parse_json_content(raw, {})
            if not data:
                result = fallback
            else:
                parsed_descriptions = self._parse_description_items(data)
                notes = self._normalize_note(data.get("notes", "")) if isinstance(data, dict) else ""
                primary = parsed_descriptions[0] if parsed_descriptions else fallback_item

                result = {
                    "description": primary.get("description", "") or fallback_description,
                    "structured_description": normalize_structured_description(
                        primary.get("structured_description")
                    ),
                    "notes": notes or primary.get("notes", ""),
                    "descriptions": parsed_descriptions or fallback_descriptions,
                }
            result["raw_response"] = raw
            return result
        except Exception as e:
            logger.warning("Failed to parse Qwen describe response: %s", e)
            fallback["notes"] = str(e)
            fallback["raw_response"] = raw
            return fallback

    def _parse_description_items(self, data: object) -> list[dict]:
        if isinstance(data, list):
            source_items = data
        elif isinstance(data, dict):
            raw_items = data.get("dishes")
            if not isinstance(raw_items, list):
                raw_items = data.get("descriptions")
            source_items = raw_items if isinstance(raw_items, list) else []
            if not source_items:
                single_item = self._normalize_description_item(data)
                return [single_item] if self._has_description_content(single_item) else []
        else:
            return []

        items = []
        for item in source_items:
            normalized = self._normalize_description_item(item)
            if self._has_description_content(normalized):
                items.append(normalized)
        return items

    def _normalize_description_item(self, raw: object) -> dict:
        if not isinstance(raw, dict):
            return {
                "position": "",
                "description": "",
                "structured_description": normalize_structured_description(None),
                "notes": "",
            }

        return {
            "position": self._normalize_note(raw.get("position", "")),
            "description": self._normalize_note(raw.get("description", "")),
            "structured_description": normalize_structured_description(
                raw.get("structured_description")
            ),
            "notes": self._normalize_note(raw.get("notes", "")),
        }

    def _has_description_content(self, item: object) -> bool:
        if not isinstance(item, dict):
            return False
        if item.get("description") or item.get("position") or item.get("notes"):
            return True
        structured = item.get("structured_description")
        return bool(isinstance(structured, dict) and any(structured.values()))
