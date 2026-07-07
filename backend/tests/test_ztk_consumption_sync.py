import os
import sys
import types
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
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

from app import db  # noqa: E402
import app.models  # noqa: F401,E402
from app.models import ConsumptionRecord, ConsumptionSyncState, Student  # noqa: E402
from app.services.ztk_consumption_sync import ZtkConsumptionSyncService  # noqa: E402


class FakeCursor:
    def __init__(self, pages):
        self.pages = list(pages)
        self.executions = []

    def execute(self, sql, params=()):
        self.executions.append({"sql": sql, "params": params})

    def fetchall(self):
        if not self.pages:
            return []
        return self.pages.pop(0)


class FakeConnection:
    def __init__(self, pages):
        self.cursor_obj = FakeCursor(pages)
        self.closed = False

    def cursor(self, as_dict=False):
        if not as_dict:
            raise AssertionError("ZTK sync should request dict rows")
        return self.cursor_obj

    def close(self):
        self.closed = True


class ZtkConsumptionSyncServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask(__name__)
        cls.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            ZTK_DB_HOST="sqlserver.example.local",
            ZTK_DB_PORT=1433,
            ZTK_DB_NAME="ZYTK40_PLUS",
            ZTK_DB_USER="test-user",
            ZTK_DB_PASSWORD="test-password",
            ZTK_PAYMENT_BOOKS_TABLE="ac_PaymentBooks",
            ZTK_SYNC_PAGE_SIZE=10,
            ZTK_SYNC_MAX_ROWS_PER_RUN=1000,
            ZTK_SYNC_LOOKBACK_MINUTES=0,
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
        db.session.query(ConsumptionRecord).delete()
        db.session.query(ConsumptionSyncState).delete()
        db.session.query(Student).delete()
        db.session.commit()

    def tearDown(self):
        db.session.rollback()
        db.session.remove()

    def _service_with_pages(self, pages):
        connection = FakeConnection(pages)
        service = ZtkConsumptionSyncService(connection_factory=lambda: connection)
        return service, connection

    def test_sync_imports_payment_books_rows_and_links_student_by_cardcode(self):
        # Only the transaction table is synced; the only student identifier on
        # a payment-books row is CardCode, so linking happens by card_no.
        db.session.add(Student(student_no="20260001", name="张三", class_id="2026-1", card_no="C1001"))
        db.session.commit()
        deal_time = datetime(2026, 6, 8, 12, 5, 30)
        service, connection = self._service_with_pages([
            [{
                "RecID": 1001,
                "AccNum": 80000001,
                "CardCode": "C1001",
                "DealTime": deal_time,
                "MonDeal": Decimal("-7.50"),
                "TerminalNum": 3,
                "StaNum": 9,
                "FeeNum": 11,
                "DealerNum": 1000,
            }]
        ])

        result = service.sync_once(batch_id="ztk-test-batch")

        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["skipped_duplicates"], 0)
        self.assertEqual(result["errors"], [])
        self.assertTrue(connection.closed)

        record = ConsumptionRecord.query.one()
        self.assertEqual(record.student_no, "20260001")
        self.assertEqual(record.student_name, "张三")
        self.assertEqual(float(record.amount), 7.5)
        self.assertEqual(record.transaction_id, "ztk:PaymentBooks:1001")
        self.assertEqual(record.channel_id, "3")
        self.assertEqual(record.import_batch, "ztk-test-batch")
        self.assertEqual(record.source_system, ZtkConsumptionSyncService.SOURCE_SYSTEM)
        self.assertEqual(record.source_record_id, "1001")
        self.assertEqual(record.source_payload["MonDeal"], -7.5)
        self.assertEqual(record.student_id, 1)

        state = ConsumptionSyncState.query.one()
        self.assertEqual(state.cursor_source_record_id, "1001")
        self.assertEqual(state.cursor_transaction_time.hour, 12)

    def test_sync_skips_duplicate_source_record_and_uses_station_when_terminal_empty(self):
        existing = ConsumptionRecord(
            student_no="20260001",
            transaction_time=datetime(2026, 6, 8, 12, 5, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
            amount=7.5,
            transaction_id="ztk:PaymentBooks:1001",
            source_system=ZtkConsumptionSyncService.SOURCE_SYSTEM,
            source_record_id="1001",
        )
        db.session.add(existing)
        db.session.commit()
        service, _ = self._service_with_pages([
            [{
                "RecID": 1001,
                "DealTime": datetime(2026, 6, 8, 12, 5, 30),
                "MonDeal": Decimal("-7.50"),
                "TerminalNum": None,
                "StaNum": 9,
            }, {
                "RecID": 1002,
                "DealTime": datetime(2026, 6, 8, 12, 6, 30),
                "MonDeal": Decimal("-8.00"),
                "TerminalNum": "",
                "StaNum": 9,
            }]
        ])

        result = service.sync_once(batch_id="ztk-test-batch")

        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["skipped_duplicates"], 1)
        self.assertEqual(ConsumptionRecord.query.count(), 2)
        new_record = ConsumptionRecord.query.filter_by(transaction_id="ztk:PaymentBooks:1002").one()
        self.assertEqual(new_record.channel_id, "9")

    def test_sync_preserves_zero_terminal_channel(self):
        service, _ = self._service_with_pages([
            [{
                "RecID": 1003,
                "DealTime": datetime(2026, 6, 8, 12, 7, 30),
                "MonDeal": Decimal("-8.00"),
                "TerminalNum": 0,
                "StaNum": 9,
            }]
        ])

        service.sync_once(batch_id="ztk-zero-terminal")

        record = ConsumptionRecord.query.one()
        self.assertEqual(record.channel_id, "0")

    def test_sync_uses_dealtime_and_recid_as_incremental_cursor(self):
        cursor_time = datetime(2026, 6, 8, 12, 5, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        db.session.add(ConsumptionSyncState(
            source_system=ZtkConsumptionSyncService.SOURCE_SYSTEM,
            cursor_transaction_time=cursor_time,
            cursor_source_record_id="1001",
        ))
        db.session.commit()
        service, connection = self._service_with_pages([[]])

        service.sync_once(batch_id="ztk-cursor-test")

        params = connection.cursor_obj.executions[0]["params"]
        self.assertEqual(params[0], datetime(2026, 6, 8, 12, 5, 30))
        self.assertEqual(params[1], datetime(2026, 6, 8, 12, 5, 30))
        self.assertEqual(params[2], 1001)

    def test_sync_stops_at_max_rows_per_run(self):
        self.app.config.update(
            ZTK_SYNC_PAGE_SIZE=2,
            ZTK_SYNC_MAX_ROWS_PER_RUN=2,
        )
        self.addCleanup(lambda: self.app.config.update(
            ZTK_SYNC_PAGE_SIZE=10,
            ZTK_SYNC_MAX_ROWS_PER_RUN=1000,
        ))
        service, connection = self._service_with_pages([
            [{
                "RecID": 1001,
                "DealTime": datetime(2026, 6, 8, 12, 5, 30),
                "MonDeal": Decimal("-7.50"),
                "TerminalNum": 1,
            }, {
                "RecID": 1002,
                "DealTime": datetime(2026, 6, 8, 12, 6, 30),
                "MonDeal": Decimal("-8.00"),
                "TerminalNum": 1,
            }],
            [{
                "RecID": 1003,
                "DealTime": datetime(2026, 6, 8, 12, 7, 30),
                "MonDeal": Decimal("-9.00"),
                "TerminalNum": 1,
            }],
        ])

        result = service.sync_once(batch_id="ztk-max-rows-test")

        self.assertEqual(result["total_rows"], 2)
        self.assertEqual(result["imported"], 2)
        self.assertTrue(result["has_more"])
        self.assertEqual(result["max_rows_per_run"], 2)
        self.assertEqual(len(connection.cursor_obj.executions), 1)
        self.assertEqual(ConsumptionRecord.query.count(), 2)
        state = ConsumptionSyncState.query.one()
        self.assertEqual(state.cursor_source_record_id, "1002")

    def test_sync_uses_configured_table_names(self):
        self.app.config.update(
            ZTK_PAYMENT_BOOKS_TABLE="dbo.PaymentBooksCustom",
        )
        self.addCleanup(lambda: self.app.config.update(
            ZTK_PAYMENT_BOOKS_TABLE="ac_PaymentBooks",
        ))
        service, connection = self._service_with_pages([[]])

        service.sync_once(batch_id="ztk-custom-table-test")

        sql = connection.cursor_obj.executions[0]["sql"]
        self.assertIn("FROM [dbo].[PaymentBooksCustom] p WITH (NOLOCK)", sql)
        # Only the transaction table is queried — there must be no JOIN.
        self.assertNotIn("JOIN", sql)

    def test_sync_rejects_invalid_configured_table_name(self):
        self.app.config["ZTK_PAYMENT_BOOKS_TABLE"] = "ac_PaymentBooks; DROP TABLE students"
        self.addCleanup(lambda: self.app.config.update(ZTK_PAYMENT_BOOKS_TABLE="ac_PaymentBooks"))
        service, _ = self._service_with_pages([[]])

        with self.assertRaisesRegex(RuntimeError, "ZTK_PAYMENT_BOOKS_TABLE"):
            service.sync_once(batch_id="ztk-invalid-table-test")

    def test_sync_lookback_replays_window_from_record_zero(self):
        self.app.config["ZTK_SYNC_LOOKBACK_MINUTES"] = 5
        self.addCleanup(lambda: self.app.config.update(ZTK_SYNC_LOOKBACK_MINUTES=0))
        cursor_time = datetime(2026, 6, 8, 12, 5, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        db.session.add(ConsumptionSyncState(
            source_system=ZtkConsumptionSyncService.SOURCE_SYSTEM,
            cursor_transaction_time=cursor_time,
            cursor_source_record_id="1001",
        ))
        db.session.commit()
        service, connection = self._service_with_pages([[]])

        service.sync_once(batch_id="ztk-lookback-test")

        params = connection.cursor_obj.executions[0]["params"]
        self.assertEqual(params[0], datetime(2026, 6, 8, 12, 0, 30))
        self.assertEqual(params[2], 0)

    def test_sync_records_configuration_errors_in_state(self):
        self.app.config["ZTK_DB_HOST"] = ""
        self.addCleanup(lambda: self.app.config.update(ZTK_DB_HOST="sqlserver.example.local"))
        service = ZtkConsumptionSyncService(connection_factory=lambda: FakeConnection([]))

        with self.assertRaisesRegex(RuntimeError, "ZTK_DB_HOST"):
            service.sync_once(batch_id="ztk-config-error")

        state = ConsumptionSyncState.query.one()
        self.assertIn("ZTK_DB_HOST", state.last_error)
        self.assertEqual(state.last_error_count, 1)


if __name__ == "__main__":
    unittest.main()
