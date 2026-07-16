import os
import sys
import types
import unittest
from unittest import mock

import requests


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

if "flask_migrate" not in sys.modules:
    flask_migrate = types.ModuleType("flask_migrate")

    class _Migrate:
        def init_app(self, *args, **kwargs):
            return None

    flask_migrate.Migrate = _Migrate
    sys.modules["flask_migrate"] = flask_migrate

if "pythonjsonlogger" not in sys.modules:
    pythonjsonlogger = types.ModuleType("pythonjsonlogger")
    jsonlogger = types.ModuleType("jsonlogger")

    class _JsonFormatter:
        def __init__(self, *args, **kwargs):
            pass

    jsonlogger.JsonFormatter = _JsonFormatter
    pythonjsonlogger.jsonlogger = jsonlogger
    sys.modules["pythonjsonlogger"] = pythonjsonlogger

if "redis" not in sys.modules:
    redis = types.ModuleType("redis")
    redis.from_url = lambda *args, **kwargs: object()
    sys.modules["redis"] = redis

from app.services.dingtalk import DingTalkService  # noqa: E402


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        return None


class DingTalkOAuthLoginTests(unittest.TestCase):
    def test_oauth_code_is_exchanged_with_user_access_token_flow(self):
        calls = []
        responses = [
            _FakeResponse({"accessToken": "user-token", "expireIn": 7200}),
            _FakeResponse({"nick": "张三", "unionId": "union-1", "openId": "open-1"}),
            _FakeResponse({"errcode": 0, "access_token": "app-token", "expires_in": 7200}),
            _FakeResponse({"errcode": 0, "result": {"userid": "user-1"}}),
            _FakeResponse({"errcode": 0, "userid": "user-1", "name": "张三"}),
        ]

        def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            return responses.pop(0)

        service = DingTalkService({
            "DINGTALK_APP_KEY": "ding-client-id",
            "DINGTALK_APP_SECRET": "ding-client-secret",
        })

        with mock.patch("requests.request", side_effect=fake_request):
            user_info = service.get_user_info_by_code("oauth-code")

        self.assertEqual(user_info["userid"], "user-1")
        self.assertEqual(user_info["name"], "张三")
        self.assertEqual(calls[0][0], "POST")
        self.assertIn("/v1.0/oauth2/userAccessToken", calls[0][1])
        self.assertEqual(calls[0][2]["json"]["code"], "oauth-code")
        self.assertEqual(calls[1][0], "GET")
        self.assertIn("/v1.0/contact/users/me", calls[1][1])
        self.assertEqual(calls[1][2]["headers"]["x-acs-dingtalk-access-token"], "user-token")
        self.assertIn("/topapi/user/getbyunionid", calls[3][1])

    def test_robot_webhook_posts_message_without_app_credentials(self):
        webhook_url = "https://oapi.dingtalk.com/robot/send?access_token=robot-token"
        service = DingTalkService({
            "MENU_REMINDER_DINGTALK_WEBHOOK_URL": webhook_url,
        })
        message = {"msgtype": "text", "text": {"content": "测试提醒"}}

        with mock.patch(
            "requests.request",
            return_value=_FakeResponse({"errcode": 0, "errmsg": "ok"}),
        ) as request_mock:
            result = service.send_robot_webhook(message)

        self.assertEqual(result["errcode"], 0)
        request_mock.assert_called_once_with(
            "POST",
            webhook_url,
            timeout=10,
            json=message,
        )

    def test_robot_webhook_can_use_an_explicit_independent_url(self):
        menu_webhook_url = "https://oapi.dingtalk.com/robot/send?access_token=menu-token"
        runtime_webhook_url = "https://oapi.dingtalk.com/robot/send?access_token=runtime-token"
        service = DingTalkService({
            "MENU_REMINDER_DINGTALK_WEBHOOK_URL": menu_webhook_url,
        })
        message = {"msgtype": "text", "text": {"content": "运行日报"}}

        with mock.patch(
            "requests.request",
            return_value=_FakeResponse({"errcode": 0, "errmsg": "ok"}),
        ) as request_mock:
            service.send_robot_webhook(message, webhook_url=runtime_webhook_url)

        request_mock.assert_called_once_with(
            "POST",
            runtime_webhook_url,
            timeout=10,
            json=message,
        )

    def test_robot_webhook_redacts_token_from_http_failure_and_retry_logs(self):
        secret_token = "secret-robot-token"
        webhook_url = f"https://oapi.dingtalk.com/robot/send?access_token={secret_token}"
        service = DingTalkService({
            "MENU_REMINDER_DINGTALK_WEBHOOK_URL": webhook_url,
        })
        http_error = requests.HTTPError(f"500 Server Error for url: {webhook_url}")

        with (
            mock.patch("requests.request", side_effect=http_error) as request_mock,
            mock.patch("app.services.dingtalk.time.sleep"),
            self.assertLogs("app.services.dingtalk", level="WARNING") as captured_logs,
            self.assertRaises(requests.RequestException) as captured_error,
        ):
            service.send_robot_webhook({"msgtype": "text", "text": {"content": "测试提醒"}})

        combined_logs = "\n".join(captured_logs.output)
        self.assertEqual(request_mock.call_count, 3)
        self.assertNotIn(secret_token, str(captured_error.exception))
        self.assertNotIn(secret_token, combined_logs)
        self.assertIn("access_token=<redacted>", str(captured_error.exception))
        self.assertIn("access_token=<redacted>", combined_logs)


if __name__ == "__main__":
    unittest.main()
