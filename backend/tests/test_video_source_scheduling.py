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

        def conf_update(self, **kwargs):
            self.conf.update(kwargs)

        def task(self, *args, **kwargs):
            def decorator(fn):
                return _FakeTaskWrapper(fn)
            return decorator

        def __getattr__(self, name):
            if name == "conf":
                return self.conf
            if name == "Task":
                return object
            raise AttributeError(name)

    def _fake_crontab(*args, **kwargs):
        return {"args": args, "kwargs": kwargs}

    celery_module.Celery = _FakeCelery
    schedules_module.crontab = _fake_crontab
    sys.modules["celery"] = celery_module
    sys.modules["celery.schedules"] = schedules_module

from app import db  # noqa: E402
import app.models  # noqa: F401,E402
from app.models import TaskLog, VideoSource  # noqa: E402
from app.services.video_sources.manager import VideoSourceManager  # noqa: E402
from app.tasks.video import _find_active_sync_task, _get_scheduled_sync_target_date, _resolve_sync_channel_ids, _resolve_sync_meal_windows, _resolve_target_date, schedule_video_source_sync  # noqa: E402


class VideoSourceSchedulingTests(unittest.TestCase):
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
        db.session.query(VideoSource).delete()
        db.session.commit()

    def tearDown(self):
        db.session.rollback()

    def test_resolve_sync_channel_ids_prefers_hikvision_camera_ids(self):
        channel_ids = _resolve_sync_channel_ids(
            {
                "cameras": [
                    {"channel_id": "8", "host": "192.168.1.88"},
                    {"channel_id": "9", "host": "192.168.1.89"},
                ],
            },
        )

        self.assertEqual(channel_ids, ["8", "9"])

    def test_scheduled_sync_uses_persisted_trigger_time(self):
        manager = VideoSourceManager(self.app.config)
        manager.create_source({
            "name": "食堂主 NVR",
            "source_type": "nvr",
            "status": "enabled",
            "is_active": True,
            "config": {
                "host": "192.168.1.10",
                "port": 8080,
                "username": "admin",
                "password": "secret-1",
                "channel_ids": ["8"],
                "meal_windows": [{"start": "11:30", "end": "13:00"}],
                "download_trigger_time": "08:15",
                "local_storage_path": "/data/nvr_cache",
                "retention_days": 3,
            },
        })

        target_date = _get_scheduled_sync_target_date(
            self.app.config,
            now=datetime(2026, 4, 3, 8, 15),
        )

        self.assertIsNotNone(target_date)
        self.assertEqual(target_date.isoformat(), "2026-04-03")

    def test_scheduled_sync_skips_when_today_already_has_sync_task(self):
        manager = VideoSourceManager(self.app.config)
        manager.create_source({
            "name": "食堂主 NVR",
            "source_type": "nvr",
            "status": "enabled",
            "is_active": True,
            "config": {
                "host": "192.168.1.10",
                "port": 8080,
                "username": "admin",
                "password": "secret-1",
                "channel_ids": ["8"],
                "meal_windows": [{"start": "11:30", "end": "13:00"}],
                "download_trigger_time": "08:15",
                "local_storage_path": "/data/nvr_cache",
                "retention_days": 3,
            },
        })
        db.session.add(TaskLog(task_type="video_source_sync", task_date=datetime(2026, 4, 3).date()))
        db.session.commit()

        target_date = _get_scheduled_sync_target_date(
            self.app.config,
            now=datetime(2026, 4, 3, 8, 15),
        )

        self.assertIsNone(target_date)

    def test_resolve_sync_meal_windows_defaults_to_three_periods(self):
        windows = _resolve_sync_meal_windows({})

        self.assertEqual(windows, [
            {"start": "07:00", "end": "09:00"},
            {"start": "11:30", "end": "13:00"},
            {"start": "17:30", "end": "19:00"},
        ])

    def test_resolve_target_date_uses_configured_local_timezone(self):
        target_date = _resolve_target_date(
            {"VIDEO_TIMEZONE": "Asia/Shanghai"},
            now=datetime(2026, 4, 2, 16, 30, tzinfo=timezone.utc),
        )

        self.assertEqual(target_date.isoformat(), "2026-04-03")

    def test_find_active_sync_task_returns_running_task(self):
        db.session.add(TaskLog(task_type="video_source_sync", task_date=datetime(2026, 4, 3).date(), status="success"))
        db.session.add(TaskLog(task_type="video_source_sync", task_date=datetime(2026, 4, 3).date(), status="running"))
        db.session.commit()

        active = _find_active_sync_task()

        self.assertIsNotNone(active)
        self.assertEqual(active.status, "running")

    def test_find_active_sync_task_marks_stale_running_task_failed(self):
        stale_started_at = datetime(2026, 4, 3, 0, 0, tzinfo=timezone.utc)
        task = TaskLog(
            task_type="video_source_sync",
            task_date=datetime(2026, 4, 3).date(),
            status="running",
            started_at=stale_started_at,
        )
        db.session.add(task)
        db.session.commit()

        with mock.patch(
            "app.tasks.video._utcnow",
            return_value=datetime(2026, 4, 3, 7, 0, tzinfo=timezone.utc),
        ):
            active = _find_active_sync_task()

        self.assertIsNone(active)
        db.session.refresh(task)
        self.assertEqual(task.status, "failed")
        self.assertIsNotNone(task.finished_at)
        self.assertIn("自动标记为失败", task.error_message or "")

    def test_find_active_sync_task_marks_stale_pending_task_failed(self):
        stale_started_at = datetime(2026, 4, 3, 0, 0, tzinfo=timezone.utc)
        task = TaskLog(
            task_type="video_source_sync",
            task_date=datetime(2026, 4, 3).date(),
            status="pending",
            started_at=stale_started_at,
        )
        db.session.add(task)
        db.session.commit()

        with mock.patch(
            "app.tasks.video._utcnow",
            return_value=datetime(2026, 4, 3, 7, 0, tzinfo=timezone.utc),
        ):
            active = _find_active_sync_task()

        self.assertIsNone(active)
        db.session.refresh(task)
        self.assertEqual(task.status, "failed")
        self.assertIsNotNone(task.finished_at)
        self.assertIn("自动标记为失败", task.error_message or "")

    def test_scheduled_sync_can_catch_up_after_active_overlap(self):
        manager = VideoSourceManager(self.app.config)
        manager.create_source({
            "name": "食堂主 NVR",
            "source_type": "nvr",
            "status": "enabled",
            "is_active": True,
            "config": {
                "host": "192.168.1.10",
                "port": 8080,
                "username": "admin",
                "password": "secret-1",
                "channel_ids": ["8"],
                "meal_windows": [{"start": "11:30", "end": "13:00"}],
                "download_trigger_time": "08:15",
                "local_storage_path": "/data/nvr_cache",
                "retention_days": 3,
            },
        })
        active_task = TaskLog(
            task_type="video_source_sync",
            task_date=datetime(2026, 4, 2).date(),
            status="running",
            started_at=datetime(2026, 4, 3, 0, 10, tzinfo=timezone.utc),
        )
        db.session.add(active_task)
        db.session.commit()

        with (
            mock.patch("app.tasks.video._get_local_now", return_value=datetime(2026, 4, 3, 8, 15)),
            mock.patch("app.tasks.video._utcnow", return_value=datetime(2026, 4, 3, 0, 15, tzinfo=timezone.utc)),
            mock.patch("app.tasks.video.sync_video_source_media.delay") as delay_mock,
        ):
            result = schedule_video_source_sync.run()

        self.assertEqual(result["reason"], "active_task_exists")
        delay_mock.assert_not_called()

        active_task.status = "success"
        active_task.finished_at = datetime(2026, 4, 3, 8, 15, tzinfo=timezone.utc)
        db.session.commit()

        with (
            mock.patch("app.tasks.video._get_local_now", return_value=datetime(2026, 4, 3, 8, 16)),
            mock.patch("app.tasks.video._utcnow", return_value=datetime(2026, 4, 3, 0, 16, tzinfo=timezone.utc)),
            mock.patch("app.tasks.video.sync_video_source_media.delay") as delay_mock,
        ):
            result = schedule_video_source_sync.run()

        self.assertTrue(result["scheduled"])
        self.assertEqual(result["date"], "2026-04-03")
        delay_mock.assert_called_once_with("2026-04-03")


if __name__ == "__main__":
    unittest.main()
