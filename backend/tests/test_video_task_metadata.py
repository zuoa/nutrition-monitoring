import os
import sys
import types
import unittest
from datetime import timedelta

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
from app.models import CapturedImage, TaskLog, VideoSource  # noqa: E402
from app.services.video_sources.manager import VideoSourceManager  # noqa: E402
from app.tasks.video import sync_video_source_media  # noqa: E402


class _FakeVideoSource:
    def list_recordings(self, channel_id, start, end):
        return [{
            "filename": f"{channel_id}_{int(start.timestamp())}.mp4",
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "download_url": "http://example.com/video.mp4",
            "size": 128,
        }]

    def download_recording(self, download_url, save_path, resume_offset=0):
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, "wb") as handle:
            handle.write(b"fake-video")
        return True


class VideoTaskMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask(__name__)
        cls.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SECRET_KEY="test-secret",
            JWT_ALGORITHM="HS256",
            JWT_ACCESS_TOKEN_EXPIRES=timedelta(hours=1),
            IMAGE_STORAGE_PATH="/tmp/nutrition-monitoring-test-images",
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
        db.session.query(CapturedImage).delete()
        db.session.query(TaskLog).delete()
        db.session.query(VideoSource).delete()
        db.session.commit()

    def tearDown(self):
        db.session.rollback()

    def test_sync_video_source_task_records_recordings_in_task_meta(self):
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
                "download_trigger_time": "21:30",
                "local_storage_path": "/tmp/nutrition-monitoring-test-videos",
                "retention_days": 3,
            },
        })

        fake_video_analyzer = types.ModuleType("app.services.video_analyzer")

        class FakeVideoAnalyzer:
            def __init__(self, config):
                self.config = config

            def extract_frames(self, video_path, output_dir, video_start_time, channel_id):
                return [
                    {
                        "channel_id": channel_id,
                        "captured_at": video_start_time,
                        "image_path": os.path.join(output_dir, "frame-1.jpg"),
                        "is_candidate": False,
                    },
                    {
                        "channel_id": channel_id,
                        "captured_at": video_start_time,
                        "image_path": os.path.join(output_dir, "frame-2.jpg"),
                        "is_candidate": True,
                    },
                ]

        fake_video_analyzer.VideoAnalyzer = FakeVideoAnalyzer
        original_video_analyzer = sys.modules.get("app.services.video_analyzer")
        sys.modules["app.services.video_analyzer"] = fake_video_analyzer

        fake_recognition = types.ModuleType("app.tasks.recognition")

        class _RunRecognitionBatch:
            def delay(self, *args, **kwargs):
                return None

        fake_recognition.run_recognition_batch = _RunRecognitionBatch()
        original_recognition = sys.modules.get("app.tasks.recognition")
        sys.modules["app.tasks.recognition"] = fake_recognition

        try:
            from unittest import mock

            with mock.patch("app.tasks.video._make_video_source", return_value=_FakeVideoSource()):
                sync_video_source_media.run(
                    types.SimpleNamespace(retry=lambda *args, **kwargs: None),
                    "2026-04-03",
                )
        finally:
            if original_video_analyzer is None:
                sys.modules.pop("app.services.video_analyzer", None)
            else:
                sys.modules["app.services.video_analyzer"] = original_video_analyzer

            if original_recognition is None:
                sys.modules.pop("app.tasks.recognition", None)
            else:
                sys.modules["app.tasks.recognition"] = original_recognition

        task = TaskLog.query.filter_by(task_type="video_source_sync").one()
        self.assertEqual(task.status, "success")
        self.assertEqual(task.total_count, 6)
        self.assertEqual(task.success_count, 6)
        self.assertEqual(task.meta["recording_count"], 3)
        self.assertEqual(task.meta["primary_count"], 3)
        self.assertEqual(task.meta["candidate_count"], 3)
        self.assertEqual(len(task.meta["recordings"]), 3)
        self.assertTrue(all(item["channel_id"] == "8" for item in task.meta["recordings"]))
        self.assertTrue(all(item["download_status"] == "success" for item in task.meta["recordings"]))
        self.assertTrue(all(item["frame_count"] == 2 for item in task.meta["recordings"]))
        self.assertTrue(all(len(item["image_ids"]) == 2 for item in task.meta["recordings"]))
        self.assertEqual(len(task.meta["image_ids"]), 6)


if __name__ == "__main__":
    unittest.main()
