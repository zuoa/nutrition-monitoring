import os
import sys
import types
import unittest
from datetime import date, timedelta

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

from app import db  # noqa: E402
import app.models  # noqa: F401,E402
from app.models import NutritionLog, Student  # noqa: E402
from app.services.nutrition_service import NutritionService  # noqa: E402


class NutritionServiceReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask(__name__)
        cls.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SECRET_KEY="test-secret",
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
        db.session.query(NutritionLog).delete()
        db.session.query(Student).delete()
        db.session.commit()

        student = Student(student_no="S001", name="测试学生", class_name="测试班", is_active=True)
        db.session.add(student)
        db.session.commit()
        self.student_id = student.id

    def tearDown(self):
        db.session.rollback()

    def _add_logs(self, nutrients: dict, *, days: int = 3):
        start = date(2026, 7, 1)
        for offset in range(days):
            db.session.add(
                NutritionLog(
                    student_id=self.student_id,
                    log_date=start + timedelta(days=offset),
                    nutrient_totals=dict(nutrients),
                    meal_count=1,
                    dish_ids=[],
                )
            )
        db.session.commit()
        return start, start + timedelta(days=days - 1)

    def test_report_skips_missing_historical_nutrients(self):
        start, end = self._add_logs({
            "calories": 2000,
            "protein": 60,
            "fat": 65,
            "carbohydrate": 275,
            "sodium": 1500,
            "fiber": 25,
        })

        report = NutritionService().generate_personal_report(self.student_id, start, end)

        self.assertIsNone(report["avg_nutrients"]["calcium"])
        self.assertEqual(report["nutrient_sample_counts"]["calcium"], 0)
        self.assertFalse(any(alert.get("nutrient") == "calcium" for alert in report["alerts"]))
        self.assertFalse(any("钙" in suggestion for suggestion in report["suggestions"]))
        self.assertGreaterEqual(report["overall_score"], 95)

    def test_report_treats_present_zero_nutrient_as_measured(self):
        start, end = self._add_logs({
            "calories": 2000,
            "protein": 60,
            "fat": 65,
            "carbohydrate": 275,
            "sodium": 1500,
            "fiber": 25,
            "calcium": 0,
        })

        report = NutritionService().generate_personal_report(self.student_id, start, end)

        self.assertEqual(report["avg_nutrients"]["calcium"], 0)
        self.assertEqual(report["nutrient_sample_counts"]["calcium"], 3)
        self.assertTrue(any(alert.get("nutrient") == "calcium" for alert in report["alerts"]))
        self.assertTrue(any("钙" in suggestion for suggestion in report["suggestions"]))


if __name__ == "__main__":
    unittest.main()

