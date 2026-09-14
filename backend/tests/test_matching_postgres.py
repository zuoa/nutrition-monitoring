"""Opt-in integration coverage using an isolated, automatically removed PG schema.

MATCHING_TEST_DATABASE_URL=postgresql://... python -m pytest tests/test_matching_postgres.py
"""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import threading
import unittest
from uuid import uuid4

from flask import Flask
import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db
from app.models import CapturedImage, ConsumptionRecord, Dish, DishRecognition, ImageStatusEnum, MatchResult, MatchingRun, MatchStatusEnum
from app.services.date_matching import advance_date_matching, request_date_matching, _publication_lock


@unittest.skipUnless(os.environ.get("MATCHING_TEST_DATABASE_URL"), "Set MATCHING_TEST_DATABASE_URL for PostgreSQL integration tests")
class PostgresMatchingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = "matching_test_" + uuid4().hex
        cls.engine = sa.create_engine(os.environ["MATCHING_TEST_DATABASE_URL"])
        with cls.engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.app = Flask(__name__)
        cls.app.config.update(
            SQLALCHEMY_DATABASE_URI=os.environ["MATCHING_TEST_DATABASE_URL"],
            SQLALCHEMY_ENGINE_OPTIONS={"connect_args": {"options": f"-csearch_path={cls.schema} -clock_timeout=5000"}},
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            LOCAL_RUNTIME_CONFIG_PATH="/tmp/matching-postgres-no-runtime-config.json",
            TIME_MATCH_WINDOW_STAGES=[1, 3, 5],
            MATCHING_BATCH_CHUNK_SIZE=1,
        )
        db.init_app(cls.app)

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.engine.dispose()
        with cls.engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.engine.dispose()

    def setUp(self):
        self.context = self.app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.tx = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
        self.day = self.tx.date()
        dish = Dish(name="Test dish", price=8, category="other")
        image = CapturedImage(capture_date=self.day, captured_at=self.tx, channel_id="1", image_path="/tmp/test.jpg", status=ImageStatusEnum.identified, is_candidate=False)
        records = [ConsumptionRecord(student_no="1", transaction_id=f"test-{i}", transaction_time=self.tx - timedelta(seconds=diff), amount=-8, channel_id="1")
                   for i, diff in enumerate((4, 0.1))]
        db.session.add_all([dish, image, *records])
        db.session.flush()
        db.session.add(DishRecognition(image_id=image.id, dish_id=dish.id, dish_name_raw=dish.name, confidence=1, is_low_confidence=False))
        db.session.commit()
        self.image_id = image.id
        self.record_ids = [record.id for record in records]

    def tearDown(self):
        db.session.remove()
        self.context.pop()

    def _step(self):
        with self.app.app_context():
            try:
                return advance_date_matching(self.day)
            finally:
                db.session.remove()

    def test_duplicate_workers_serialize_and_assign_once(self):
        request_date_matching(self.day)
        with ThreadPoolExecutor(max_workers=4) as pool:
            for _ in range(30):
                pending = list(pool.map(lambda _: self._step(), range(4)))
                if not any(pending):
                    break
        db.session.expire_all()
        run = db.session.get(MatchingRun, self.day)
        self.assertEqual(run.phase, "idle")
        self.assertEqual(run.completed, run.requested)
        matches = MatchResult.query.filter_by(image_id=self.image_id).all()
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].consumption_record_id, self.record_ids[1])

    def test_publication_waits_for_manual_write_and_rechecks_snapshot(self):
        request_date_matching(self.day)
        for _ in range(40):
            self._step()
            db.session.expire_all()
            if db.session.get(MatchingRun, self.day).phase == "publish":
                break
        db.session.rollback()
        _publication_lock()
        db.session.add(MatchResult(consumption_record_id=self.record_ids[0], image_id=self.image_id,
                                   match_date=self.day, status=MatchStatusEnum.confirmed, is_manual=True))
        db.session.flush()
        started = threading.Event()

        def publish():
            started.set()
            return self._step()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(publish)
            self.assertTrue(started.wait(2))
            # Publication cannot pass the manual writer's table lock.
            self.assertFalse(future.done())
            db.session.commit()
            future.result(timeout=10)
        for _ in range(40):
            if not self._step():
                break
        db.session.expire_all()
        match = MatchResult.query.filter_by(image_id=self.image_id).one()
        self.assertEqual(match.status, MatchStatusEnum.confirmed)
        self.assertEqual(match.consumption_record_id, self.record_ids[0])

    def test_calibrated_window_can_include_images_from_next_local_date(self):
        from zoneinfo import ZoneInfo
        # Session timezone is deliberately non-UTC. Naive UTC search bounds
        # would be interpreted as local time and miss this next-date image.
        tx = datetime(2026, 9, 14, 23, 59, 59, tzinfo=ZoneInfo("Asia/Shanghai"))
        db.session.execute(sa.text("SET TIME ZONE 'Asia/Shanghai'"))
        for index, record in enumerate(ConsumptionRecord.query.order_by(ConsumptionRecord.id).all()):
            record.transaction_time = tx - timedelta(seconds=4 if index == 0 else 0)
        image = db.session.get(CapturedImage, self.image_id)
        image.captured_at = tx + timedelta(seconds=1)
        image.capture_date = self.day + timedelta(days=1)
        db.session.commit()
        request_date_matching(self.day)
        for _ in range(40):
            if not advance_date_matching(self.day):
                break
        match = MatchResult.query.filter_by(image_id=self.image_id).one()
        self.assertEqual(match.consumption_record_id, self.record_ids[1])
        self.assertEqual(match.time_diff_seconds, 1)

    def test_matching_migration_is_idempotent_on_postgres(self):
        import importlib.util
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        path = Path(__file__).resolve().parents[1] / "migrations/versions/20260914_0026_add_matching_runs.py"
        spec = importlib.util.spec_from_file_location("matching_pg_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        db.session.remove()
        with db.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()  # create_all schema already has the fields
                migration.downgrade()
                migration.upgrade()
                migration.upgrade()
        request_date_matching(self.day)
        for _ in range(40):
            if not advance_date_matching(self.day):
                break
        self.assertEqual(MatchResult.query.filter_by(image_id=self.image_id).one().consumption_record_id, self.record_ids[1])
