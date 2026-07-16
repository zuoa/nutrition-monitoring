import os
import sys
import types
import unittest
from datetime import date

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

from app import db, init_app as register_cli_commands  # noqa: E402
import app.models  # noqa: F401,E402
from app.models import Dish, NutritionLog, Report, ReportPushLog, ReportTypeEnum, Student  # noqa: E402
from app.services.demo_data_service import DEMO_STUDENT_TEMPLATES, DemoDataService  # noqa: E402


class DemoDataServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask(__name__)
        cls.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SECRET_KEY="test-secret",
            REDIS_URL="redis://localhost:6379/0",
        )
        db.init_app(cls.app)
        register_cli_commands(cls.app)
        cls.app_context = cls.app.app_context()
        cls.app_context.push()
        db.create_all()

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        db.drop_all()
        cls.app_context.pop()

    def setUp(self):
        db.session.query(ReportPushLog).delete()
        db.session.query(Report).delete()
        db.session.query(NutritionLog).delete()
        db.session.query(Student).delete()
        db.session.query(Dish).delete()
        db.session.commit()

    def tearDown(self):
        db.session.rollback()

    def test_seed_historical_data_creates_students_logs_and_reports(self):
        svc = DemoDataService(today=date(2026, 4, 22), seed=123)

        summary = svc.seed_historical_data(weeks=6, report_weeks=3, student_prefix="TESTDEMO")

        student_count = len(DEMO_STUDENT_TEMPLATES)
        self.assertEqual(summary["student_count"], student_count)
        self.assertEqual(summary["class_count"], 2)
        self.assertEqual(summary["nutrition_log_count"], student_count * 42)
        self.assertEqual(summary["personal_report_count"], student_count * 3)
        self.assertEqual(summary["class_report_count"], 2 * 3)
        self.assertEqual(summary["latest_report_start"], "2026-04-13")
        self.assertEqual(summary["latest_report_end"], "2026-04-19")

        student = Student.query.filter_by(student_no="TESTDEMO001").first()
        self.assertIsNotNone(student)

        latest_report = Report.query.filter_by(
            report_type=ReportTypeEnum.personal_weekly,
            target_id=str(student.id),
        ).order_by(Report.created_at.desc()).first()

        self.assertIsNotNone(latest_report)
        self.assertEqual(latest_report.period_start.isoformat(), "2026-04-13")
        self.assertEqual(latest_report.period_end.isoformat(), "2026-04-19")
        self.assertEqual(latest_report.content["student_name"], student.name)
        self.assertEqual(latest_report.content["analysis_basis"], "period_daily_average")
        self.assertNotIn("meal_days", latest_report.content)
        self.assertNotIn("top_dishes", latest_report.content)
        self.assertGreater(latest_report.content["overall_score"], 0)

        class_report = Report.query.filter_by(
            report_type=ReportTypeEnum.class_weekly,
            target_id=str(student.class_id),
        ).order_by(Report.created_at.desc()).first()
        self.assertIsNotNone(class_report)
        self.assertEqual(class_report.content["student_count"], 5)
        self.assertEqual(class_report.content["analysis_basis"], "student_period_average")
        self.assertNotIn("flagged_students", class_report.content)

    def test_seed_historical_data_is_idempotent_for_same_prefix(self):
        svc = DemoDataService(today=date(2026, 4, 22), seed=456)
        svc.seed_historical_data(weeks=5, report_weeks=2, student_prefix="TESTDEMO")
        svc.seed_historical_data(weeks=5, report_weeks=2, student_prefix="TESTDEMO")

        student_count = len(DEMO_STUDENT_TEMPLATES)
        self.assertEqual(Student.query.filter(Student.student_no.like("TESTDEMO%")).count(), student_count)
        self.assertEqual(NutritionLog.query.count(), student_count * 35)
        self.assertEqual(
            Report.query.filter(Report.report_type == ReportTypeEnum.personal_weekly).count(),
            student_count * 2,
        )
        self.assertEqual(
            Report.query.filter(Report.report_type == ReportTypeEnum.class_weekly).count(),
            2 * 2,
        )

    def test_cli_command_seeds_demo_history(self):
        runner = self.app.test_cli_runner()

        result = runner.invoke(
            args=[
                "seed-demo-history",
                "--weeks",
                "4",
                "--report-weeks",
                "2",
                "--student-prefix",
                "CLI",
            ]
        )

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("已生成历史演示数据", result.output)
        self.assertIn("个人周报", result.output)
        self.assertEqual(Student.query.filter(Student.student_no.like("CLI%")).count(), len(DEMO_STUDENT_TEMPLATES))


if __name__ == "__main__":
    unittest.main()
