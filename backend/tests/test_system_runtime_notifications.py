import os
import sys
import tempfile
import types
import unittest
from datetime import date, datetime, timedelta
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
from app.models import (  # noqa: E402
    CapturedImage,
    ConsumptionRecord,
    DishRecognition,
    ImageStatusEnum,
    MatchResult,
    MatchStatusEnum,
    TaskLog,
    VideoRecordingJob,
    VideoSource,
    VideoSourceStatus,
    VideoSourceValidationStatus,
)
from app.services.system_runtime_notifications import (  # noqa: E402
    build_system_runtime_summary,
    format_system_runtime_message,
    send_system_runtime_notification,
)
from app.tasks.system_notifications import dispatch_daily_system_runtime_notification  # noqa: E402


class SystemRuntimeNotificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime_dir = tempfile.TemporaryDirectory()
        cls.app = Flask(__name__)
        cls.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            LOCAL_RUNTIME_CONFIG_PATH=os.path.join(cls.runtime_dir.name, "runtime_config.json"),
            APP_TIMEZONE="Asia/Shanghai",
            SYSTEM_RUNTIME_NOTIFICATION_ENABLED=True,
            SYSTEM_RUNTIME_NOTIFICATION_WEBHOOK_URL=(
                "https://oapi.dingtalk.com/robot/send?access_token=runtime-token"
            ),
            SYSTEM_RUNTIME_NOTIFICATION_WEBHOOK_PREFIX="[系统运行测试]",
            SYSTEM_RUNTIME_NOTIFICATION_TIME="08:10",
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
        db.session.remove()
        db.drop_all()
        db.create_all()
        runtime_path = self.app.config["LOCAL_RUNTIME_CONFIG_PATH"]
        if os.path.exists(runtime_path):
            os.unlink(runtime_path)
        self.app.config.update(
            SYSTEM_RUNTIME_NOTIFICATION_ENABLED=True,
            SYSTEM_RUNTIME_NOTIFICATION_WEBHOOK_URL=(
                "https://oapi.dingtalk.com/robot/send?access_token=runtime-token"
            ),
            SYSTEM_RUNTIME_NOTIFICATION_WEBHOOK_PREFIX="[系统运行测试]",
            SYSTEM_RUNTIME_NOTIFICATION_TIME="08:10",
        )
        self.target_date = date(2026, 7, 15)

    def _seed_runtime_data(self):
        source = VideoSource(
            name="食堂 NVR",
            source_type="nvr",
            is_active=True,
            status=VideoSourceStatus.enabled.value,
            last_validation_status=VideoSourceValidationStatus.success.value,
        )
        sync_task = TaskLog(
            task_type="video_source_sync",
            task_date=self.target_date,
            status="partial",
        )
        db.session.add_all([source, sync_task])
        db.session.flush()
        db.session.add_all([
            VideoRecordingJob(
                task_log_id=sync_task.id,
                video_source_id=source.id,
                channel_id="1",
                filename="success.mp4",
                video_path="/tmp/success.mp4",
                output_dir="/tmp/images",
                download_url="https://example.invalid/success.mp4",
                status="success",
            ),
            VideoRecordingJob(
                task_log_id=sync_task.id,
                video_source_id=source.id,
                channel_id="2",
                filename="failed.mp4",
                video_path="/tmp/failed.mp4",
                output_dir="/tmp/images",
                download_url="https://example.invalid/failed.mp4",
                status="failed",
            ),
        ])
        captured_at = datetime(2026, 7, 15, 12, 0)
        matched_image = CapturedImage(
            capture_date=self.target_date,
            channel_id="1",
            captured_at=captured_at,
            image_path="/tmp/matched.jpg",
            status=ImageStatusEnum.matched,
            is_candidate=False,
        )
        identified_image = CapturedImage(
            capture_date=self.target_date,
            channel_id="1",
            captured_at=captured_at + timedelta(seconds=1),
            image_path="/tmp/identified.jpg",
            status=ImageStatusEnum.identified,
            is_candidate=False,
        )
        error_image = CapturedImage(
            capture_date=self.target_date,
            channel_id="1",
            captured_at=captured_at + timedelta(seconds=2),
            image_path="/tmp/error.jpg",
            status=ImageStatusEnum.error,
            is_candidate=False,
        )
        candidate_image = CapturedImage(
            capture_date=self.target_date,
            channel_id="1",
            captured_at=captured_at + timedelta(seconds=3),
            image_path="/tmp/candidate.jpg",
            status=ImageStatusEnum.pending,
            is_candidate=True,
        )
        db.session.add_all([matched_image, identified_image, error_image, candidate_image])
        db.session.flush()
        db.session.add_all([
            DishRecognition(
                image_id=matched_image.id,
                dish_name_raw="红烧肉",
                confidence=0.91,
                is_low_confidence=False,
            ),
            DishRecognition(
                image_id=identified_image.id,
                dish_name_raw="青菜",
                confidence=0.42,
                is_low_confidence=True,
            ),
        ])
        consumption = ConsumptionRecord(
            transaction_time=captured_at,
            amount=-12,
            transaction_id="runtime-consumption-1",
        )
        db.session.add(consumption)
        db.session.flush()
        db.session.add_all([
            MatchResult(
                consumption_record_id=consumption.id,
                image_id=matched_image.id,
                status=MatchStatusEnum.matched,
                match_date=self.target_date,
            ),
            MatchResult(
                image_id=identified_image.id,
                status=MatchStatusEnum.time_matched_only,
                match_date=self.target_date,
            ),
        ])
        db.session.commit()

    def test_build_summary_counts_video_analysis_recognition_and_matching(self):
        self._seed_runtime_data()

        summary = build_system_runtime_summary(
            self.target_date,
            now=datetime.fromisoformat("2026-07-16T08:10:00+08:00"),
        )

        self.assertEqual(summary["video"]["total"], 2)
        self.assertEqual(summary["video"]["success"], 1)
        self.assertEqual(summary["video"]["failed"], 1)
        self.assertEqual(summary["images"]["total"], 3)
        self.assertEqual(summary["images"]["candidate"], 1)
        self.assertEqual(summary["images"]["processed"], 3)
        self.assertEqual(summary["images"]["recognized_dishes"], 2)
        self.assertEqual(summary["images"]["low_confidence_dishes"], 1)
        self.assertEqual(summary["matches"]["consumptions"], 1)
        self.assertEqual(summary["matches"]["matched"], 1)
        self.assertEqual(summary["matches"]["pending_confirmation"], 1)
        self.assertEqual(summary["health"]["active_video_sources"], 1)
        self.assertEqual(summary["health"]["overall"], "warning")

        message = format_system_runtime_message(summary, "[系统运行测试]")
        self.assertIn("录像 2 段", message)
        self.assertIn("主图 3 张", message)
        self.assertIn("识别菜品 2 项", message)
        self.assertIn("成功匹配 1 笔", message)

    def test_send_uses_the_independent_webhook(self):
        summary = build_system_runtime_summary(self.target_date)
        with mock.patch(
            "app.services.system_runtime_notifications.DingTalkService.send_robot_webhook",
            return_value={"errcode": 0, "errmsg": "ok"},
        ) as send_webhook:
            send_system_runtime_notification(self.app.config, summary)

        self.assertEqual(
            send_webhook.call_args.kwargs["webhook_url"],
            self.app.config["SYSTEM_RUNTIME_NOTIFICATION_WEBHOOK_URL"],
        )
        payload = send_webhook.call_args.args[0]
        self.assertTrue(payload["text"]["content"].startswith("[系统运行测试]"))

    def test_daily_dispatch_sends_previous_day_once(self):
        self._seed_runtime_data()
        with mock.patch(
            "app.tasks.system_notifications.send_system_runtime_notification"
        ) as send_notification:
            first = dispatch_daily_system_runtime_notification(
                "2026-07-16T08:10:00+08:00"
            )
            second = dispatch_daily_system_runtime_notification(
                "2026-07-16T08:10:30+08:00"
            )

        self.assertTrue(first["sent"])
        self.assertEqual(first["date"], "2026-07-15")
        self.assertEqual(second["reason"], "already_sent")
        send_notification.assert_called_once()

    def test_daily_dispatch_respects_enabled_flag_and_schedule(self):
        self.app.config["SYSTEM_RUNTIME_NOTIFICATION_ENABLED"] = False
        disabled = dispatch_daily_system_runtime_notification(
            "2026-07-16T08:10:00+08:00"
        )
        self.assertEqual(disabled["reason"], "notification_disabled")

        self.app.config["SYSTEM_RUNTIME_NOTIFICATION_ENABLED"] = True
        not_due = dispatch_daily_system_runtime_notification(
            "2026-07-16T08:09:00+08:00"
        )
        self.assertEqual(not_due["reason"], "not_due")

    def test_daily_dispatch_reads_runtime_schedule_without_restart(self):
        from app.services.runtime_config import persist_runtime_overrides

        persist_runtime_overrides(self.app.config, {
            "SYSTEM_RUNTIME_NOTIFICATION_TIME": "09:15",
        })
        old_schedule = dispatch_daily_system_runtime_notification(
            "2026-07-16T08:10:00+08:00"
        )
        with mock.patch(
            "app.tasks.system_notifications.send_system_runtime_notification"
        ) as send_notification:
            configured_schedule = dispatch_daily_system_runtime_notification(
                "2026-07-16T09:15:00+08:00"
            )

        self.assertEqual(old_schedule["reason"], "not_due")
        self.assertTrue(configured_schedule["sent"])
        send_notification.assert_called_once()


if __name__ == "__main__":
    unittest.main()
