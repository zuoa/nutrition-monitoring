import os
import sys
import tempfile
import types
import unittest
from datetime import timedelta
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

if "celery" not in sys.modules:
    celery_module = types.ModuleType("celery")
    schedules_module = types.ModuleType("celery.schedules")

    class _FakeTaskWrapper:
        def __init__(self, fn):
            self.run = fn
            self.delay = lambda *args, **kwargs: types.SimpleNamespace(id="fake-task-id")

        def __call__(self, *args, **kwargs):
            return self.run(*args, **kwargs)

    class _FakeCelery:
        def __init__(self, *args, **kwargs):
            self.conf = {}

        def task(self, *args, **kwargs):
            def decorator(fn):
                return _FakeTaskWrapper(fn)
            return decorator

        def __getattr__(self, name):
            if name == "Task":
                return object
            raise AttributeError(name)

    celery_module.Celery = _FakeCelery
    schedules_module.crontab = lambda *args, **kwargs: object()
    sys.modules["celery"] = celery_module
    sys.modules["celery.schedules"] = schedules_module

from app import db  # noqa: E402
import app.models  # noqa: F401,E402
from app.models import Student, TaskLog  # noqa: E402
from app.tasks.nutrition import check_all_alerts  # noqa: E402
from app.tasks.reports import dispatch_scheduled_weekly_reports, generate_all_reports  # noqa: E402


class ScheduledNotificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime_dir = tempfile.TemporaryDirectory()
        cls.app = Flask(__name__)
        cls.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SECRET_KEY="test-secret",
            JWT_ALGORITHM="HS256",
            JWT_ACCESS_TOKEN_EXPIRES=timedelta(hours=1),
            APP_TIMEZONE="Asia/Shanghai",
            LOCAL_RUNTIME_CONFIG_PATH=os.path.join(cls.runtime_dir.name, "runtime_config.json"),
            WEEKLY_REPORT_DAY_OF_WEEK="sunday",
            WEEKLY_REPORT_TIME="08:00",
            NUTRITION_ALERT_NOTIFICATION_ENABLED=True,
        )
        db.init_app(cls.app)
        cls.app_context = cls.app.app_context()
        cls.app_context.push()
        db.create_all()

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        db.drop_all()
        cls.app_context.pop()
        cls.runtime_dir.cleanup()

    def setUp(self):
        db.session.query(TaskLog).delete()
        db.session.query(Student).delete()
        db.session.commit()
        runtime_path = self.app.config["LOCAL_RUNTIME_CONFIG_PATH"]
        if os.path.exists(runtime_path):
            os.unlink(runtime_path)
        self.app.config.update(
            WEEKLY_REPORT_DAY_OF_WEEK="sunday",
            WEEKLY_REPORT_TIME="08:00",
            NUTRITION_ALERT_NOTIFICATION_ENABLED=True,
        )

    def tearDown(self):
        db.session.rollback()

    def test_nutrition_alert_task_skips_when_notifications_disabled(self):
        self.app.config["NUTRITION_ALERT_NOTIFICATION_ENABLED"] = False

        with mock.patch("app.services.dingtalk.DingTalkService") as dingtalk_service:
            result = check_all_alerts()

        self.assertEqual(result, {"checked": False, "reason": "notification_disabled"})
        dingtalk_service.assert_not_called()

    def test_weekly_report_dispatches_on_configured_sunday_and_deduplicates(self):
        async_result = types.SimpleNamespace(id="weekly-task-1")
        with mock.patch.object(generate_all_reports, "delay", return_value=async_result) as delay:
            first = dispatch_scheduled_weekly_reports("2026-07-19T08:00:00+08:00")
            second = dispatch_scheduled_weekly_reports("2026-07-19T08:00:30+08:00")

        self.assertTrue(first["scheduled"])
        self.assertEqual(first["period_start"], "2026-07-13")
        self.assertEqual(first["period_end"], "2026-07-19")
        self.assertEqual(second["reason"], "already_dispatched")
        self.assertEqual(delay.call_count, 4)
        self.assertEqual(
            [call.args[0] for call in delay.call_args_list],
            ["personal_weekly", "class_weekly", "grade_weekly", "campus_weekly"],
        )
        for call in delay.call_args_list:
            self.assertEqual(call.args[1:], ("2026-07-13", "2026-07-19"))

    def test_weekly_report_uses_runtime_schedule_without_restart(self):
        from app.services.runtime_config import persist_runtime_overrides

        persist_runtime_overrides(self.app.config, {
            "WEEKLY_REPORT_DAY_OF_WEEK": "saturday",
            "WEEKLY_REPORT_TIME": "09:15",
        })
        with mock.patch.object(generate_all_reports, "delay", return_value=types.SimpleNamespace(id="weekly-task-2")):
            old_schedule = dispatch_scheduled_weekly_reports("2026-07-19T08:00:00+08:00")
            configured_schedule = dispatch_scheduled_weekly_reports("2026-07-18T09:15:00+08:00")

        self.assertEqual(old_schedule["reason"], "not_due")
        self.assertTrue(configured_schedule["scheduled"])


if __name__ == "__main__":
    unittest.main()
