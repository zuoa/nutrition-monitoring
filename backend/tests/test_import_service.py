import os
import sys
import types
import unittest
from datetime import datetime

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

if "chardet" not in sys.modules:
    chardet = types.ModuleType("chardet")
    chardet.detect = lambda content: {"encoding": "gbk"}
    sys.modules["chardet"] = chardet

from app import db  # noqa: E402
import app.models  # noqa: F401,E402
from app.models import ConsumptionRecord, Student  # noqa: E402
from app.services.import_service import ConsumptionImportService  # noqa: E402


class ConsumptionImportServiceTests(unittest.TestCase):
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
        ConsumptionRecord.query.delete()
        Student.query.delete()
        db.session.commit()

    def tearDown(self):
        db.session.rollback()

    def test_suggest_mapping_supports_card_account_export_columns(self):
        svc = ConsumptionImportService()

        mapping = svc._suggest_mapping(["帐号", "姓名", "交易金额", "钱包流水号", "交易时间"])

        self.assertEqual(
            mapping,
            {
                "student_id": "帐号",
                "student_name": "姓名",
                "transaction_time": "交易时间",
                "amount": "交易金额",
                "transaction_id": "钱包流水号",
            },
        )

    def test_import_file_normalizes_negative_amount_and_wallet_serial(self):
        db.session.add(Student(student_no="230502", name="柴浚尘", class_id="2023-8"))
        db.session.commit()

        content = (
            "帐号,姓名,交易金额,钱包流水号,交易时间\n"
            "230502,柴浚尘,-7,3794,2026/03/24 06:12:20\t\n"
        ).encode("gbk")

        svc = ConsumptionImportService()
        result = svc.import_file(content, "csv", "batch001")

        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["skipped_duplicates"], 0)
        self.assertEqual(result["errors"], [])

        record = ConsumptionRecord.query.one()
        self.assertEqual(record.student_no, "230502")
        self.assertEqual(record.student_name, "柴浚尘")
        self.assertEqual(float(record.amount), 7.0)
        self.assertEqual(record.transaction_id, "wallet:230502:3794")
        self.assertIsNotNone(record.student_id)

    def test_import_file_skips_duplicate_when_legacy_raw_wallet_id_exists(self):
        db.session.add(
            ConsumptionRecord(
                student_no="230502",
                student_name="柴浚尘",
                transaction_time=datetime(2026, 3, 24, 6, 12, 20),
                amount=7.0,
                transaction_id="3794",
                import_batch="legacy001",
            )
        )
        db.session.commit()

        content = (
            "帐号,姓名,交易金额,钱包流水号,交易时间\n"
            "230502,柴浚尘,-7,3794,2026/03/24 06:12:20\t\n"
        ).encode("gbk")

        svc = ConsumptionImportService()
        result = svc.import_file(content, "csv", "batch002")

        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["skipped_duplicates"], 1)
        self.assertEqual(ConsumptionRecord.query.count(), 1)

    def test_import_file_skips_duplicate_when_old_timestamp_wallet_id_exists(self):
        db.session.add(
            ConsumptionRecord(
                student_no="230502",
                student_name="柴浚尘",
                transaction_time=datetime(2026, 3, 24, 6, 12, 20),
                amount=7.0,
                transaction_id="wallet:230502:20260324061220:3794",
                import_batch="legacy002",
            )
        )
        db.session.commit()

        content = (
            "帐号,姓名,交易金额,钱包流水号,交易时间\n"
            "230502,柴浚尘,-7,3794,2026/03/24 06:12\n"
        ).encode("gbk")

        svc = ConsumptionImportService()
        result = svc.import_file(content, "csv", "batch003")

        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["skipped_duplicates"], 1)
        self.assertEqual(ConsumptionRecord.query.count(), 1)


if __name__ == "__main__":
    unittest.main()
