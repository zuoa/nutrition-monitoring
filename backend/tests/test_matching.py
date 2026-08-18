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
    MatchStatusEnum,
    VideoSource,
)
from app.services.consumption_location_filter import ENABLED_TRANSACTION_LOCATION_IDS_KEY  # noqa: E402
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
        self._dish_seq = 0
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
        image_same_channel = self._image_with_price("1", 10.0, tx_time)
        image_other_channel = self._image_with_price("2", 8.0, tx_time)
        db.session.commit()

        _match_record(record, tolerance_s=5, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.image_id, image_same_channel.id)
        self.assertEqual(match.captured_at, image_same_channel.captured_at)
        self.assertEqual(match.to_dict()["captured_at"], image_same_channel.captured_at.isoformat())
        self.assertNotEqual(match.image_id, image_other_channel.id)
        self.assertEqual(match.status, MatchStatusEnum.time_matched_only)
        self.assertEqual(match.price_diff, 2.0)

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

        _match_record(record, tolerance_s=5, price_tol=0.5, target_date=tx_time.date())

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
        image_same_text_channel = self._image_with_price("ch01", 10.0, tx_time)
        image_numeric_channel = self._image_with_price("01", 8.0, tx_time)
        db.session.commit()

        _match_record(record, tolerance_s=5, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.image_id, image_same_text_channel.id)
        self.assertNotEqual(match.image_id, image_numeric_channel.id)
        self.assertEqual(match.status, MatchStatusEnum.time_matched_only)

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

        _match_record(record, tolerance_s=5, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.image_id, image_alias_channel.id)
        self.assertNotEqual(match.image_id, image_other_channel.id)
        self.assertEqual(match.status, MatchStatusEnum.matched)

    def test_match_record_allows_pending_image_without_recognition(self):
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

        _match_record(record, tolerance_s=5, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.image_id, image.id)
        self.assertEqual(match.status, MatchStatusEnum.time_matched_only)
        self.assertEqual(match.price_diff, 8.0)

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

        _match_record(record, tolerance_s=1, price_tol=0.5, target_date=tx_time.date())

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

        _match_record(record, tolerance_s=1, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.image_id, image.id)
        self.assertEqual(match.status, MatchStatusEnum.matched)
        self.assertEqual(match.time_diff_seconds, 2.5)

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

        _match_record(first, tolerance_s=1, price_tol=0.5, target_date=tx_time.date())
        _match_record(second, tolerance_s=1, price_tol=0.5, target_date=tx_time.date())

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

        _match_record(record, tolerance_s=1, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.image_id, confirmed_image.id)
        self.assertNotEqual(match.image_id, better_image.id)
        self.assertEqual(match.status, MatchStatusEnum.confirmed)
        self.assertTrue(match.is_manual)

    def test_match_record_marks_large_price_diff_as_pending_confirmation(self):
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

        _match_record(record, tolerance_s=1, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.image_id, image.id)
        self.assertEqual(match.status, MatchStatusEnum.time_matched_only)
        self.assertEqual(match.price_diff, 12.0)

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

        _match_record(record, tolerance_s=1, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.image_id, matching_price.id)
        self.assertNotEqual(match.image_id, wrong_price.id)
        self.assertEqual(match.status, MatchStatusEnum.matched)

    def test_run_matching_for_batch_continues_with_keyset_cursor(self):
        self.app.config["MATCHING_BATCH_CHUNK_SIZE"] = 2
        batch_id = "batch-chunked-001"
        start = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        records = []
        images = []
        for index in range(3):
            # Keep every row on the same timestamp to exercise the id tie-break
            # at the keyset pagination boundary.
            tx_time = start
            record = ConsumptionRecord(
                student_no=f"23050{index + 1}",
                transaction_time=tx_time,
                amount=-8.0,
                transaction_id=f"tx-batch-chunk-{index + 1}",
                channel_id="1",
                import_batch=batch_id,
            )
            db.session.add(record)
            db.session.flush()
            records.append(record)
            images.append(self._image_with_price("1", 8.0, tx_time))
        db.session.commit()

        with mock.patch.object(run_matching_for_batch, "delay") as continuation:
            first_result = run_matching_for_batch.run(batch_id)

        self.assertFalse(first_result["completed"])
        self.assertEqual(first_result["processed"], 2)
        self.assertEqual(
            MatchResult.query.filter(
                MatchResult.consumption_record_id.in_([record.id for record in records])
            ).count(),
            2,
        )
        continuation.assert_called_once()
        continuation_args = continuation.call_args.args
        self.assertEqual(continuation_args[0], batch_id)
        self.assertEqual(continuation_args[2], records[1].id)
        self.assertEqual(continuation_args[3], [start.date().isoformat()])
        self.assertEqual(continuation_args[4], 2)

        with mock.patch.object(run_matching_for_batch, "delay") as next_continuation:
            final_result = run_matching_for_batch.run(*continuation_args)

        self.assertTrue(final_result["completed"])
        self.assertEqual(final_result["processed"], 3)
        next_continuation.assert_not_called()
        matches = MatchResult.query.filter(
            MatchResult.consumption_record_id.in_([record.id for record in records])
        ).all()
        self.assertEqual(len(matches), 3)
        self.assertEqual(
            {match.image_id for match in matches},
            {image.id for image in images},
        )

    def test_run_matching_for_batch_continues_when_time_budget_is_reached(self):
        self.app.config["MATCHING_BATCH_CHUNK_SIZE"] = 10
        self.app.config["MATCHING_BATCH_TIME_BUDGET_SECONDS"] = 1
        batch_id = "batch-time-budget-001"
        start = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
        records = []
        for index in range(2):
            tx_time = start + timedelta(seconds=index * 10)
            record = ConsumptionRecord(
                student_no=f"23051{index + 1}",
                transaction_time=tx_time,
                amount=-8.0,
                transaction_id=f"tx-batch-budget-{index + 1}",
                channel_id="1",
                import_batch=batch_id,
            )
            db.session.add(record)
            db.session.flush()
            records.append(record)
            self._image_with_price("1", 8.0, tx_time)
        db.session.commit()

        with mock.patch("app.tasks.matching.time.monotonic", side_effect=[100.0, 101.0]), mock.patch.object(
            run_matching_for_batch,
            "delay",
        ) as continuation:
            result = run_matching_for_batch.run(batch_id)

        self.assertFalse(result["completed"])
        self.assertEqual(result["processed"], 1)
        continuation.assert_called_once()
        self.assertEqual(continuation.call_args.args[2], records[0].id)
        self.assertEqual(
            MatchResult.query.filter(
                MatchResult.consumption_record_id.in_([record.id for record in records])
            ).count(),
            1,
        )

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

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertEqual(match.image_id, image.id)
        self.assertEqual(match.status, MatchStatusEnum.matched)
        self.assertEqual(match.time_diff_seconds, 2.5)

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

        _match_record(record, tolerance_s=5, price_tol=0.5, target_date=tx_time.date())

        match = MatchResult.query.filter_by(consumption_record_id=record.id).one()
        self.assertIsNone(match.image_id)
        self.assertIsNone(match.captured_at)
        self.assertEqual(match.status, MatchStatusEnum.unmatched_record)
        self.assertIsNone(match.time_diff_seconds)
        self.assertIsNone(match.price_diff)


if __name__ == "__main__":
    unittest.main()
