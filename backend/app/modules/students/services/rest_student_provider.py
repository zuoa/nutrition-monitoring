"""定制 REST 学生名单接口适配器。"""

import requests

from app.modules.students.services.sync_provider import StudentRosterEntry


class StudentRosterProviderError(RuntimeError):
    pass


class RestStudentListProvider:
    """适配 ``code/msg/data`` 协议的学生名单接口。

    ``apikey`` 只从运行时配置读取，不记录 URL、请求参数或原始响应，
    避免密钥和身份证号进入日志。
    """

    key = "rest_student_list"
    label = "外部学生名单"

    def __init__(self, config: dict):
        self.url = str(config.get("STUDENT_SYNC_REST_URL") or "").strip()
        self.api_key = str(config.get("STUDENT_SYNC_REST_API_KEY") or "").strip()
        self.http_method = str(config.get("STUDENT_SYNC_REST_HTTP_METHOD") or "GET").strip().upper()
        self.timeout = max(1, int(config.get("STUDENT_SYNC_REST_TIMEOUT_SECONDS") or 15))
        if self.http_method not in {"GET", "POST"}:
            raise StudentRosterProviderError("STUDENT_SYNC_REST_HTTP_METHOD 仅支持 GET 或 POST")

    @property
    def configured(self) -> bool:
        return bool(self.url and self.api_key)

    def fetch_students(self) -> list[StudentRosterEntry]:
        if not self.configured:
            raise StudentRosterProviderError("外部学生名单接口未配置 URL 或 API Key")

        request_kwargs = {
            "timeout": self.timeout,
            "headers": {"Accept": "application/json"},
        }
        if self.http_method == "GET":
            request_kwargs["params"] = {"apikey": self.api_key}
        else:
            request_kwargs["data"] = {"apikey": self.api_key}

        try:
            response = requests.request(self.http_method, self.url, **request_kwargs)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            # requests 异常可能包含带 apikey 的完整 GET URL，不可透传到
            # TaskLog 或应用日志。
            status = getattr(getattr(exc, "response", None), "status_code", None)
            detail = f"HTTP {status}" if status else "网络异常"
            raise StudentRosterProviderError(f"请求外部学生名单失败：{detail}") from exc
        except ValueError as exc:
            raise StudentRosterProviderError("外部学生名单响应不是有效 JSON") from exc

        if not isinstance(payload, dict):
            raise StudentRosterProviderError("外部学生名单响应不是 JSON 对象")
        if str(payload.get("code")) != "1":
            message = str(payload.get("msg") or "未知错误")[:200]
            raise StudentRosterProviderError(f"外部学生名单返回失败：{message}")
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise StudentRosterProviderError("外部学生名单 data 不是数组")

        return [self._normalize(row) for row in rows if isinstance(row, dict)]

    @staticmethod
    def _normalize(row: dict) -> StudentRosterEntry:
        def value(key):
            return str(row.get(key) or "").strip()

        user_code = value("user_code")
        return StudentRosterEntry(
            external_id=user_code,
            student_no=user_code,
            registration_no=value("user_account"),
            name=value("user_name"),
            gender=value("user_sex"),
            identity_number=value("id_number"),
            grade_name=value("grade_name"),
            class_name=value("class_name"),
            dorm_building=value("floor_name"),
            dorm_floor=value("storey_name"),
            dorm_room=value("dorm_name"),
        )
