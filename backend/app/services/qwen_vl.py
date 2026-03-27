import base64
import logging
import time
import json
import re
import requests

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一个学校食堂菜品识别助手，任务是尽可能完整地识别餐盘里的所有独立菜品。

识别原则：
1. 先按餐盘分区逐个扫描，再汇总，不要只返回最显眼的 1 到 2 个菜。
2. 目标是“宁可给出低置信候选，也不要漏掉明显可见的菜品”。
3. 只识别候选列表中的菜品；如果不在候选列表中，不要臆造新菜名。
4. 同一菜品不要重复输出；同一区域若明显是混合菜，只输出最贴近的一个候选。
5. 米饭、主菜、配菜、青菜、汤类等如果是独立取餐区域，应该分别判断。
6. 调味汁、少量点缀、不可独立成菜的碎料不要单独算一道菜。

请结合每个候选菜品的视觉描述来判断：
- 颜色：主色调、深浅、是否偏红/黄/绿/褐
- 形状：块、片、丝、丁、条、丸、叶、花等
- 质地：干炒、带汁、酥脆、炖煮、清汤、浓芡
- 配菜：葱花、辣椒、木耳、胡萝卜、豆腐、鸡蛋等常见搭配

如果画面存在遮挡、反光、堆叠、模糊，请在 notes 里说明，但仍要尽量给出候选。
只返回 JSON 格式，不要输出其他内容。"""

USER_PROMPT_TEMPLATE = """候选菜品列表：
{dish_list_with_desc}

请按下面流程识别：
1. 先判断餐盘里大约有几个独立取餐区域或独立菜品。
2. 逐个区域与候选菜品列表比对，给出最可能的菜名。
3. 对清晰可见但不够确定的菜，也可以保留较低 confidence，而不是直接漏掉。
4. 输出时按你看到的区域顺序排列。

confidence 取值建议：
- 0.85~0.98：画面清晰且高度确定
- 0.65~0.84：大概率匹配
- 0.40~0.64：存在遮挡或相似菜，但仍值得保留为候选

返回格式：
{{
  "dishes": [
    {{"name": "菜品名", "confidence": 0.95}}
  ],
  "notes": "可选备注，说明遮挡、相似菜、低置信原因"
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

        describe_system_prompt = """你是一个专业的菜品视觉特征描述助手。请仔细观察图片中餐盘/餐具中的菜肴，
从视觉角度描述每道菜的特征，帮助管理员编写更好的菜品描述信息。

请关注以下方面：
- 颜色：菜品呈现的主要颜色和色调
- 形状：食材的形状和切割方式
- 质地：表面特征、酱汁状态、烹饪方式留下的痕迹
- 配菜：可见的配菜、装饰、调料
- 整体印象：这道菜给人的整体感觉

请用中文描述，语言简洁但信息丰富。"""

        describe_user_prompt = """请描述这张图片中餐盘里的所有菜品，每道菜单独描述。
重点关注视觉特征，以便用于菜品信息的描述字段。

格式要求：
1. 按照餐盘中的区域逐一描述
2. 每道菜描述颜色、形状、质地、配菜等视觉特征
3. 如果能推测出烹饪方式也可以提及

请直接描述，不需要 JSON 格式。"""

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
                    {"role": "system", "content": describe_system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": describe_user_prompt},
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
                        {"role": "system", "content": describe_system_prompt},
                        {
                            "role": "user",
                            "content": [
                                {"image": image_url},
                                {"text": describe_user_prompt},
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
