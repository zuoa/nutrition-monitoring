import json
import logging
import time

import requests

logger = logging.getLogger(__name__)


DEMO_AGENT_SYSTEM_PROMPT = """你是“营养洞察Agent”，角色设定是一名专业的膳食营养专家，擅长学校食堂、学生餐盘、单餐结构评估和可执行饮食建议。

你的工作目标：
1. 基于当前餐盘识别结果、营养数据、风险提示和历史对话，回答用户关于这顿饭“吃得是否合理、问题在哪、该怎么调整”的问题。
2. 你的分析重点是膳食结构、能量负荷、蛋白质质量、脂肪控制、钠摄入、膳食纤维、蔬菜比例、主食搭配和整体均衡性。
3. 面对追问时，延续上下文，像真正的专家连续会诊，而不是每次都重新开始。

你的专业判断原则：
1. 优先用“当前这顿饭”的视角给出判断，再补充“若连续多餐如此，可能带来的风险”。
2. 先指出最值得关注的 1 到 2 个核心问题，再给调整动作，不要把所有指标平均展开。
3. 当用户问“够不够”“高不高”“要不要补”时，结合当前分析值、识别菜品和建议摄入占比做出明确判断。
4. 建议必须具体到替换、增减、搭配或下一餐修正，例如“减少汤汁和卤味”“补一个高蛋白菜”“主食减半并加蔬菜”。
5. 如果识别不完整、数据不足或菜品不确定，要明确说明不确定性，不要假装精确。

回答边界：
1. 你不是临床医生，不做疾病诊断，不给药物建议。
2. 不要编造图片里没有出现或上下文没有提供的菜品、克重、疾病史和生化指标。
3. 如果问题超出餐盘分析能力，要明确说明需要更多背景信息。

表达要求：
1. 使用专业、克制、自然的中文，体现“膳食营养专家”的判断力。
2. 默认回答 1 到 3 段，必要时可用 2 到 4 个短要点。
3. 不要使用表格，不要空泛说教，不要反复寒暄。
4. 用户要求“总结/报告”时可以更完整；普通追问只回答当前问题。
5. 若用户在问某个营养素，先给结论，再解释原因，最后给动作建议。
"""


class DemoAgentService:
    def __init__(self, config: dict):
        self.api_key = config.get("OPENAI_API_KEY", "")
        self.base_url = config.get("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
        self.model = config.get("OPENAI_MODEL", "deepseek-chat")
        self.timeout = int(config.get("OPENAI_TIMEOUT", 30))

        self.base_url = self.base_url.rstrip("/")
        if self.base_url.endswith("/chat/completions"):
            self.api_url = self.base_url
        else:
            self.api_url = f"{self.base_url}/chat/completions"

    def reply(self, message: str, history: list[dict] | None = None, analysis_result: dict | None = None) -> str:
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY not configured")

        payload = {
            "model": self.model,
            "messages": self._build_messages(message, history or [], analysis_result or {}),
            "temperature": 0.4,
            "max_tokens": 1000,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        for attempt in range(3):
            try:
                resp = requests.post(
                    self.api_url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                raw = resp.json()
                return self._extract_content(raw).strip()
            except requests.Timeout:
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
            except requests.RequestException as exc:
                logger.warning("Demo agent request failed (attempt %s): %s", attempt + 1, exc)
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)

        return ""

    def _build_messages(self, message: str, history: list[dict], analysis_result: dict) -> list[dict]:
        messages = [{"role": "system", "content": DEMO_AGENT_SYSTEM_PROMPT}]

        analysis_context = self._build_analysis_context(analysis_result)
        if analysis_context:
            messages.append({
                "role": "system",
                "content": f"当前餐盘分析上下文如下，请优先基于这些信息回答：\n{analysis_context}",
            })

        for item in history[-12:]:
            role = item.get("role")
            content = (item.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            messages.append({
                "role": role,
                "content": content[:4000],
            })

        messages.append({"role": "user", "content": message})
        return messages

    def _build_analysis_context(self, analysis_result: dict) -> str:
        if not analysis_result:
            return ""

        payload = {
            "recognized_dishes": analysis_result.get("recognized_dishes", []),
            "matched_dishes": analysis_result.get("matched_dishes", []),
            "nutrition": analysis_result.get("nutrition", {}),
            "suggestions": analysis_result.get("suggestions", []),
            "notes": analysis_result.get("notes", ""),
            "analyzed_at": analysis_result.get("analyzed_at"),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _extract_content(self, raw: dict) -> str:
        content = (
            raw.get("choices", [{}])[0]
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
            return "\n".join(part for part in parts if part)

        return content if isinstance(content, str) else str(content)
