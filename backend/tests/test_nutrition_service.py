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
from app.models import Campus, Class, Grade, NutritionLog, School, Stage, Student  # noqa: E402
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
        db.session.query(Class).delete()
        db.session.query(Grade).delete()
        db.session.query(Stage).delete()
        db.session.query(Campus).delete()
        db.session.query(School).delete()
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
        self.assertEqual(report["analysis_basis"], "period_daily_average")
        self.assertNotIn("meal_days", report)
        self.assertNotIn("total_days", report)
        self.assertNotIn("top_dishes", report)

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

    def test_group_report_exposes_distributions_without_individual_details(self):
        school = School(name="测试学校")
        db.session.add(school)
        db.session.flush()
        campus = Campus(name="东校区", school_id=school.id)
        db.session.add(campus)
        db.session.flush()
        stage = Stage(name="初中部", campus_id=campus.id)
        db.session.add(stage)
        db.session.flush()
        grade = Grade(name="七年级", stage_id=stage.id)
        db.session.add(grade)
        db.session.flush()
        class_ = Class(name="七年级一班", grade_id=grade.id)
        db.session.add(class_)
        db.session.flush()

        first = db.session.get(Student, self.student_id)
        first.class_id = class_.id
        second = Student(student_no="S002", name="另一名学生", class_id=class_.id, is_active=True)
        db.session.add(second)
        db.session.flush()

        start = date(2026, 7, 1)
        for offset in range(3):
            db.session.add_all([
                NutritionLog(
                    student_id=first.id,
                    log_date=start + timedelta(days=offset),
                    nutrient_totals={"protein": 60},
                    meal_count=1,
                    dish_ids=[],
                ),
                NutritionLog(
                    student_id=second.id,
                    log_date=start + timedelta(days=offset),
                    nutrient_totals={"protein": 30},
                    meal_count=1,
                    dish_ids=[],
                ),
            ])
        db.session.commit()

        report = NutritionService().generate_group_report("campus", campus.id, start, start + timedelta(days=2))

        self.assertEqual(report["analysis_basis"], "student_period_average")
        self.assertEqual(report["student_count"], 2)
        self.assertEqual(report["students_with_data"], 2)
        self.assertEqual(report["avg_nutrients"]["protein"], 45)
        self.assertEqual(report["nutrient_distributions"]["protein"]["ok"], 1)
        self.assertEqual(report["nutrient_distributions"]["protein"]["low"], 1)
        self.assertNotIn("flagged_students", report)
        self.assertNotIn(first.name, str(report))
        self.assertNotIn(second.name, str(report))


if __name__ == "__main__":
    unittest.main()
