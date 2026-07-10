import os
import sys
import tempfile
import threading
import types
import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

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
from app.models import CapturedImage, CategoryEnum, DailyMenu, Dish, TaskLog, VideoSource  # noqa: E402
from app.services.video_sources.manager import VideoSourceManager  # noqa: E402
from app.tasks.video import (  # noqa: E402
    _build_recording_filename,
    _cleanup_expired_video_recordings,
    sync_video_source_media,
)


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


class _OrderingVideoSource(_FakeVideoSource):
    def __init__(self, events, before_download=None):
        self.events = events
        self.before_download = before_download
        self.download_count = 0

    def download_recording(self, download_url, save_path, resume_offset=0):
        self.download_count += 1
        if self.before_download is not None:
            self.before_download(self.download_count)
        self.events.append(("download", os.path.basename(save_path)))
        return super().download_recording(download_url, save_path, resume_offset)


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
            VIDEO_EXTRACT_USE_SUBPROCESS=False,
            MEAL_SLOTS=[
                {"key": "breakfast", "label": "早餐", "start": "07:00", "end": "09:00"},
                {"key": "lunch", "label": "午餐", "start": "11:30", "end": "13:00"},
                {"key": "dinner", "label": "晚餐", "start": "17:30", "end": "19:00"},
            ],
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
        db.session.query(DailyMenu).delete()
        db.session.query(Dish).delete()
        db.session.query(VideoSource).delete()
        db.session.commit()

    def tearDown(self):
        db.session.rollback()
        db.session.expunge_all()

    def test_build_recording_filename_uses_formatted_local_time(self):
        filename = _build_recording_filename(
            "nvr",
            "8",
            datetime(2026, 4, 3, 3, 30, tzinfo=ZoneInfo("UTC")),
            {"VIDEO_TIMEZONE": "Asia/Shanghai"},
        )

        self.assertEqual(filename, "nvr_ch8_2026-04-03_11-30-00.mp4")

    def test_cleanup_expired_video_recordings_deletes_old_date_dirs(self):
        cfg = {"VIDEO_TIMEZONE": "Asia/Shanghai"}
        with tempfile.TemporaryDirectory() as tmpdir:
            for dirname in ("2026-05-10", "2026-05-11", "2026-05-12", "2026-05-14"):
                os.makedirs(os.path.join(tmpdir, dirname), exist_ok=True)
                with open(os.path.join(tmpdir, dirname, "recording.mp4"), "wb") as handle:
                    handle.write(b"video")
            old_file = os.path.join(tmpdir, "old-orphan.mp4")
            with open(old_file, "wb") as handle:
                handle.write(b"video")
            old_mtime = datetime(2026, 5, 10, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
            os.utime(old_file, (old_mtime, old_mtime))

            summary = _cleanup_expired_video_recordings(
                tmpdir,
                3,
                cfg,
                now=datetime(2026, 5, 14, 21, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
                keep_dates={date(2026, 5, 10)},
            )

            self.assertTrue(os.path.isdir(os.path.join(tmpdir, "2026-05-10")))
            self.assertFalse(os.path.exists(os.path.join(tmpdir, "2026-05-11")))
            self.assertTrue(os.path.isdir(os.path.join(tmpdir, "2026-05-12")))
            self.assertTrue(os.path.isdir(os.path.join(tmpdir, "2026-05-14")))
            self.assertFalse(os.path.exists(old_file))
            self.assertEqual(summary["cutoff_date"], "2026-05-12")
            self.assertIn("2026-05-11", summary["deleted_dirs"])
            self.assertIn("old-orphan.mp4", summary["deleted_files"])

    def _create_menu(self, menu_date):
        dish = Dish(name="红烧肉", price=12.0, category=CategoryEnum.meat, is_active=True)
        db.session.add(dish)
        db.session.flush()
        db.session.add(DailyMenu(
            menu_date=menu_date,
            meal_dish_ids={
                "breakfast": [],
                "lunch": [dish.id],
                "dinner": [],
                "late_night": [],
            },
            is_default=False,
        ))
        db.session.commit()

    def test_sync_video_source_task_records_recordings_in_task_meta(self):
        self._create_menu(date(2026, 4, 3))
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

            def extract_frames(self, video_path, output_dir, video_start_time, channel_id, progress_callback=None):
                if progress_callback is not None:
                    progress_callback({
                        "frame_no": 50,
                        "total_frames": 100,
                        "progress_percent": 50.0,
                        "extracted_count": 1,
                        "frame_step": 2,
                        "effective_scan_fps": 15.0,
                    })
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

        fake_recognition.enqueue_recognition_images = lambda image_ids, **kwargs: types.SimpleNamespace(
            id=900,
            total_count=len(image_ids),
        )
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
        self.assertTrue(all(item["progress_percent"] == 100.0 for item in task.meta["recordings"]))
        self.assertTrue(all(item["effective_scan_fps"] == 15.0 for item in task.meta["recordings"]))
        self.assertEqual(len(task.meta["image_ids"]), 6)

    def test_sync_video_source_uses_ffmpeg_fallback_after_analyzer_failure(self):
        self._create_menu(date(2026, 4, 3))
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

            def extract_frames(self, video_path, output_dir, video_start_time, channel_id, progress_callback=None):
                raise RuntimeError("extract stuck")

        fake_video_analyzer.VideoAnalyzer = FakeVideoAnalyzer
        original_video_analyzer = sys.modules.get("app.services.video_analyzer")
        sys.modules["app.services.video_analyzer"] = fake_video_analyzer

        recognition_calls = []
        fake_recognition = types.ModuleType("app.tasks.recognition")

        def enqueue_recognition_images(image_ids, **kwargs):
            recognition_calls.append((list(image_ids), kwargs))
            return types.SimpleNamespace(id=900 + len(recognition_calls), total_count=len(image_ids))

        fake_recognition.enqueue_recognition_images = enqueue_recognition_images
        original_recognition = sys.modules.get("app.tasks.recognition")
        sys.modules["app.tasks.recognition"] = fake_recognition

        try:
            from unittest import mock

            def fallback_extract(cfg, video_path, output_dir, video_start, channel_id, progress_callback=None, cancel_event=None):
                return [{
                    "channel_id": channel_id,
                    "captured_at": video_start,
                    "image_path": os.path.join(output_dir, "fallback.jpg"),
                    "is_candidate": False,
                    "extraction_strategy": "ffmpeg_interval_fallback",
                }]

            with (
                mock.patch("app.tasks.video._make_video_source", return_value=_FakeVideoSource()),
                mock.patch("app.tasks.video._repair_video_for_extract", side_effect=RuntimeError("repair failed")),
                mock.patch("app.tasks.video._extract_frames_with_ffmpeg_fallback", side_effect=fallback_extract),
            ):
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
        self.assertEqual(task.total_count, 3)
        self.assertEqual(task.success_count, 3)
        self.assertEqual(task.error_count, 0)
        self.assertEqual(task.meta["recording_count"], 3)
        self.assertTrue(all(item["download_status"] == "success" for item in task.meta["recordings"]))
        self.assertTrue(all(item["fallback_used"] for item in task.meta["recordings"]))
        self.assertEqual(len(recognition_calls), 3)
        self.assertTrue(all(len(image_ids) == 1 for image_ids, _kwargs in recognition_calls))
        self.assertTrue(all(kwargs["target_date"] == date(2026, 4, 3) for _image_ids, kwargs in recognition_calls))

    def test_sync_video_source_starts_extracting_before_all_recordings_are_downloaded(self):
        self._create_menu(date(2026, 4, 3))
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

        events = []
        first_extract_started = threading.Event()
        pipeline_failures = []

        def before_download(download_count):
            if download_count == 2 and not first_extract_started.wait(timeout=2.0):
                pipeline_failures.append("second download started before first extraction")

        fake_video_analyzer = types.ModuleType("app.services.video_analyzer")

        class FakeVideoAnalyzer:
            def __init__(self, config):
                self.config = config

            def extract_frames(self, video_path, output_dir, video_start_time, channel_id, progress_callback=None):
                events.append(("extract", os.path.basename(video_path)))
                first_extract_started.set()
                return [{
                    "channel_id": channel_id,
                    "captured_at": video_start_time,
                    "image_path": os.path.join(output_dir, "frame-1.jpg"),
                    "is_candidate": False,
                }]

        fake_video_analyzer.VideoAnalyzer = FakeVideoAnalyzer
        original_video_analyzer = sys.modules.get("app.services.video_analyzer")
        sys.modules["app.services.video_analyzer"] = fake_video_analyzer

        fake_recognition = types.ModuleType("app.tasks.recognition")

        fake_recognition.enqueue_recognition_images = lambda image_ids, **kwargs: types.SimpleNamespace(
            id=900,
            total_count=len(image_ids),
        )
        original_recognition = sys.modules.get("app.tasks.recognition")
        sys.modules["app.tasks.recognition"] = fake_recognition

        try:
            from unittest import mock

            with mock.patch("app.tasks.video._make_video_source", return_value=_OrderingVideoSource(events, before_download)):
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

        self.assertEqual(len(events), 6)
        self.assertEqual(pipeline_failures, [])
        event_types = [event[0] for event in events]
        self.assertEqual(event_types.count("download"), 3)
        self.assertEqual(event_types.count("extract"), 3)
        self.assertLess(
            event_types.index("extract"),
            max(index for index, event_type in enumerate(event_types) if event_type == "download"),
        )


if __name__ == "__main__":
    unittest.main()
