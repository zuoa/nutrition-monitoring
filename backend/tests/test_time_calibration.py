import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
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
    TimeCalibrationSample,
)
from app.services.time_calibration import TimeOffsetResolver  # noqa: E402
from app.tasks.matching import _match_record  # noqa: E402

SH = ZoneInfo("Asia/Shanghai")


def _sample(source_time, offset, *, created_at=None, sample_id=None):
    return SimpleNamespace(
        source_time=source_time,
        offset_seconds=offset,
        created_at=created_at,
        id=sample_id,
    )


class TimeOffsetResolverLogicTests(unittest.TestCase):
    """Pure in-memory lookup behavior (no database)."""

    def test_exact_minute_sample_wins(self):
        resolver = TimeOffsetResolver([
            _sample(datetime(2026, 7, 23, 12, 0, 5), 2.0),
            _sample(datetime(2026, 7, 23, 12, 1, 5), 9.0),
        ])
        moment = datetime(2026, 7, 23, 12, 0, 42)
        self.assertEqual(resolver.offset_for(moment), -2.0)

    def test_nearest_sample_used_when_minute_missing(self):
        resolver = TimeOffsetResolver([
            _sample(datetime(2026, 7, 23, 11, 58, 10), 1.0),
            _sample(datetime(2026, 7, 23, 12, 5, 10), 3.0),
        ])
        moment = datetime(2026, 7, 23, 12, 0, 0)
        # 11:58:10 is 110s away, 12:05:10 is 310s away -> nearest is 1.0.
        self.assertEqual(resolver.offset_for(moment), -1.0)

    def test_nearest_prefers_future_sample_when_closer(self):
        resolver = TimeOffsetResolver([
            _sample(datetime(2026, 7, 23, 11, 50, 0), 1.0),
            _sample(datetime(2026, 7, 23, 12, 0, 30), 3.0),
        ])
        moment = datetime(2026, 7, 23, 12, 0, 0)
        self.assertEqual(resolver.offset_for(moment), -3.0)

    def test_latest_sample_wins_within_same_minute(self):
        older = _sample(
            datetime(2026, 7, 23, 12, 0, 5), 2.0,
            created_at=datetime(2026, 7, 23, 12, 0, 6, tzinfo=timezone.utc), sample_id=1,
        )
        newer = _sample(
            datetime(2026, 7, 23, 12, 0, 40), 2.7,
            created_at=datetime(2026, 7, 23, 12, 0, 41, tzinfo=timezone.utc), sample_id=2,
        )
        resolver = TimeOffsetResolver([newer, older])
        self.assertEqual(resolver.offset_for(datetime(2026, 7, 23, 12, 0, 15)), -2.7)

    def test_manual_fallback_when_no_samples(self):
        resolver = TimeOffsetResolver([], fallback_offset=-1.5)
        self.assertEqual(resolver.offset_for(datetime(2026, 7, 23, 12, 0, 0)), -1.5)
        self.assertFalse(resolver.has_samples)

    def test_aware_moment_converted_to_source_timezone(self):
        resolver = TimeOffsetResolver([
            _sample(datetime(2026, 7, 23, 12, 0, 5), 2.0),
        ])
        # 2026-07-23 04:00:42 UTC == 12:00:42 Asia/Shanghai -> same-minute hit.
        moment = datetime(2026, 7, 23, 4, 0, 42, tzinfo=timezone.utc)
        self.assertEqual(resolver.offset_for(moment), -2.0)


class TimeOffsetResolverDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask(__name__)
        cls.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            APP_TIMEZONE="Asia/Shanghai",
            VIDEO_TIMEZONE="Asia/Shanghai",
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
        db.session.query(TimeCalibrationSample).delete()
        db.session.query(MatchResult).delete()
        db.session.query(DishRecognition).delete()
        db.session.query(CapturedImage).delete()
        db.session.query(Dish).delete()
        db.session.query(ConsumptionRecord).delete()
        db.session.commit()

    def tearDown(self):
        db.session.rollback()

    def _persist_sample(self, source_time: datetime, offset: float) -> TimeCalibrationSample:
        sample = TimeCalibrationSample(
            source_system="ztk_plus",
            source_time=source_time,
            local_time=source_time - timedelta(seconds=offset),
            offset_seconds=offset,
            rtt_ms=1.0,
        )
        db.session.add(sample)
        db.session.commit()
        return sample

    def test_for_time_range_loads_window_and_edge_neighbors(self):
        self._persist_sample(datetime(2026, 7, 23, 11, 59, 10), 1.0)
        self._persist_sample(datetime(2026, 7, 23, 12, 0, 10), 2.0)
        self._persist_sample(datetime(2026, 7, 23, 12, 1, 10), 3.0)
        self._persist_sample(datetime(2026, 7, 23, 12, 2, 10), 4.0)

        resolver = TimeOffsetResolver.for_time_range(
            datetime(2026, 7, 23, 12, 0, 0),
            datetime(2026, 7, 23, 12, 1, 0),
            fallback_offset=99.0,
        )

        # Inside window: exact-minute bucket.
        self.assertEqual(resolver.offset_for(datetime(2026, 7, 23, 12, 0, 30)), -2.0)
        # Just outside each edge the nearest neighbor sample is reachable.
        self.assertEqual(resolver.offset_for(datetime(2026, 7, 23, 11, 59, 30)), -1.0)
        self.assertEqual(resolver.offset_for(datetime(2026, 7, 23, 12, 1, 30)), -3.0)
        # Further out the nearest loaded sample is used.
        self.assertEqual(resolver.offset_for(datetime(2026, 7, 23, 12, 2, 30)), -3.0)

    def test_for_time_range_empty_table_uses_fallback(self):
        resolver = TimeOffsetResolver.for_time_range(
            datetime(2026, 7, 23, 12, 0, 0),
            datetime(2026, 7, 23, 13, 0, 0),
            fallback_offset=-2.5,
        )
        self.assertEqual(resolver.offset_for(datetime(2026, 7, 23, 12, 30, 0)), -2.5)

    def _image_with_price(self, channel_id: str, price: float, captured_at: datetime) -> CapturedImage:
        dish = Dish(
            name=f"菜品-{channel_id}-{price}",
            price=price,
            category=CategoryEnum.other,
            is_active=True,
        )
        image = CapturedImage(
            capture_date=captured_at.date(),
            channel_id=channel_id,
            captured_at=captured_at,
            image_path=f"/tmp/{channel_id}-{price}.jpg",
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

    def test_match_record_uses_same_minute_calibration(self):
        # The source clock runs 2s ahead. A 12:00:02 source transaction maps
        # to 12:00:00 on the local/video clock after applying -2s.
        tx_time = datetime(2026, 7, 23, 12, 0, 2, tzinfo=SH)
        self._persist_sample(datetime(2026, 7, 23, 12, 0, 5), 2.0)
        self._image_with_price("1-3", 8.0, datetime(2026, 7, 23, 12, 0, 0, tzinfo=SH))
        record = ConsumptionRecord(
            student_no="230501",
            transaction_time=tx_time,
            amount=-8.0,
            transaction_id="tx-cal-001",
            channel_id="1-3",
        )
        db.session.add(record)
        db.session.commit()

        resolver = TimeOffsetResolver.for_time_range(tx_time, tx_time, fallback_offset=0.0)
        _match_record(record, 0.5, tx_time.date(), offset_resolver=resolver)

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.status, MatchStatusEnum.matched)
        self.assertAlmostEqual(match.time_diff_seconds, 0.0, places=3)

    def test_match_record_falls_back_to_nearest_sample(self):
        tx_time = datetime(2026, 7, 23, 12, 0, 2, tzinfo=SH)
        # No sample at 12:00; the 11:50 sample is the nearest one available.
        self._persist_sample(datetime(2026, 7, 23, 11, 50, 5), 2.0)
        self._image_with_price("1-3", 8.0, datetime(2026, 7, 23, 12, 0, 0, tzinfo=SH))
        record = ConsumptionRecord(
            student_no="230501",
            transaction_time=tx_time,
            amount=-8.0,
            transaction_id="tx-cal-002",
            channel_id="1-3",
        )
        db.session.add(record)
        db.session.commit()

        resolver = TimeOffsetResolver.for_time_range(tx_time, tx_time, fallback_offset=0.0)
        _match_record(record, 0.5, tx_time.date(), offset_resolver=resolver)

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.status, MatchStatusEnum.matched)

    def test_match_record_without_samples_uses_manual_offset(self):
        tx_time = datetime(2026, 7, 23, 12, 0, 0, tzinfo=SH)
        self._image_with_price("1-3", 8.0, datetime(2026, 7, 23, 12, 0, 2, tzinfo=SH))
        record = ConsumptionRecord(
            student_no="230501",
            transaction_time=tx_time,
            amount=-8.0,
            transaction_id="tx-cal-003",
            channel_id="1-3",
        )
        db.session.add(record)
        db.session.commit()

        resolver = TimeOffsetResolver.for_time_range(tx_time, tx_time, fallback_offset=2.0)
        _match_record(record, 0.5, tx_time.date(), offset_resolver=resolver)

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.status, MatchStatusEnum.matched)


if __name__ == "__main__":
    unittest.main()
