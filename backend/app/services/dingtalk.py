import logging
import re
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

logger = logging.getLogger(__name__)

DINGTALK_API_BASE = "https://oapi.dingtalk.com"
DINGTALK_API_V2_BASE = "https://api.dingtalk.com"
DINGTALK_ROBOT_WEBHOOK_PATH = "/robot/send"
DEFAULT_DINGTALK_ROBOT_WEBHOOK_PREFIX = "[营养监测系统提醒]"
MAX_DINGTALK_ROBOT_WEBHOOK_PREFIX_LENGTH = 64
SENSITIVE_QUERY_PARAM_PATTERN = re.compile(
    r"([?&](?:access_token|appsecret|clientsecret|sign)=)[^&#\s\"']+",
    re.IGNORECASE,
)


def redact_dingtalk_request_error(error: Exception | str) -> str:
    """Return an exception message safe for logs and persisted task records."""
    return SENSITIVE_QUERY_PARAM_PATTERN.sub(r"\1<redacted>", str(error))


def normalize_robot_webhook_url(value) -> str:
    """Validate and normalize an official DingTalk custom-robot webhook URL."""
    webhook_url = str(value or "").strip()
    if not webhook_url:
        return ""

    parsed = urlsplit(webhook_url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(
            "钉钉 Webhook 必须是 https://oapi.dingtalk.com/robot/send?access_token=... 格式"
        ) from exc
    query = parse_qs(parsed.query, keep_blank_values=True)
    access_tokens = query.get("access_token") or []
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() != "oapi.dingtalk.com"
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path.rstrip("/") != DINGTALK_ROBOT_WEBHOOK_PATH
        or not any(str(token).strip() for token in access_tokens)
        or parsed.fragment
    ):
        raise ValueError(
            "钉钉 Webhook 必须是 https://oapi.dingtalk.com/robot/send?access_token=... 格式"
        )
    return webhook_url


def resolve_robot_webhook_url(config: dict) -> str:
    """Resolve a runtime webhook URL, with the legacy token env as fallback."""
    explicit_url = str(config.get("MENU_REMINDER_DINGTALK_WEBHOOK_URL") or "").strip()
    if explicit_url:
        return normalize_robot_webhook_url(explicit_url)

    token = str(config.get("DINGTALK_WEBHOOK_TOKEN") or "").strip()
    if not token:
        return ""
    query = urlencode({"access_token": token})
    return normalize_robot_webhook_url(f"{DINGTALK_API_BASE}{DINGTALK_ROBOT_WEBHOOK_PATH}?{query}")


def normalize_robot_webhook_prefix(value) -> str:
    """Validate the single-line prefix prepended to robot webhook messages."""
    prefix = str(value or "").strip()
    if not prefix:
        raise ValueError("钉钉 Webhook 推送前缀不能为空")
    if "\n" in prefix or "\r" in prefix:
        raise ValueError("钉钉 Webhook 推送前缀只能占一行")
    if len(prefix) > MAX_DINGTALK_ROBOT_WEBHOOK_PREFIX_LENGTH:
        raise ValueError(
            f"钉钉 Webhook 推送前缀不能超过 {MAX_DINGTALK_ROBOT_WEBHOOK_PREFIX_LENGTH} 个字符"
        )
    return prefix


def resolve_robot_webhook_prefix(config: dict) -> str:
    configured_prefix = config.get("MENU_REMINDER_DINGTALK_WEBHOOK_PREFIX")
    if configured_prefix is None or not str(configured_prefix).strip():
        return DEFAULT_DINGTALK_ROBOT_WEBHOOK_PREFIX
    return normalize_robot_webhook_prefix(configured_prefix)


class DingTalkService:
    def __init__(self, config: dict):
        self.config = config
        self.app_key = config.get("DINGTALK_APP_KEY", "")
        self.app_secret = config.get("DINGTALK_APP_SECRET", "")
        self.agent_id = config.get("DINGTALK_AGENT_ID", "")
        self.corp_id = config.get("DINGTALK_CORP_ID", "")
        self._access_token = None
        self._token_expires = 0

    def get_access_token(self) -> str:
        if self._access_token and time.time() < self._token_expires - 60:
            return self._access_token

        resp = self._request_with_retry(
            "GET",
            f"{DINGTALK_API_BASE}/gettoken",
            params={"appkey": self.app_key, "appsecret": self.app_secret},
        )
        data = resp.json()
        if data.get("errcode") != 0:
            raise Exception(f"Failed to get access token: {data}")

        self._access_token = data["access_token"]
        self._token_expires = time.time() + data.get("expires_in", 7200)
        return self._access_token

    def get_user_info_by_code(self, auth_code: str) -> dict:
        """Exchange a browser OAuth code for the current DingTalk user's profile."""
        user_token_data = self.get_user_access_token_by_code(auth_code)
        user_access_token = user_token_data["accessToken"]
        profile = self.get_current_user_profile(user_access_token)

        union_id = profile.get("unionId") or profile.get("unionid")
        user_id = None
        if union_id:
            user_id = self.get_userid_by_unionid(union_id)

        if not user_id:
            user_id = profile.get("userid") or profile.get("userId") or profile.get("openId")
        if not user_id:
            raise Exception(f"No userid/openId in OAuth user profile: {profile}")

        detail = self.get_user_detail(user_id)
        if detail:
            return detail

        return {
            "userid": user_id,
            "unionid": union_id,
            "name": profile.get("nick") or profile.get("name") or user_id,
            "avatar": profile.get("avatarUrl"),
            "mobile": profile.get("mobile"),
            "stateCode": profile.get("stateCode"),
        }

    def get_legacy_user_info_by_code(self, auth_code: str) -> dict:
        token = self.get_access_token()
        # Get userid from authCode
        resp = self._request_with_retry(
            "GET",
            f"{DINGTALK_API_BASE}/user/getuserinfo",
            params={"access_token": token, "code": auth_code},
        )
        data = resp.json()
        if data.get("errcode") != 0:
            raise Exception(f"Failed to get user info: {data}")

        user_id = data.get("userid")
        if not user_id:
            raise Exception("No userid in response")

        # Get detailed user info
        detail_resp = self._request_with_retry(
            "GET",
            f"{DINGTALK_API_BASE}/user/get",
            params={"access_token": token, "userid": user_id},
        )
        detail = detail_resp.json()
        if detail.get("errcode") != 0:
            # Return basic info if detail fails
            return {"userid": user_id, "name": data.get("name", user_id)}

        return detail

    def get_user_access_token_by_code(self, auth_code: str) -> dict:
        resp = self._request_with_retry(
            "POST",
            f"{DINGTALK_API_V2_BASE}/v1.0/oauth2/userAccessToken",
            json={
                "clientId": self.app_key,
                "clientSecret": self.app_secret,
                "code": auth_code,
                "grantType": "authorization_code",
            },
        )
        data = resp.json()
        if not data.get("accessToken"):
            raise Exception(f"Failed to get user access token: {data}")
        return data

    def get_current_user_profile(self, user_access_token: str) -> dict:
        resp = self._request_with_retry(
            "GET",
            f"{DINGTALK_API_V2_BASE}/v1.0/contact/users/me",
            headers={
                "x-acs-dingtalk-access-token": user_access_token,
                "Content-Type": "application/json",
            },
        )
        data = resp.json()
        if data.get("code") and not (data.get("openId") or data.get("unionId")):
            raise Exception(f"Failed to get current user profile: {data}")
        return data

    def get_userid_by_unionid(self, union_id: str) -> str | None:
        token = self.get_access_token()
        resp = self._request_with_retry(
            "POST",
            f"{DINGTALK_API_BASE}/topapi/user/getbyunionid",
            params={"access_token": token},
            json={"unionid": union_id},
        )
        data = resp.json()
        if data.get("errcode") != 0:
            logger.warning("Failed to map DingTalk unionId to userId: %s", data)
            return None
        result = data.get("result") or {}
        return result.get("userid")

    def get_user_detail(self, user_id: str) -> dict | None:
        token = self.get_access_token()
        detail_resp = self._request_with_retry(
            "GET",
            f"{DINGTALK_API_BASE}/user/get",
            params={"access_token": token, "userid": user_id},
        )
        detail = detail_resp.json()
        if detail.get("errcode") != 0:
            logger.warning("Failed to get DingTalk user detail: %s", detail)
            return None
        return detail

    def get_department_list(self) -> list:
        token = self.get_access_token()
        resp = self._request_with_retry(
            "GET",
            f"{DINGTALK_API_BASE}/department/list",
            params={"access_token": token},
        )
        data = resp.json()
        return data.get("department", [])

    def get_department_users(self, dept_id: int, offset: int = 0, size: int = 100) -> dict:
        token = self.get_access_token()
        resp = self._request_with_retry(
            "GET",
            f"{DINGTALK_API_BASE}/user/listbypage",
            params={
                "access_token": token,
                "department_id": dept_id,
                "offset": offset,
                "size": size,
            },
        )
        return resp.json()

    def send_work_notification(self, user_ids: list[str], msg: dict) -> dict:
        """Send work notification to users."""
        token = self.get_access_token()
        payload = {
            "agent_id": int(self.agent_id),
            "userid_list": ",".join(user_ids[:100]),
            "msg": msg,
        }
        resp = self._request_with_retry(
            "POST",
            f"{DINGTALK_API_BASE}/topapi/message/corpconversation/asyncsend_v2",
            params={"access_token": token},
            json=payload,
        )
        return resp.json()

    def send_robot_webhook(self, msg: dict, *, webhook_url: str | None = None) -> dict:
        """Send a message through a DingTalk custom-robot webhook."""
        resolved_url = normalize_robot_webhook_url(webhook_url) if webhook_url else resolve_robot_webhook_url(self.config)
        if not resolved_url:
            raise ValueError("未配置钉钉机器人 Webhook")
        resp = self._request_with_retry("POST", resolved_url, json=msg)
        return resp.json()

    def send_card_message(self, user_id: str, title: str, subtitle: str, summary: str, jump_url: str) -> bool:
        msg = {
            "msgtype": "oa",
            "oa": {
                "message_url": jump_url,
                "head": {"bgcolor": "FFBBBBBB", "text": title},
                "body": {
                    "title": subtitle,
                    "content": summary,
                },
            },
        }
        result = self.send_work_notification([user_id], msg)
        return result.get("errcode") == 0

    def _request_with_retry(self, method: str, url: str, max_retries: int = 3, **kwargs) -> requests.Response:
        for attempt in range(max_retries):
            try:
                resp = requests.request(method, url, timeout=10, **kwargs)
                resp.raise_for_status()
                return resp
            except requests.RequestException as e:
                safe_error = redact_dingtalk_request_error(e)
                if attempt == max_retries - 1:
                    # Do not propagate the original RequestException: its URL may
                    # contain a robot access_token and exception chaining would
                    # expose it again in callers that log exc_info=True.
                    raise requests.RequestException(safe_error) from None
                wait = 2 ** attempt
                logger.warning(
                    "DingTalk API retry %s/%s after %ss: %s",
                    attempt + 1,
                    max_retries,
                    wait,
                    safe_error,
                )
                time.sleep(wait)
        raise Exception("Max retries exceeded")
