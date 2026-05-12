import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from flask import Flask


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

from app import db  # noqa: E402
import app.models  # noqa: F401,E402
from app.api.auth import bp as auth_bp  # noqa: E402
from app.models import RoleEnum, User  # noqa: E402


class AuthApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask(__name__)
        cls.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SECRET_KEY="test-secret",
            JWT_ALGORITHM="HS256",
            JWT_ACCESS_TOKEN_EXPIRES=timedelta(hours=1),
        )
        db.init_app(cls.app)
        cls.app.register_blueprint(auth_bp, url_prefix="/api/auth")
        cls.app_context = cls.app.app_context()
        cls.app_context.push()
        db.create_all()
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        db.drop_all()
        cls.app_context.pop()

    def setUp(self):
        db.session.query(User).delete()
        db.session.commit()

    def tearDown(self):
        db.session.rollback()

    def test_dingtalk_login_prefers_active_synced_user_over_inactive_placeholder(self):
        synced = User(
            dingtalk_user_id="ding-user-1",
            name="张三",
            role=RoleEnum.canteen_manager,
            dept_id="1",
            dept_name="根部门",
            is_active=True,
            sync_at=datetime.now(timezone.utc),
        )
        placeholder = User(
            dingtalk_user_id="oauth-open-1",
            name="张三",
            role=RoleEnum.teacher,
            is_active=False,
        )
        db.session.add_all([synced, placeholder])
        db.session.commit()

        class FakeDingTalk:
            def __init__(self, _cfg):
                pass

            def get_user_info_by_code(self, auth_code):
                self.auth_code = auth_code
                return {
                    "userid": "oauth-open-1",
                    "name": "张三",
                }

        with mock.patch("app.api.auth.DingTalkService", FakeDingTalk):
            res = self.client.post(
                "/api/auth/dingtalk-login",
                json={"authCode": "oauth-code"},
            )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()["data"]
        self.assertEqual(payload["user"]["id"], synced.id)
        self.assertEqual(payload["user"]["role"], "canteen_manager")


if __name__ == "__main__":
    unittest.main()
