import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
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

if "celery" not in sys.modules:
    celery_module = types.ModuleType("celery")
    celery_schedules = types.ModuleType("celery.schedules")

    class _Celery:
        def __init__(self, *args, **kwargs):
            self.conf = types.SimpleNamespace(update=lambda **updates: None)

        def task(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

    celery_module.Celery = _Celery
    celery_schedules.crontab = lambda *args, **kwargs: object()
    sys.modules["celery"] = celery_module
    sys.modules["celery.schedules"] = celery_schedules

from app import db  # noqa: E402
import app.models  # noqa: F401,E402
from app.models import ConsumptionRecord, ConsumptionSyncState, Student  # noqa: E402
from app.services.ztk_consumption_sync import ZtkConsumptionSyncService  # noqa: E402
from app.tasks.ztk_consumption import _mark_sync_attempt_started, _sync_due_status  # noqa: E402


class FakeCursor:
    def __init__(self, pages, fail_accounts=False):
        self.pages = list(pages)
        self.executions = []
        self.fail_accounts = fail_accounts

    def execute(self, sql, params=()):
        self.executions.append({"sql": sql, "params": params})
        if self.fail_accounts and "ac_dict_Accounts" in sql:
            raise RuntimeError("Invalid object name 'ac_dict_Accounts'")

    def fetchall(self):
        if not self.pages:
            return []
        return self.pages.pop(0)


class FakeConnection:
    def __init__(self, pages, fail_accounts=False):
        self.cursor_obj = FakeCursor(pages, fail_accounts=fail_accounts)
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
            ZTK_PAYMENT_BOOKS_TABLE="dbo.view_ac_paymentbooks",
            ZTK_SYNC_PAGE_SIZE=10,
            ZTK_SYNC_MAX_ROWS_PER_RUN=1000,
            ZTK_SYNC_LOOKBACK_MINUTES=0,
            ZTK_SYNC_INTERVAL_MINUTES=5,
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

    def _service_with_pages(self, pages, fail_accounts=False):
        connection = FakeConnection(pages, fail_accounts=fail_accounts)
        service = ZtkConsumptionSyncService(connection_factory=lambda: connection)
        return service, connection

    def test_scheduled_sync_waits_for_configured_interval(self):
        db.session.add(ConsumptionSyncState(
            source_system=ZtkConsumptionSyncService.SOURCE_SYSTEM,
            last_synced_at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        ))
        db.session.commit()
        config = dict(self.app.config)
        config["ZTK_SYNC_INTERVAL_MINUTES"] = 10

        due, interval, next_sync_at = _sync_due_status(
            config,
            now=datetime(2026, 1, 1, 12, 5, tzinfo=timezone.utc),
        )

        self.assertFalse(due)
        self.assertEqual(interval, 10)
        self.assertEqual(next_sync_at, datetime(2026, 1, 1, 12, 10, tzinfo=timezone.utc))

        due, _, _ = _sync_due_status(
            config,
            now=datetime(2026, 1, 1, 12, 10, tzinfo=timezone.utc),
        )
        self.assertTrue(due)

    def test_sync_attempt_marker_throttles_follow_up_checks(self):
        config = dict(self.app.config)
        config["ZTK_SYNC_INTERVAL_MINUTES"] = 5

        _mark_sync_attempt_started(now=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc))

        due, interval, next_sync_at = _sync_due_status(
            config,
            now=datetime(2026, 1, 1, 12, 1, tzinfo=timezone.utc),
        )

        self.assertFalse(due)
        self.assertEqual(interval, 5)
        self.assertEqual(next_sync_at, datetime(2026, 1, 1, 12, 5, tzinfo=timezone.utc))

    def test_sync_attempt_marker_reuses_existing_state_row(self):
        db.session.add(ConsumptionSyncState(
            source_system=ZtkConsumptionSyncService.SOURCE_SYSTEM,
            cursor_source_record_id="1001",
        ))
        db.session.commit()

        _mark_sync_attempt_started(now=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc))

        self.assertEqual(ConsumptionSyncState.query.count(), 1)
        state = ConsumptionSyncState.query.one()
        self.assertEqual(state.cursor_source_record_id, "1001")
        self.assertEqual(state.last_synced_at, datetime(2026, 1, 1, 12, 0))

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
        self.assertEqual(float(record.amount), -7.5)
        self.assertEqual(record.transaction_id, "ztk:PaymentBooks:1001")
        # StaNum (结算台) is preferred over TerminalNum for the channel since
        # it maps to the camera watching that checkout station.
        self.assertEqual(record.channel_id, "9")
        self.assertEqual(record.import_batch, "ztk-test-batch")
        self.assertEqual(record.source_system, ZtkConsumptionSyncService.SOURCE_SYSTEM)
        self.assertEqual(record.source_record_id, "1001")
        self.assertEqual(record.source_payload["MonDeal"], -7.5)
        self.assertEqual(record.student_id, 1)

        state = ConsumptionSyncState.query.one()
        self.assertEqual(state.cursor_source_record_id, "1001")
        self.assertEqual(state.cursor_transaction_time.hour, 12)

    def test_sync_preserves_recharge_rows_as_positive_amounts(self):
        service, _ = self._service_with_pages([
            [{
                "RecID": 1010,
                "AccNum": 80000001,
                "CardCode": "C1001",
                "DealTime": datetime(2026, 6, 8, 12, 15, 30),
                "MonDeal": Decimal("20.00"),
                "TerminalNum": 3,
                "StaNum": 9,
            }]
        ])

        service.sync_once(batch_id="ztk-recharge-batch")

        record = ConsumptionRecord.query.one()
        self.assertEqual(float(record.amount), 20.0)
        self.assertEqual(record.source_payload["MonDeal"], 20.0)

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

    def test_sync_treats_zero_terminal_as_empty(self):
        # TerminalNum=0 means "unassigned" in the ZTK schema, not a real
        # channel: it must fall through to StaNum, and when that is also 0
        # the channel is left blank instead of showing a misleading "0".
        service, _ = self._service_with_pages([
            [{
                "RecID": 1003,
                "DealTime": datetime(2026, 6, 8, 12, 7, 30),
                "MonDeal": Decimal("-8.00"),
                "TerminalNum": 0,
                "StaNum": 9,
            }, {
                "RecID": 1004,
                "DealTime": datetime(2026, 6, 8, 12, 8, 30),
                "MonDeal": Decimal("-8.00"),
                "TerminalNum": 0,
                "StaNum": 0,
            }]
        ])

        service.sync_once(batch_id="ztk-zero-terminal")

        fallthrough = ConsumptionRecord.query.filter_by(
            transaction_id="ztk:PaymentBooks:1003"
        ).one()
        self.assertEqual(fallthrough.channel_id, "9")
        blank = ConsumptionRecord.query.filter_by(
            transaction_id="ztk:PaymentBooks:1004"
        ).one()
        self.assertIsNone(blank.channel_id)

    def test_sync_composes_channel_from_area_and_station(self):
        # Channel is "收费区-结算台" so stations are unique across dining halls;
        # when PayAreaNum is missing/0 it degrades to the station number alone.
        service, _ = self._service_with_pages([
            [{
                "RecID": 1006,
                "DealTime": datetime(2026, 6, 8, 12, 10, 30),
                "MonDeal": Decimal("-8.00"),
                "PayAreaNum": 1,
                "StaNum": 3,
                "TerminalNum": 0,
            }, {
                "RecID": 1007,
                "DealTime": datetime(2026, 6, 8, 12, 11, 30),
                "MonDeal": Decimal("-8.00"),
                "PayAreaNum": 0,
                "StaNum": 5,
                "TerminalNum": 0,
            }]
        ])

        service.sync_once(batch_id="ztk-composite-channel")

        composite = ConsumptionRecord.query.filter_by(
            transaction_id="ztk:PaymentBooks:1006"
        ).one()
        self.assertEqual(composite.channel_id, "1-3")
        station_only = ConsumptionRecord.query.filter_by(
            transaction_id="ztk:PaymentBooks:1007"
        ).one()
        self.assertEqual(station_only.channel_id, "5")

    def test_sync_surfaces_card_code_when_student_not_matched(self):
        # No Student row exists for this card, so the card code must still be
        # surfaced as student_no so the consumption list shows something.
        service, _ = self._service_with_pages([
            [{
                "RecID": 1005,
                "DealTime": datetime(2026, 6, 8, 12, 9, 30),
                "MonDeal": Decimal("-8.00"),
                "CardCode": "C7777",
                "TerminalNum": 3,
            }]
        ])

        service.sync_once(batch_id="ztk-cardcode-fallback")

        record = ConsumptionRecord.query.one()
        self.assertEqual(record.student_no, "C7777")
        self.assertIsNone(record.student_name)
        self.assertIsNone(record.student_id)

    def test_sync_enriches_name_and_certificate_from_accounts(self):
        service, _ = self._service_with_pages([[{
            "RecID": 1008,
            "DealTime": datetime(2026, 6, 8, 12, 10, 30),
            "MonDeal": Decimal("-9.00"),
            "CardCode": "C8888",
            "AccountName": "李四",
            "AccountPerCode": "20260008",
            "CertCode": "CERT-1008",
        }]])

        service.sync_once(batch_id="ztk-account-enrichment")

        record = ConsumptionRecord.query.one()
        self.assertEqual(record.student_name, "李四")
        self.assertEqual(record.student_no, "C8888")
        self.assertEqual(record.source_payload["AccountPerCode"], "20260008")
        self.assertEqual(record.source_payload["CertCode"], "CERT-1008")

    def test_select_wraps_chinese_text_columns_as_nvarchar(self):
        # Legacy SQL Server stores AccName/Remark as varchar in a CP936 collation.
        # Over a TDS 7.0 connection FreeTDS mis-decodes those GBK bytes to UTF-8,
        # garbling Chinese names. Forcing UCS-2 transport via CONVERT(NVARCHAR)
        # makes the server convert GBK->UCS-2 from its own collation, so FreeTDS
        # takes the unambiguous wide-char path and the name arrives intact.
        service, connection = self._service_with_pages([[]])
        service.sync_once(batch_id="ztk-nvarchar-test")
        sql = connection.cursor_obj.executions[0]["sql"]
        self.assertIn("CONVERT(NVARCHAR(255), account.AccName)", sql)
        self.assertIn("CONVERT(NVARCHAR(4000), p.Remark)", sql)

    def test_sync_continues_when_accounts_table_is_unavailable(self):
        service, connection = self._service_with_pages([[{
            "RecID": 1009,
            "DealTime": datetime(2026, 6, 8, 12, 11, 30),
            "MonDeal": Decimal("-6.00"),
            "CardCode": "C9999",
        }]], fail_accounts=True)

        result = service.sync_once(batch_id="ztk-no-accounts")

        self.assertEqual(result["imported"], 1)
        self.assertEqual(len(connection.cursor_obj.executions), 2)
        self.assertIn("ac_dict_Accounts", connection.cursor_obj.executions[0]["sql"])
        self.assertNotIn("ac_dict_Accounts", connection.cursor_obj.executions[1]["sql"])

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
            ZTK_ACCOUNTS_TABLE="dbo.AccountsCustom",
        )
        self.addCleanup(lambda: self.app.config.update(
            ZTK_PAYMENT_BOOKS_TABLE="dbo.view_ac_paymentbooks",
            ZTK_ACCOUNTS_TABLE="dbo.ac_dict_Accounts",
        ))
        service, connection = self._service_with_pages([[]])

        service.sync_once(batch_id="ztk-custom-table-test")

        sql = connection.cursor_obj.executions[0]["sql"]
        self.assertIn("FROM [dbo].[PaymentBooksCustom] p WITH (NOLOCK)", sql)
        self.assertIn("FROM [dbo].[AccountsCustom] a WITH (NOLOCK)", sql)

    def test_sync_rejects_invalid_configured_table_name(self):
        self.app.config["ZTK_PAYMENT_BOOKS_TABLE"] = "ac_PaymentBooks; DROP TABLE students"
        self.addCleanup(lambda: self.app.config.update(ZTK_PAYMENT_BOOKS_TABLE="dbo.view_ac_paymentbooks"))
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

    def test_connection_uses_legacy_sql_server_protocol(self):
        kwargs = ZtkConsumptionSyncService()._connection_kwargs()

        self.assertEqual(kwargs["tds_version"], "7.0")
        self.assertEqual(kwargs["encryption"], "off")


if __name__ == "__main__":
    unittest.main()
