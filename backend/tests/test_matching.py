import os
import sys
import types
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone

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
    MatchingRun,
    MatchingCandidate,
    MatchStatusEnum,
    VideoSource,
)
from app.services.consumption_location_filter import ENABLED_TRANSACTION_LOCATION_IDS_KEY  # noqa: E402
from app.services.match_windows import (  # noqa: E402
    DEFAULT_MATCH_WINDOW_STAGES,
    matching_windows,
    normalize_match_window_stages,
)
from app.tasks.matching import (  # noqa: E402
    _match_record,
    match_single_image,
    run_matching_for_batch,
    run_matching_for_date,
)


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
        db.session.remove()
        self._dish_seq = 0
        self.app.config["TIME_MATCH_WINDOW_STAGES"] = [1, 3, 5]
        self.app.config["TIME_OFFSET_CALIBRATION"] = 0.0
        self.app.config["LOCAL_RUNTIME_CONFIG_PATH"] = "/tmp/nutrition-matching-tests-no-overrides.json"
        db.session.query(MatchingCandidate).delete()
        db.session.query(MatchingRun).delete()
        self.app.config[ENABLED_TRANSACTION_LOCATION_IDS_KEY] = []
        self.app.config["MATCHING_BATCH_CHUNK_SIZE"] = 200
        self.app.config["MATCHING_BATCH_TIME_BUDGET_SECONDS"] = 240
        db.session.query(MatchResult).delete()
        db.session.query(DishRecognition).delete()
        db.session.query(CapturedImage).delete()
        db.session.query(VideoSource).delete()
        db.session.query(Dish).delete()
        db.session.query(ConsumptionRecord).delete()
        db.session.commit()

    def tearDown(self):
        db.session.rollback()

    def _drain_matching(self):
        from app.services.date_matching import advance_date_matching
        for _ in range(300):
            days = [run.match_date for run in MatchingRun.query.all() if run.requested > run.completed or run.phase != "idle"]
            if not days:
                return
            for day in days:
                advance_date_matching(day)
        self.fail("Matching queue did not drain")

    def _image_with_price(self, channel_id: str, price: float, captured_at: datetime) -> CapturedImage:
        self._dish_seq += 1
        dish = Dish(
            name=f"菜品{channel_id}-{self._dish_seq}",
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
            amount=-8.0,
            transaction_id="tx-channel-001",
            channel_id="1",
        )
        db.session.add(record)
        db.session.flush()
        image_same_channel = self._image_with_price("1", 8.0, tx_time)
        image_other_channel = self._image_with_price("2", 8.0, tx_time)
        db.session.commit()

        _match_record(record, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.image_id, image_same_channel.id)
        self.assertEqual(match.captured_at, image_same_channel.captured_at)
        self.assertEqual(match.to_dict()["captured_at"], image_same_channel.captured_at.isoformat())
        self.assertNotEqual(match.image_id, image_other_channel.id)
        self.assertEqual(match.status, MatchStatusEnum.matched)
        self.assertEqual(match.price_diff, 0.0)

    def test_date_matching_excludes_standby_frames_from_unmatched_images(self):
        captured_at = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        primary = CapturedImage(
            capture_date=captured_at.date(),
            channel_id="1",
            captured_at=captured_at,
            image_path="/tmp/primary.jpg",
            status=ImageStatusEnum.pending,
            is_candidate=False,
        )
        standby = CapturedImage(
            capture_date=captured_at.date(),
            channel_id="1",
            captured_at=captured_at + timedelta(milliseconds=400),
            image_path="/tmp/standby.jpg",
            status=ImageStatusEnum.pending,
            is_candidate=True,
        )
        db.session.add_all([primary, standby])
        db.session.flush()
        db.session.add(MatchResult(
            image_id=standby.id,
            status=MatchStatusEnum.unmatched_image,
            match_date=captured_at.date(),
        ))
        db.session.commit()

        run_matching_for_date.run(captured_at.date().isoformat())
        self._drain_matching()

        unmatched_ids = {
            row.image_id
            for row in MatchResult.query.filter_by(status=MatchStatusEnum.unmatched_image).all()
        }
        self.assertIn(primary.id, unmatched_ids)
        self.assertNotIn(standby.id, unmatched_ids)

    def test_match_record_uses_absolute_amount_for_signed_consumption(self):
        tx_time = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        record = ConsumptionRecord(
            student_no="230501",
            transaction_time=tx_time,
            amount=-8.0,
            transaction_id="tx-signed-amount-001",
            channel_id="1",
        )
        db.session.add(record)
        db.session.flush()
        image = self._image_with_price("1", 8.0, tx_time)
        db.session.commit()

        _match_record(record, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.image_id, image.id)
        self.assertEqual(match.status, MatchStatusEnum.matched)
        self.assertEqual(match.price_diff, 0.0)

    def test_match_record_compares_channel_text_without_ch_prefix_conversion(self):
        tx_time = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        record = ConsumptionRecord(
            student_no="230501",
            transaction_time=tx_time,
            amount=-8.0,
            transaction_id="tx-channel-ch01",
            channel_id="ch01",
        )
        db.session.add(record)
        db.session.flush()
        image_same_text_channel = self._image_with_price("ch01", 8.0, tx_time)
        image_numeric_channel = self._image_with_price("01", 8.0, tx_time)
        db.session.commit()

        _match_record(record, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.image_id, image_same_text_channel.id)
        self.assertNotEqual(match.image_id, image_numeric_channel.id)
        self.assertEqual(match.status, MatchStatusEnum.matched)

    def test_match_record_resolves_consumption_location_alias_to_channel(self):
        tx_time = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        db.session.add(VideoSource(
            name="食堂主 NVR",
            source_type="nvr",
            status="enabled",
            config_json={
                "host": "192.168.1.10",
                "port": 8080,
                "channel_ids": ["1", "2"],
                "channel_location_aliases": {"2": "二楼结算台"},
            },
            credentials_json_encrypted="",
        ))
        record = ConsumptionRecord(
            student_no="230501",
            transaction_time=tx_time,
            amount=-8.0,
            transaction_id="tx-alias-001",
            channel_id="二楼结算台",
        )
        db.session.add(record)
        db.session.flush()
        image_other_channel = self._image_with_price("1", 8.0, tx_time)
        image_alias_channel = self._image_with_price("2", 8.0, tx_time)
        db.session.commit()

        _match_record(record, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.image_id, image_alias_channel.id)
        self.assertNotEqual(match.image_id, image_other_channel.id)
        self.assertEqual(match.status, MatchStatusEnum.matched)

    def test_match_record_does_not_bind_pending_image_without_recognition(self):
        tx_time = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        record = ConsumptionRecord(
            student_no="230501",
            transaction_time=tx_time,
            amount=-8.0,
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

        _match_record(record, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertIsNone(match.image_id)
        self.assertEqual(match.status, MatchStatusEnum.unmatched_record)
        self.assertIsNone(match.price_diff)
        self.assertEqual(image.status, ImageStatusEnum.pending)

    def test_match_window_stages_default_to_one_three_five(self):
        self.assertEqual(normalize_match_window_stages(None), DEFAULT_MATCH_WINDOW_STAGES)
        self.assertEqual(normalize_match_window_stages("1,3,5"), (1, 3, 5))
        self.assertEqual(normalize_match_window_stages("5,1,3"), (1, 3, 5))

        tx_time = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        windows = matching_windows(tx_time)
        self.assertEqual(
            [(lower, upper) for lower, upper, _include_upper in windows],
            [
                (tx_time - timedelta(seconds=1), tx_time + timedelta(seconds=1)),
                (tx_time - timedelta(seconds=3), tx_time + timedelta(seconds=3)),
                (tx_time - timedelta(seconds=5), tx_time + timedelta(seconds=5)),
            ],
        )

    def test_match_record_uses_second_round_previous_two_seconds_when_primary_empty(self):
        tx_time = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        record = ConsumptionRecord(
            student_no="230501",
            transaction_time=tx_time,
            amount=-8.0,
            transaction_id="tx-fallback-2s",
            channel_id="1",
        )
        db.session.add(record)
        db.session.flush()
        image = self._image_with_price("1", 8.0, tx_time - timedelta(seconds=1.5))
        db.session.commit()

        _match_record(record, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.image_id, image.id)
        self.assertEqual(match.status, MatchStatusEnum.matched)
        self.assertEqual(match.time_diff_seconds, 1.5)

    def test_match_record_uses_third_round_previous_three_seconds_when_earlier_rounds_empty(self):
        tx_time = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        record = ConsumptionRecord(
            student_no="230501",
            transaction_time=tx_time,
            amount=-8.0,
            transaction_id="tx-fallback-3s",
            channel_id="1",
        )
        db.session.add(record)
        db.session.flush()
        image = self._image_with_price("1", 8.0, tx_time - timedelta(seconds=2.5))
        db.session.commit()

        _match_record(record, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.image_id, image.id)
        self.assertEqual(match.status, MatchStatusEnum.matched)
        self.assertEqual(match.time_diff_seconds, 2.5)

    def test_match_record_uses_plus_minus_3s_when_image_is_slightly_after_consumption(self):
        tx_time = datetime(2026, 8, 16, 11, 51, 37, tzinfo=timezone.utc)
        record = ConsumptionRecord(
            student_no="230501",
            transaction_time=tx_time,
            amount=-8.0,
            transaction_id="tx-forward-1083ms",
            channel_id="1",
        )
        db.session.add(record)
        db.session.flush()
        image = self._image_with_price("1", 8.0, tx_time + timedelta(milliseconds=1083))
        db.session.commit()

        _match_record(record, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.image_id, image.id)
        self.assertEqual(match.status, MatchStatusEnum.matched)
        self.assertAlmostEqual(match.time_diff_seconds, 1.083, places=3)

    def test_match_record_uses_plus_minus_5s_when_outside_3s(self):
        tx_time = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        record = ConsumptionRecord(
            student_no="230501",
            transaction_time=tx_time,
            amount=-8.0,
            transaction_id="tx-forward-4s",
            channel_id="1",
        )
        db.session.add(record)
        db.session.flush()
        image = self._image_with_price("1", 8.0, tx_time + timedelta(seconds=4))
        db.session.commit()

        _match_record(record, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.image_id, image.id)
        self.assertEqual(match.status, MatchStatusEnum.matched)
        self.assertEqual(match.time_diff_seconds, 4)

    def test_match_record_rejects_image_beyond_5s(self):
        tx_time = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        record = ConsumptionRecord(
            student_no="230501",
            transaction_time=tx_time,
            amount=-8.0,
            transaction_id="tx-beyond-5s",
            channel_id="1",
        )
        db.session.add(record)
        db.session.flush()
        self._image_with_price("1", 8.0, tx_time + timedelta(seconds=5.1))
        db.session.commit()

        _match_record(record, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertIsNone(match.image_id)
        self.assertEqual(match.status, MatchStatusEnum.unmatched_record)

    def test_match_record_skips_wrong_price_in_inner_window_for_exact_outer_image(self):
        tx_time = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        record = ConsumptionRecord(
            student_no="230501",
            transaction_time=tx_time,
            amount=-8.0,
            transaction_id="tx-inner-window-priority",
            channel_id="1",
        )
        db.session.add(record)
        db.session.flush()
        inner_wrong_price = self._image_with_price("1", 20.0, tx_time + timedelta(milliseconds=400))
        outer_exact_price = self._image_with_price("1", 8.0, tx_time + timedelta(seconds=2))
        db.session.commit()

        _match_record(record, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.image_id, outer_exact_price.id)
        self.assertNotEqual(match.image_id, inner_wrong_price.id)
        self.assertEqual(match.status, MatchStatusEnum.matched)

    def test_match_record_does_not_reuse_image_already_taken_by_another_record(self):
        tx_time = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        first = ConsumptionRecord(
            student_no="230501",
            transaction_time=tx_time,
            amount=-8.0,
            transaction_id="tx-taken-001",
            channel_id="1",
        )
        second = ConsumptionRecord(
            student_no="230502",
            transaction_time=tx_time + timedelta(milliseconds=200),
            amount=-8.0,
            transaction_id="tx-taken-002",
            channel_id="1",
        )
        db.session.add_all([first, second])
        db.session.flush()
        taken_image = self._image_with_price("1", 8.0, tx_time)
        available_image = self._image_with_price("1", 8.0, tx_time - timedelta(seconds=1.5))
        db.session.commit()

        _match_record(first, price_tol=0.5, target_date=tx_time.date())
        _match_record(second, price_tol=0.5, target_date=tx_time.date())

        first_match = MatchResult.query.filter_by(consumption_record_id=first.id).one()
        second_match = MatchResult.query.filter_by(consumption_record_id=second.id).one()
        self.assertEqual(first_match.image_id, taken_image.id)
        self.assertEqual(second_match.image_id, available_image.id)

    def test_match_record_keeps_confirmed_manual_match(self):
        tx_time = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        record = ConsumptionRecord(
            student_no="230501",
            transaction_time=tx_time,
            amount=-8.0,
            transaction_id="tx-confirmed-001",
            channel_id="1",
        )
        db.session.add(record)
        db.session.flush()
        confirmed_image = self._image_with_price("1", 9.0, tx_time - timedelta(seconds=2))
        better_image = self._image_with_price("1", 8.0, tx_time)
        db.session.add(MatchResult(
            consumption_record_id=record.id,
            image_id=confirmed_image.id,
            status=MatchStatusEnum.confirmed,
            match_date=tx_time.date(),
            time_diff_seconds=2,
            price_diff=1,
            is_manual=True,
        ))
        db.session.commit()

        _match_record(record, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.image_id, confirmed_image.id)
        self.assertNotEqual(match.image_id, better_image.id)
        self.assertEqual(match.status, MatchStatusEnum.confirmed)
        self.assertTrue(match.is_manual)

    def test_match_record_rejects_price_difference_instead_of_pending_confirmation(self):
        tx_time = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        record = ConsumptionRecord(
            student_no="230501",
            transaction_time=tx_time,
            amount=-20.0,
            transaction_id="tx-price-diff-001",
            channel_id="1",
        )
        db.session.add(record)
        db.session.flush()
        image = self._image_with_price("1", 8.0, tx_time)
        db.session.commit()

        _match_record(record, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertIsNone(match.image_id)
        self.assertEqual(match.status, MatchStatusEnum.unmatched_record)
        self.assertIsNone(match.price_diff)
        self.assertEqual(image.status, ImageStatusEnum.identified)

    def test_wrong_amount_does_not_take_image_from_later_exact_record(self):
        tx_time = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        wrong_record = ConsumptionRecord(
            student_no="230501",
            transaction_time=tx_time,
            amount=-8.01,
            transaction_id="tx-price-diff-first",
            channel_id="1",
        )
        exact_record = ConsumptionRecord(
            student_no="230502",
            transaction_time=tx_time + timedelta(milliseconds=200),
            amount=-8.0,
            transaction_id="tx-price-exact-second",
            channel_id="1",
        )
        db.session.add_all([wrong_record, exact_record])
        db.session.flush()
        image = self._image_with_price("1", 8.0, tx_time)
        db.session.commit()

        _match_record(wrong_record, price_tol=0.5, target_date=tx_time.date())
        _match_record(exact_record, price_tol=0.5, target_date=tx_time.date())

        wrong_match = MatchResult.query.filter_by(consumption_record_id=wrong_record.id).one()
        exact_match = MatchResult.query.filter_by(consumption_record_id=exact_record.id).one()
        self.assertIsNone(wrong_match.image_id)
        self.assertEqual(wrong_match.status, MatchStatusEnum.unmatched_record)
        self.assertEqual(exact_match.image_id, image.id)
        self.assertEqual(exact_match.status, MatchStatusEnum.matched)

    def test_match_record_uses_aggregated_prices_to_break_time_tie(self):
        tx_time = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        record = ConsumptionRecord(
            student_no="230501",
            transaction_time=tx_time,
            amount=-8.0,
            transaction_id="tx-price-tie-001",
            channel_id="1",
        )
        db.session.add(record)
        db.session.flush()
        wrong_price = self._image_with_price("1", 10.0, tx_time)
        matching_price = self._image_with_price("1", 8.0, tx_time)
        db.session.commit()

        _match_record(record, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.image_id, matching_price.id)
        self.assertNotEqual(match.image_id, wrong_price.id)
        self.assertEqual(match.status, MatchStatusEnum.matched)

    def test_batch_schedules_whole_date_and_defers_publication_until_all_chunks(self):
        from app.services.date_matching import advance_date_matching
        self.app.config["MATCHING_BATCH_CHUNK_SIZE"] = 1
        start = datetime(2026, 3, 31, 12, 0)
        earlier = self._record(start - timedelta(seconds=4), batch="first")
        closer = self._record(start - timedelta(seconds=0.2), batch="second")
        image = self._image_with_price("1", 8, start)
        db.session.commit()
        result = run_matching_for_batch.run("first")
        self.assertTrue(result["scheduled"])
        day = start.date()
        advance_date_matching(day)  # freeze complete date, including other batch
        advance_date_matching(day)  # first record only
        self.assertEqual(MatchResult.query.count(), 0)
        self._drain_matching()
        self.assertIsNone(MatchResult.query.filter_by(consumption_record_id=earlier.id).one().image_id)
        self.assertEqual(MatchResult.query.filter_by(consumption_record_id=closer.id).one().image_id, image.id)

    def test_date_collection_resumes_when_time_budget_is_reached(self):
        from app.services.date_matching import advance_date_matching
        self.app.config["MATCHING_BATCH_TIME_BUDGET_SECONDS"] = 1
        start = datetime(2026, 3, 31, 12, 0)
        first = self._record(start)
        self._record(start + timedelta(seconds=10))
        self._image_with_price("1", 8, start)
        db.session.commit()
        run_matching_for_date(start.date().isoformat())
        advance_date_matching(start.date())
        with mock.patch("app.services.date_matching.time.monotonic", side_effect=[100.0, 101.0]):
            advance_date_matching(start.date())
        self.assertEqual(db.session.get(MatchingRun, start.date()).cursor, first.id)
        self.assertEqual(MatchResult.query.count(), 0)
        self._drain_matching()
        self.assertEqual(MatchResult.query.filter(MatchResult.consumption_record_id.isnot(None)).count(), 2)

    def _advance_to(self, day, phase):
        from app.services.date_matching import advance_date_matching
        for _ in range(100):
            run = db.session.get(MatchingRun, day)
            if run and run.phase == phase:
                return run
            advance_date_matching(day)
        self.fail(f"Did not reach {phase}")

    def test_same_round_uses_global_nearest_even_with_single_candidate_chunks(self):
        self.app.config["MATCHING_BATCH_CHUNK_SIZE"] = 1
        tx = datetime(2026, 3, 31, 12, 0)
        earlier = self._record(tx - timedelta(seconds=0.9))
        closer = self._record(tx - timedelta(seconds=0.1))
        image = self._image_with_price("1", 8, tx)
        db.session.commit()
        run_matching_for_date(tx.date().isoformat())
        self._drain_matching()
        self.assertIsNone(MatchResult.query.filter_by(consumption_record_id=earlier.id).one().image_id)
        self.assertEqual(MatchResult.query.filter_by(consumption_record_id=closer.id).one().image_id, image.id)

    def test_equal_differences_are_deterministic_across_reruns_and_chunk_sizes(self):
        tx = datetime(2026, 3, 31, 12, 0)
        records = [self._record(tx), self._record(tx)]
        images = [self._image_with_price("1", 8, tx), self._image_with_price("1", 8, tx)]
        db.session.commit()
        for chunk_size in (1, 200):
            self.app.config["MATCHING_BATCH_CHUNK_SIZE"] = chunk_size
            run_matching_for_date(tx.date().isoformat())
            self._drain_matching()
            pairs = [(m.consumption_record_id, m.image_id) for m in MatchResult.query.order_by(MatchResult.consumption_record_id).all()]
            self.assertEqual(pairs, [(records[0].id, images[0].id), (records[1].id, images[1].id)])

    def test_recompute_releases_old_automatic_match_and_notifies_losing_student(self):
        from app.tasks.nutrition import compute_nutrition_log
        tx = datetime(2026, 3, 31, 12, 0)
        wrong = self._record(tx - timedelta(seconds=4))
        wrong.student_id = 123
        correct = self._record(tx)
        image = self._image_with_price("1", 8, tx)
        old = MatchResult(consumption_record_id=wrong.id, image_id=image.id, student_id=123,
                          match_date=tx.date(), status=MatchStatusEnum.matched)
        db.session.add(old)
        db.session.commit()
        run_matching_for_date(tx.date().isoformat())
        with mock.patch.object(compute_nutrition_log, "delay") as notify:
            self._drain_matching()
        self.assertIsNone(db.session.get(MatchResult, old.id).image_id)
        self.assertEqual(MatchResult.query.filter_by(consumption_record_id=correct.id).one().image_id, image.id)
        notify.assert_called_once_with(123, tx.date().isoformat())

    def test_confirmation_during_run_invalidates_draft_and_keeps_manual_pair(self):
        from app.services.date_matching import advance_date_matching
        tx = datetime(2026, 3, 31, 12, 0)
        earlier = self._record(tx - timedelta(seconds=4))
        closer = self._record(tx)
        image = self._image_with_price("1", 8, tx)
        old = MatchResult(consumption_record_id=earlier.id, image_id=image.id,
                          match_date=tx.date(), status=MatchStatusEnum.matched)
        db.session.add(old)
        db.session.commit()
        run_matching_for_date(tx.date().isoformat())
        self._advance_to(tx.date(), "publish")
        old.status = MatchStatusEnum.confirmed
        old.is_manual = True
        db.session.commit()
        advance_date_matching(tx.date())
        self.assertEqual(db.session.get(MatchingRun, tx.date()).phase, "idle")
        self._drain_matching()
        self.assertEqual(db.session.get(MatchResult, old.id).image_id, image.id)
        self.assertIsNone(MatchResult.query.filter_by(consumption_record_id=closer.id).one().image_id)

    def test_duplicate_requests_coalesce_and_new_data_is_included(self):
        from app.tasks.matching import continue_date_matching
        from app.services.date_matching import advance_date_matching
        tx = datetime(2026, 3, 31, 12, 0)
        self._record(tx)
        db.session.commit()
        with mock.patch.object(continue_date_matching, "delay") as dispatch:
            run_matching_for_date(tx.date().isoformat())
            advance_date_matching(tx.date())
            image = self._image_with_price("1", 8, tx)
            db.session.commit()
            for _ in range(5):
                run_matching_for_date(tx.date().isoformat())
            dispatch.assert_called_once()
        self._drain_matching()
        run = db.session.get(MatchingRun, tx.date())
        self.assertEqual(run.requested, run.completed)
        self.assertEqual(MatchResult.query.filter_by(status=MatchStatusEnum.matched).one().image_id, image.id)

    def test_failed_publication_preserves_old_results_and_resumes(self):
        from app.services.date_matching import advance_date_matching
        tx = datetime(2026, 3, 31, 12, 0)
        record = self._record(tx)
        self._image_with_price("1", 8, tx)
        old = MatchResult(consumption_record_id=record.id, match_date=tx.date(), status=MatchStatusEnum.unmatched_record)
        db.session.add(old)
        db.session.commit()
        run_matching_for_date(tx.date().isoformat())
        self._advance_to(tx.date(), "publish")
        with mock.patch("app.services.date_matching._publication_lock", side_effect=RuntimeError("temporary failure")):
            with self.assertRaises(RuntimeError):
                advance_date_matching(tx.date())
        db.session.rollback()
        self.assertEqual(db.session.get(MatchResult, old.id).status, MatchStatusEnum.unmatched_record)
        self._drain_matching()
        self.assertEqual(db.session.get(MatchResult, old.id).status, MatchStatusEnum.matched)

    def test_window_boundaries_and_calibration_metadata(self):
        self.app.config["TIME_OFFSET_CALIBRATION"] = 10.0
        tx = datetime(2026, 3, 31, 12, 0)
        records = [self._record(tx + timedelta(minutes=i), channel=str(i)) for i in range(4)]
        for i, difference in enumerate((11, 15, 15.001, 5)):
            self._image_with_price(str(i), 8, tx + timedelta(minutes=i, seconds=difference))
        db.session.commit()
        run_matching_for_date(tx.date().isoformat())
        self._drain_matching()
        matches = [MatchResult.query.filter_by(consumption_record_id=r.id).one() for r in records]
        self.assertEqual([m.match_round for m in matches], [1, 3, None, 3])
        self.assertEqual([m.time_diff_seconds for m in matches], [1.0, 5.0, None, 5.0])
        payload = matches[0].to_dict()
        self.assertEqual(payload["applied_time_offset_seconds"], 10.0)
        self.assertEqual(payload["raw_time_diff_seconds"], 11.0)

    def test_runtime_configuration_is_loaded_and_frozen_during_run(self):
        from app.services.date_matching import advance_date_matching
        from app.services.runtime_config import persist_runtime_overrides
        from tempfile import TemporaryDirectory
        tx = datetime(2026, 3, 31, 12, 0)
        record = self._record(tx)
        self._image_with_price("1", 8, tx + timedelta(seconds=3))
        db.session.commit()
        with TemporaryDirectory() as directory:
            self.app.config["LOCAL_RUNTIME_CONFIG_PATH"] = directory + "/runtime.json"
            persist_runtime_overrides(self.app.config, {"TIME_MATCH_WINDOW_STAGES": [1]})
            run_matching_for_date(tx.date().isoformat())
            advance_date_matching(tx.date())
            persist_runtime_overrides(self.app.config, {"TIME_MATCH_WINDOW_STAGES": [1, 5]})
            self._drain_matching()
            self.assertIsNone(MatchResult.query.filter_by(consumption_record_id=record.id).one().image_id)
            run_matching_for_date(tx.date().isoformat())
            self._drain_matching()
            self.assertEqual(MatchResult.query.filter_by(consumption_record_id=record.id).one().match_window_seconds, 5)

    def test_new_recognition_result_invalidates_frozen_candidates(self):
        tx = datetime(2026, 3, 31, 12, 0)
        record = self._record(tx)
        image = self._image_with_price("1", 8, tx)
        db.session.commit()
        run_matching_for_date(tx.date().isoformat())
        self._advance_to(tx.date(), "publish")
        DishRecognition.query.filter_by(image_id=image.id).one().is_low_confidence = True
        db.session.commit()
        self._drain_matching()
        self.assertIsNone(MatchResult.query.filter_by(consumption_record_id=record.id).one().image_id)

    def test_image_across_midnight_is_not_assigned_twice(self):
        tx = datetime(2026, 3, 31, 23, 59, 59)
        first = self._record(tx)
        second = self._record(tx + timedelta(seconds=2))
        image = self._image_with_price("1", 8, tx + timedelta(seconds=1))
        db.session.commit()
        match_single_image(image.id)
        self._drain_matching()
        matches = MatchResult.query.filter_by(image_id=image.id).all()
        self.assertEqual(len(matches), 1)
        self.assertIn(matches[0].consumption_record_id, (first.id, second.id))

    def _record(self, tx, batch="test", amount=-8, channel="1"):
        record = ConsumptionRecord(
            student_no="230501", transaction_time=tx, amount=amount,
            transaction_id=f"tx-{ConsumptionRecord.query.count()}", channel_id=channel, import_batch=batch,
        )
        db.session.add(record)
        db.session.flush()
        return record

    def test_match_single_image_checks_records_after_image_for_fallback_window(self):
        image_time = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        record = ConsumptionRecord(
            student_no="230501",
            transaction_time=image_time + timedelta(seconds=2.5),
            amount=-8.0,
            transaction_id="tx-single-image-fallback",
            channel_id="1",
        )
        db.session.add(record)
        db.session.flush()
        image = self._image_with_price("1", 8.0, image_time)
        db.session.commit()

        match_single_image(image.id)
        self._drain_matching()

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.image_id, image.id)
        self.assertEqual(match.status, MatchStatusEnum.matched)
        self.assertEqual(match.time_diff_seconds, 2.5)

    def test_match_single_image_matches_record_slightly_before_image(self):
        image_time = datetime(2026, 8, 16, 11, 51, 38, 83000, tzinfo=timezone.utc)
        record = ConsumptionRecord(
            student_no="230501",
            transaction_time=datetime(2026, 8, 16, 11, 51, 37, tzinfo=timezone.utc),
            amount=-8.0,
            transaction_id="tx-single-image-forward",
            channel_id="1",
        )
        db.session.add(record)
        db.session.flush()
        image = self._image_with_price("1", 8.0, image_time)
        db.session.commit()

        match_single_image(image.id)
        self._drain_matching()

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.image_id, image.id)
        self.assertEqual(match.status, MatchStatusEnum.matched)
        self.assertAlmostEqual(match.time_diff_seconds, 1.083, places=3)

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
        self._drain_matching()

        match = MatchResult.query.filter_by(image_id=image.id).one()
        self.assertEqual(match.status, MatchStatusEnum.unmatched_image)
        self.assertEqual(match.match_date, tx_time.date())

    def test_run_matching_ignores_positive_recharge_records(self):
        tx_time = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        recharge = ConsumptionRecord(
            student_no="230501",
            transaction_time=tx_time,
            amount=20.0,
            transaction_id="tx-positive-recharge",
            channel_id="1",
        )
        db.session.add(recharge)
        db.session.flush()
        image = self._image_with_price("1", 20.0, tx_time)
        db.session.commit()

        run_matching_for_date("2026-03-31")
        self._drain_matching()

        self.assertIsNone(MatchResult.query.filter_by(consumption_record_id=recharge.id).first())
        image_marker = MatchResult.query.filter_by(image_id=image.id).one()
        self.assertEqual(image_marker.status, MatchStatusEnum.unmatched_image)

    def test_run_matching_filters_by_enabled_transaction_location_ids(self):
        self.app.config[ENABLED_TRANSACTION_LOCATION_IDS_KEY] = ["1-15"]
        tx_time = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        enabled_record = ConsumptionRecord(
            student_no="230501",
            transaction_time=tx_time,
            amount=-8.0,
            transaction_id="tx-enabled-location",
            channel_id="1-15",
        )
        disabled_record = ConsumptionRecord(
            student_no="230502",
            transaction_time=tx_time,
            amount=-8.0,
            transaction_id="tx-disabled-location",
            channel_id="1-16",
        )
        db.session.add_all([enabled_record, disabled_record])
        db.session.flush()
        enabled_image = self._image_with_price("1-15", 8.0, tx_time)
        disabled_image = self._image_with_price("1-16", 8.0, tx_time)
        db.session.commit()

        run_matching_for_date("2026-03-31")
        self._drain_matching()

        enabled_match = MatchResult.query.filter_by(consumption_record_id=enabled_record.id).one()
        self.assertEqual(enabled_match.image_id, enabled_image.id)
        self.assertIsNone(MatchResult.query.filter_by(consumption_record_id=disabled_record.id).first())
        disabled_image_marker = MatchResult.query.filter_by(image_id=disabled_image.id).one()
        self.assertEqual(disabled_image_marker.status, MatchStatusEnum.unmatched_image)

    def test_match_single_image_filters_by_enabled_transaction_location_ids(self):
        self.app.config[ENABLED_TRANSACTION_LOCATION_IDS_KEY] = ["1-15"]
        image_time = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        disabled_record = ConsumptionRecord(
            student_no="230501",
            transaction_time=image_time,
            amount=-8.0,
            transaction_id="tx-single-disabled-location",
            channel_id="1-16",
        )
        db.session.add(disabled_record)
        db.session.flush()
        image = self._image_with_price("1-16", 8.0, image_time)
        db.session.commit()

        match_single_image(image.id)
        self._drain_matching()

        self.assertIsNone(MatchResult.query.filter_by(consumption_record_id=disabled_record.id).first())

    def test_disabled_location_match_does_not_occupy_image_for_enabled_location(self):
        self.app.config[ENABLED_TRANSACTION_LOCATION_IDS_KEY] = ["1-15"]
        tx_time = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        disabled_record = ConsumptionRecord(
            student_no="230501",
            transaction_time=tx_time,
            amount=-8.0,
            transaction_id="tx-disabled-existing-match",
            channel_id="1-16",
        )
        enabled_record = ConsumptionRecord(
            student_no="230502",
            transaction_time=tx_time,
            amount=-8.0,
            transaction_id="tx-enabled-after-disabled-match",
            channel_id="1-15",
        )
        db.session.add_all([disabled_record, enabled_record])
        db.session.flush()
        image = self._image_with_price("1-15", 8.0, tx_time)
        db.session.add(MatchResult(
            consumption_record_id=disabled_record.id,
            image_id=image.id,
            status=MatchStatusEnum.matched,
            match_date=tx_time.date(),
            time_diff_seconds=0,
            price_diff=0,
        ))
        db.session.commit()

        run_matching_for_date("2026-03-31")
        self._drain_matching()

        enabled_match = MatchResult.query.filter_by(consumption_record_id=enabled_record.id).one()
        self.assertEqual(enabled_match.image_id, image.id)

    def test_match_record_clears_existing_match_when_channel_has_no_candidate(self):
        tx_time = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        record = ConsumptionRecord(
            student_no="230501",
            transaction_time=tx_time,
            amount=-8.0,
            transaction_id="tx-channel-002",
            channel_id="9",
        )
        db.session.add(record)
        db.session.flush()
        image = self._image_with_price("1", 8.0, tx_time)
        db.session.add(MatchResult(
            consumption_record_id=record.id,
            image_id=image.id,
            captured_at=image.captured_at,
            status=MatchStatusEnum.matched,
            match_date=tx_time.date(),
            time_diff_seconds=0,
            price_diff=0,
        ))
        db.session.commit()

        _match_record(record, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertIsNone(match.image_id)
        self.assertIsNone(match.captured_at)
        self.assertEqual(match.status, MatchStatusEnum.unmatched_record)
        self.assertIsNone(match.time_diff_seconds)
        self.assertIsNone(match.price_diff)


if __name__ == "__main__":
    unittest.main()
