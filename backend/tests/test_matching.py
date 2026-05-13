import os
import sys
import types
import unittest
from datetime import datetime, timezone

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

    celery_module.Celery = _FakeCelery
    schedules_module.crontab = lambda *args, **kwargs: {"args": args, "kwargs": kwargs}
    sys.modules["celery"] = celery_module
    sys.modules["celery.schedules"] = schedules_module

from app import db  # noqa: E402
import app.models  # noqa: F401,E402
from app.models import (  # noqa: E402
    CapturedImage,
    CategoryEnum,
    ConsumptionRecord,
    Dish,
    DishRecognition,
    ImageStatusEnum,
    MatchResult,
    MatchStatusEnum,
)
from app.tasks.matching import _match_record, run_matching_for_date  # noqa: E402


class MatchingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask(__name__)
        cls.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
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
        db.session.query(MatchResult).delete()
        db.session.query(DishRecognition).delete()
        db.session.query(CapturedImage).delete()
        db.session.query(Dish).delete()
        db.session.query(ConsumptionRecord).delete()
        db.session.commit()

    def tearDown(self):
        db.session.rollback()

    def _image_with_price(self, channel_id: str, price: float, captured_at: datetime) -> CapturedImage:
        dish = Dish(
            name=f"菜品{channel_id}",
            price=price,
            category=CategoryEnum.other,
            is_active=True,
        )
        image = CapturedImage(
            capture_date=captured_at.date(),
            channel_id=channel_id,
            captured_at=captured_at,
            image_path=f"/tmp/{channel_id}.jpg",
            status=ImageStatusEnum.identified,
            is_candidate=False,
        )
        db.session.add_all([dish, image])
        db.session.flush()
        db.session.add(DishRecognition(
            image_id=image.id,
            dish_id=dish.id,
            dish_name_raw=dish.name,
            confidence=0.95,
            is_low_confidence=False,
            model_version="test",
        ))
        db.session.flush()
        return image

    def test_match_record_filters_candidates_by_consumption_channel(self):
        tx_time = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        record = ConsumptionRecord(
            student_no="230501",
            transaction_time=tx_time,
            amount=8.0,
            transaction_id="tx-channel-001",
            channel_id="1",
        )
        db.session.add(record)
        db.session.flush()
        image_same_channel = self._image_with_price("1", 10.0, tx_time)
        image_other_channel = self._image_with_price("2", 8.0, tx_time)
        db.session.commit()

        _match_record(record, tolerance_s=5, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.image_id, image_same_channel.id)
        self.assertNotEqual(match.image_id, image_other_channel.id)
        self.assertEqual(match.status, MatchStatusEnum.time_matched_only)
        self.assertEqual(match.price_diff, 2.0)

    def test_match_record_allows_pending_image_without_recognition(self):
        tx_time = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        record = ConsumptionRecord(
            student_no="230501",
            transaction_time=tx_time,
            amount=8.0,
            transaction_id="tx-pending-001",
            channel_id="1",
        )
        image = CapturedImage(
            capture_date=tx_time.date(),
            channel_id="1",
            captured_at=tx_time,
            image_path="/tmp/pending.jpg",
            status=ImageStatusEnum.pending,
            is_candidate=False,
        )
        db.session.add_all([record, image])
        db.session.commit()

        _match_record(record, tolerance_s=5, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.image_id, image.id)
        self.assertEqual(match.status, MatchStatusEnum.time_matched_only)
        self.assertEqual(match.price_diff, 8.0)

    def test_run_matching_marks_pending_images_as_unmatched(self):
        tx_time = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        image = CapturedImage(
            capture_date=tx_time.date(),
            channel_id="1",
            captured_at=tx_time,
            image_path="/tmp/pending-unmatched.jpg",
            status=ImageStatusEnum.pending,
            is_candidate=False,
        )
        db.session.add(image)
        db.session.commit()

        run_matching_for_date("2026-03-31")

        match = MatchResult.query.filter_by(image_id=image.id).one()
        self.assertEqual(match.status, MatchStatusEnum.unmatched_image)
        self.assertEqual(match.match_date, tx_time.date())

    def test_match_record_clears_existing_match_when_channel_has_no_candidate(self):
        tx_time = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        record = ConsumptionRecord(
            student_no="230501",
            transaction_time=tx_time,
            amount=8.0,
            transaction_id="tx-channel-002",
            channel_id="9",
        )
        db.session.add(record)
        db.session.flush()
        image = self._image_with_price("1", 8.0, tx_time)
        db.session.add(MatchResult(
            consumption_record_id=record.id,
            image_id=image.id,
            status=MatchStatusEnum.matched,
            match_date=tx_time.date(),
            time_diff_seconds=0,
            price_diff=0,
        ))
        db.session.commit()

        _match_record(record, tolerance_s=5, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertIsNone(match.image_id)
        self.assertEqual(match.status, MatchStatusEnum.unmatched_record)
        self.assertIsNone(match.time_diff_seconds)
        self.assertIsNone(match.price_diff)


if __name__ == "__main__":
    unittest.main()
