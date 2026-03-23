import logging
import time
import requests

logger = logging.getLogger(__name__)

DINGTALK_API_BASE = "https://oapi.dingtalk.com"
DINGTALK_API_V2_BASE = "https://api.dingtalk.com"


class DingTalkService:
    def __init__(self, config: dict):
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
                if attempt == max_retries - 1:
                    raise
                wait = 2 ** attempt
                logger.warning(f"DingTalk API retry {attempt + 1}/{max_retries} after {wait}s: {e}")
                time.sleep(wait)
        raise Exception("Max retries exceeded")
