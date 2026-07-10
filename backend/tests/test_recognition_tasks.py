import os
import sys
import types
import unittest
from datetime import date, datetime, timedelta, timezone
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
            self.apply_async = lambda *args, **kwargs: None

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
    schedules_module.crontab = lambda *args, **kwargs: {"args": args, "kwargs": kwargs}
    sys.modules["celery"] = celery_module
    sys.modules["celery.schedules"] = schedules_module

if "billiard.exceptions" not in sys.modules:
    billiard = types.ModuleType("billiard")
    billiard_exceptions = types.ModuleType("billiard.exceptions")

    class _SoftTimeLimitExceeded(Exception):
        pass

    billiard_exceptions.SoftTimeLimitExceeded = _SoftTimeLimitExceeded
    billiard.exceptions = billiard_exceptions
    sys.modules["billiard"] = billiard
    sys.modules["billiard.exceptions"] = billiard_exceptions

from app import db  # noqa: E402
import app.models  # noqa: F401,E402
from app.models import (  # noqa: E402
    CapturedImage,
    CategoryEnum,
    Dish,
    DishRecognition,
    ImageStatusEnum,
    TaskLog,
)
from app.tasks import recognition as recognition_tasks  # noqa: E402


class RecognitionTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask(__name__)
        cls.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            DISH_RECOGNITION_MODE="local_embedding",
            RECOGNITION_MENU_SCOPE="all",
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
        db.session.query(DishRecognition).delete()
        db.session.query(CapturedImage).delete()
        db.session.query(TaskLog).delete()
        db.session.query(Dish).delete()
        db.session.commit()

    def _create_image(self) -> CapturedImage:
        image = CapturedImage(
            capture_date=date(2026, 7, 10),
            channel_id="1",
            captured_at=datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
            image_path="/tmp/no-plate.jpg",
            status=ImageStatusEnum.pending,
            is_candidate=False,
        )
        db.session.add(image)
        db.session.commit()
        return image

    def test_enqueue_recognition_images_creates_independent_image_jobs(self):
        images = [self._create_image(), self._create_image()]

        with mock.patch.object(recognition_tasks.recognize_single_image, "apply_async", create=True) as apply_async:
            task_log = recognition_tasks.enqueue_recognition_images(
                [image.id for image in images],
                target_date=date(2026, 7, 10),
            )

        self.assertIsNotNone(task_log)
        self.assertEqual(task_log.total_count, 2)
        self.assertEqual(task_log.meta["dispatch_mode"], "per_image")
        self.assertEqual(apply_async.call_count, 2)
        refreshed = CapturedImage.query.order_by(CapturedImage.id.asc()).all()
        self.assertTrue(all(image.status == ImageStatusEnum.queued for image in refreshed))
        self.assertTrue(all(image.recognition_task_log_id == task_log.id for image in refreshed))
        self.assertTrue(all(image.recognition_task_id for image in refreshed))
        self.assertTrue(all(image.recognition_lease_expires_at is not None for image in refreshed))

    def test_enqueue_recognition_images_atomically_claims_pending_images(self):
        image = self._create_image()

        with mock.patch.object(recognition_tasks.recognize_single_image, "apply_async", create=True) as apply_async:
            first_task_log = recognition_tasks.enqueue_recognition_images(
                [image.id],
                target_date=image.capture_date,
            )
            second_task_log = recognition_tasks.enqueue_recognition_images(
                [image.id],
                target_date=image.capture_date,
            )

        self.assertIsNotNone(first_task_log)
        self.assertIsNone(second_task_log)
        self.assertEqual(TaskLog.query.filter_by(task_type="ai_recognition").count(), 1)
        apply_async.assert_called_once()

    def test_unpublished_queued_image_is_recovered_after_dispatcher_crash(self):
        image = self._create_image()

        with mock.patch.object(
            recognition_tasks.recognize_single_image,
            "apply_async",
            side_effect=SystemExit("simulated dispatcher crash"),
            create=True,
        ):
            with self.assertRaises(SystemExit):
                recognition_tasks.enqueue_recognition_images(
                    [image.id],
                    target_date=image.capture_date,
                )

        db.session.expire_all()
        image = CapturedImage.query.get(image.id)
        self.assertEqual(image.status, ImageStatusEnum.queued)
        self.assertIsNone(image.recognition_task_id)
        self.assertIsNotNone(image.recognition_lease_expires_at)

        image.recognition_lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.session.commit()
        with mock.patch.object(recognition_tasks.recognize_single_image, "apply_async", create=True) as apply_async:
            result = recognition_tasks.requeue_stale_recognition_images.run()

        db.session.expire_all()
        image = CapturedImage.query.get(image.id)
        self.assertEqual(result["requeued"], 1)
        self.assertEqual(image.status, ImageStatusEnum.queued)
        self.assertIsNotNone(image.recognition_task_id)
        self.assertIsNotNone(image.recognition_lease_expires_at)
        apply_async.assert_called_once()

    def test_no_plate_result_marks_image_invalid_and_completes_batch(self):
        Dish.query.delete()
        db.session.add(Dish(name="红烧肉", price=12, category=CategoryEnum.meat, is_active=True))
        image = self._create_image()
        task_log = TaskLog(
            task_type="ai_recognition",
            task_date=image.capture_date,
            total_count=1,
            status="running",
        )
        db.session.add(task_log)
        db.session.flush()
        image.status = ImageStatusEnum.queued
        image.recognition_task_log_id = task_log.id
        db.session.commit()

        recognizer = mock.Mock()
        recognizer.recognize_dishes.return_value = {
            "valid_image": False,
            "invalid_reason": "no_plate_detected",
            "notes": "图片中未检测到餐盘或有效菜区",
            "dishes": [],
        }
        fake_task = types.SimpleNamespace(
            request=types.SimpleNamespace(id="recognition-task-1", retries=0),
            max_retries=3,
            retry=mock.Mock(),
        )

        with mock.patch(
            "app.services.dish_recognition.DishRecognitionService",
            return_value=recognizer,
        ):
            result = recognition_tasks.recognize_single_image.run(fake_task, image.id, task_log.id)

        db.session.expire_all()
        image = CapturedImage.query.get(image.id)
        task_log = TaskLog.query.get(task_log.id)
        self.assertEqual(result["status"], ImageStatusEnum.invalid.value)
        self.assertEqual(image.status, ImageStatusEnum.invalid)
        self.assertEqual(image.recognition_error_code, "no_plate_detected")
        self.assertEqual(task_log.status, "success")
        self.assertEqual(task_log.success_count, 1)
        self.assertEqual(task_log.invalid_count, 1)

    def test_stale_processing_image_is_requeued(self):
        image = self._create_image()
        image.status = ImageStatusEnum.processing
        image.recognition_lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.session.commit()

        with mock.patch.object(recognition_tasks.recognize_single_image, "apply_async", create=True) as apply_async:
            result = recognition_tasks.requeue_stale_recognition_images.run()

        self.assertEqual(result["requeued"], 1)
        self.assertEqual(CapturedImage.query.get(image.id).status, ImageStatusEnum.queued)
        apply_async.assert_called_once()


if __name__ == "__main__":
    unittest.main()
