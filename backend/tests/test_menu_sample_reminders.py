import os
import sys
import types
import unittest
from datetime import date, timedelta
from unittest import mock

import requests
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
            self.delay = lambda *args, **kwargs: None

        def __call__(self, *args, **kwargs):
            return self.run(*args, **kwargs)

    class _FakeCelery:
        def __init__(self, *args, **kwargs):
            self.conf = {}

        def task(self, *args, **kwargs):
            def decorator(fn):
                return _FakeTaskWrapper(fn)
            return decorator

    def _fake_crontab(*args, **kwargs):
        return {"args": args, "kwargs": kwargs}

    celery_module.Celery = _FakeCelery
    schedules_module.crontab = _fake_crontab
    sys.modules["celery"] = celery_module
    sys.modules["celery.schedules"] = schedules_module

from app import db  # noqa: E402
import app.models  # noqa: F401,E402
from app.models import CategoryEnum, DailyMenu, Dish, DishSampleImage, RoleEnum, TaskLog, User  # noqa: E402
from app.tasks.menu_reminders import check_menu_sample_reminders  # noqa: E402


class MenuSampleReminderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask(__name__)
        cls.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SECRET_KEY="test-secret",
            JWT_ALGORITHM="HS256",
            JWT_ACCESS_TOKEN_EXPIRES=timedelta(hours=1),
            APP_TIMEZONE="Asia/Shanghai",
            MENU_REMINDER_ENABLED=True,
            MENU_REMINDER_BEFORE_MINUTES=30,
            MENU_REMINDER_MEAL_TIMES={
                "breakfast": "05:00",
                "lunch": "10:30",
                "dinner": "17:00",
                "late_night": "21:00",
            },
            MENU_REMINDER_RESPONSIBLE_USER_IDS=[],
            MENU_REMINDER_DINGTALK_MODE="app",
            MENU_REMINDER_DINGTALK_WEBHOOK_URL="",
            FRONTEND_URL="https://nutrition.example.com/",
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

    def setUp(self):
        db.session.query(TaskLog).delete()
        db.session.query(DishSampleImage).delete()
        db.session.query(DailyMenu).delete()
        db.session.query(Dish).delete()
        db.session.query(User).delete()
        db.session.commit()
        self.app.config["MENU_REMINDER_RESPONSIBLE_USER_IDS"] = []
        self.app.config["MENU_REMINDER_DINGTALK_MODE"] = "app"
        self.app.config["MENU_REMINDER_DINGTALK_WEBHOOK_URL"] = ""
        # 现有用例验证“当顿餐菜单”模式下的提醒逻辑；all 模式用例会在自身覆盖此项。
        self.app.config["RECOGNITION_MENU_SCOPE"] = "meal"

    def tearDown(self):
        db.session.rollback()
        db.session.remove()

    def _create_responsible_user(self, *, role=RoleEnum.canteen_manager) -> User:
        user = User(
            name="食堂负责人",
            role=role,
            dingtalk_user_id="dt-user-1",
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
        return user

    def _create_dish(self, name="红烧肉") -> Dish:
        dish = Dish(
            name=name,
            price=12.0,
            category=CategoryEnum.meat,
            is_active=True,
        )
        db.session.add(dish)
        db.session.flush()
        return dish

    def test_sends_missing_menu_reminder_once_before_meal(self):
        self._create_responsible_user()
        sent_messages = []

        class FakeDingTalk:
            def __init__(self, _cfg):
                pass

            def send_work_notification(self, user_ids, msg):
                sent_messages.append((user_ids, msg))
                return {"errcode": 0}

        with mock.patch("app.services.dingtalk.DingTalkService", FakeDingTalk):
            result = check_menu_sample_reminders("2026-04-03T10:00:00+08:00")
            second_result = check_menu_sample_reminders("2026-04-03T10:00:00+08:00")

        self.assertEqual(result["sent"], 1)
        self.assertEqual(second_result["sent"], 0)
        self.assertEqual(len(sent_messages), 1)
        self.assertEqual(sent_messages[0][0], ["dt-user-1"])
        self.assertIn("午餐菜单未设置", sent_messages[0][1]["text"]["content"])
        self.assertIn("系统入口：https://nutrition.example.com", sent_messages[0][1]["text"]["content"])
        task = TaskLog.query.filter_by(task_type="menu_sample_reminder").first()
        self.assertIsNotNone(task)
        self.assertEqual(task.meta["meal_slot"], "lunch")

    def test_webhook_mode_sends_without_app_or_responsible_users(self):
        self.app.config["MENU_REMINDER_DINGTALK_MODE"] = "webhook"
        self.app.config["MENU_REMINDER_DINGTALK_WEBHOOK_URL"] = (
            "https://oapi.dingtalk.com/robot/send?access_token=robot-token"
        )
        sent_messages = []

        class FakeDingTalk:
            def __init__(self, _cfg):
                pass

            def send_robot_webhook(self, msg):
                sent_messages.append(msg)
                return {"errcode": 0}

            def send_work_notification(self, _user_ids, _msg):
                raise AssertionError("webhook 模式不应发送应用消息")

        with mock.patch("app.services.dingtalk.DingTalkService", FakeDingTalk):
            result = check_menu_sample_reminders("2026-04-03T10:00:00+08:00")

        self.assertEqual(result["sent"], 1)
        self.assertEqual(result["results"][0]["delivery_mode"], "webhook")
        self.assertEqual(result["results"][0]["recipient_count"], 0)
        self.assertIn("午餐菜单未设置", sent_messages[0]["text"]["content"])
        task = TaskLog.query.filter_by(task_type="menu_sample_reminder").first()
        self.assertEqual(task.meta["dingtalk_delivery_mode"], "webhook")
        self.assertEqual(task.meta["recipient_user_ids"], [])

    def test_webhook_http_failure_does_not_persist_access_token(self):
        secret_token = "task-secret-robot-token"
        webhook_url = f"https://oapi.dingtalk.com/robot/send?access_token={secret_token}"
        self.app.config["MENU_REMINDER_DINGTALK_MODE"] = "webhook"
        self.app.config["MENU_REMINDER_DINGTALK_WEBHOOK_URL"] = webhook_url
        http_error = requests.HTTPError(f"503 Server Error for url: {webhook_url}")

        with (
            mock.patch("requests.request", side_effect=http_error),
            mock.patch("app.services.dingtalk.time.sleep"),
            self.assertLogs(level="WARNING") as captured_logs,
        ):
            result = check_menu_sample_reminders("2026-04-03T10:00:00+08:00")

        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["results"][0]["reason"], "send_failed")
        self.assertNotIn(secret_token, "\n".join(captured_logs.output))
        task = TaskLog.query.filter_by(task_type="menu_sample_reminder").first()
        self.assertNotIn(secret_token, task.error_message)
        self.assertIn("access_token=<redacted>", task.error_message)

    def test_sends_when_menu_dish_has_no_sample_image(self):
        responsible = self._create_responsible_user(role=RoleEnum.admin)
        self.app.config["MENU_REMINDER_RESPONSIBLE_USER_IDS"] = [responsible.id]
        dish = self._create_dish("清炒菠菜")
        db.session.add(DailyMenu(
            menu_date=date(2026, 4, 3),
            meal_dish_ids={
                "breakfast": [],
                "lunch": [dish.id],
                "dinner": [],
                "late_night": [],
            },
            is_default=False,
        ))
        db.session.commit()
        sent_messages = []

        class FakeDingTalk:
            def __init__(self, _cfg):
                pass

            def send_work_notification(self, user_ids, msg):
                sent_messages.append((user_ids, msg))
                return {"errcode": 0}

        with mock.patch("app.services.dingtalk.DingTalkService", FakeDingTalk):
            result = check_menu_sample_reminders("2026-04-03T10:00:00+08:00")

        self.assertEqual(result["sent"], 1)
        self.assertIn("缺少菜品样图：清炒菠菜", sent_messages[0][1]["text"]["content"])

    def test_skips_when_menu_and_sample_images_are_ready(self):
        self._create_responsible_user()
        dish = self._create_dish("清炒菠菜")
        db.session.add(DishSampleImage(
            dish_id=dish.id,
            image_path="/tmp/sample.jpg",
            is_active=True,
        ))
        db.session.add(DailyMenu(
            menu_date=date(2026, 4, 3),
            meal_dish_ids={
                "breakfast": [],
                "lunch": [dish.id],
                "dinner": [],
                "late_night": [],
            },
            is_default=False,
        ))
        db.session.commit()

        with mock.patch("app.services.dingtalk.DingTalkService") as fake_dt:
            result = check_menu_sample_reminders("2026-04-03T10:00:00+08:00")

        self.assertEqual(result["sent"], 0)
        fake_dt.assert_not_called()

    def test_all_scope_skips_reminder_when_menu_missing(self):
        # 全量库模式下菜单可选：未设菜单且库里无缺样图菜品时不应打扰。
        self.app.config["RECOGNITION_MENU_SCOPE"] = "all"
        self._create_responsible_user()

        with mock.patch("app.services.dingtalk.DingTalkService") as fake_dt:
            result = check_menu_sample_reminders("2026-04-03T10:00:00+08:00")

        self.assertEqual(result["sent"], 0)
        fake_dt.assert_not_called()

    def test_all_scope_alerts_on_library_dish_without_sample(self):
        # 全量库模式：即便不设菜单，只要库里有菜品缺样图就应提醒，且文案不再提“菜单未设置”。
        self.app.config["RECOGNITION_MENU_SCOPE"] = "all"
        self._create_responsible_user()
        self._create_dish("清炒菠菜")

        sent_messages = []

        class FakeDingTalk:
            def __init__(self, _cfg):
                pass

            def send_work_notification(self, user_ids, msg):
                sent_messages.append((user_ids, msg))
                return {"errcode": 0}

        with mock.patch("app.services.dingtalk.DingTalkService", FakeDingTalk):
            result = check_menu_sample_reminders("2026-04-03T10:00:00+08:00")

        self.assertEqual(result["sent"], 1)
        content = sent_messages[0][1]["text"]["content"]
        self.assertIn("缺少菜品样图：清炒菠菜", content)
        self.assertIn("全量菜品库", content)
        self.assertNotIn("菜单未设置", content)
        self.assertIn("样图采集页面", content)

    def test_all_scope_dedups_reminder_by_date_across_meals(self):
        # 全量库样图检查与餐次无关：同一天跨餐次只提醒一次。
        self.app.config["RECOGNITION_MENU_SCOPE"] = "all"
        self._create_responsible_user()
        self._create_dish("清炒菠菜")

        with mock.patch("app.services.dingtalk.DingTalkService") as fake_dt:
            fake_dt.return_value.send_work_notification.return_value = {"errcode": 0}
            lunch_result = check_menu_sample_reminders("2026-04-03T10:00:00+08:00")
            dinner_result = check_menu_sample_reminders("2026-04-03T16:30:00+08:00")

        self.assertEqual(lunch_result["sent"], 1)
        self.assertEqual(dinner_result["sent"], 0)
        self.assertEqual(fake_dt.return_value.send_work_notification.call_count, 1)
