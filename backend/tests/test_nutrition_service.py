import os
import sys
import types
import unittest
from datetime import date, datetime, timedelta

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
from app.models import Campus, CapturedImage, CategoryEnum, Class, ConsumptionRecord, Dish, DishRecognition, Grade, MatchResult, MatchStatusEnum, NutritionLog, School, Stage, Student  # noqa: E402
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
        db.session.remove()
        db.session.query(MatchResult).delete()
        db.session.query(DishRecognition).delete()
        db.session.query(CapturedImage).delete()
        db.session.query(ConsumptionRecord).delete()
        db.session.query(Dish).delete()
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
        self.assertIsNone(report["overall_score"])
        self.assertEqual(report["report_version"], 2)
        self.assertEqual(report["coverage"]["nutrition_days"], 3)
        self.assertEqual(report["coverage"]["historical_quality_days"], 3)
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
        self.assertEqual(report["alerts"], [])
        self.assertIsNone(report["overall_score"])
        self.assertFalse(any("不足" in suggestion or "偏低" in suggestion for suggestion in report["suggestions"]))

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
                    nutrient_totals={"protein": 60, "_quality_version": 2, "_meal_slots": ["lunch"]},
                    meal_count=1,
                    dish_ids=[],
                ),
                NutritionLog(
                    student_id=second.id,
                    log_date=start + timedelta(days=offset),
                    nutrient_totals={"protein": 30, "_quality_version": 2, "_meal_slots": ["breakfast", "lunch", "dinner"]},
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
        self.assertEqual(report["meal_projection"]["avg_nutrients"]["protein"], 90)
        self.assertEqual(report["meal_projection"]["nutrient_sample_counts"]["protein"], 2)
        self.assertEqual(report["nutrient_distributions"]["protein"]["min"], 30)
        self.assertEqual(report["nutrient_distributions"]["protein"]["max"], 60)
        self.assertEqual(report["nutrient_sample_counts"]["protein"], 2)
        self.assertIsNone(report["average_score"])
        self.assertNotIn("low_rate", report["nutrient_distributions"]["protein"])
        self.assertNotIn("flagged_students", report)
        self.assertNotIn(first.name, str(report))
        self.assertNotIn(second.name, str(report))

    def test_missing_invalid_and_zero_values_have_separate_denominators(self):
        start, end = self._add_logs({"protein": 10, "calcium": 0})
        logs = NutritionLog.query.order_by(NutritionLog.log_date).all()
        logs[0].nutrient_totals = {"protein": None, "calcium": 0}
        logs[1].nutrient_totals = {"protein": -1, "calcium": 0}
        db.session.commit()
        report = NutritionService().generate_personal_report(self.student_id, start, end + timedelta(days=1))
        self.assertEqual(report["avg_nutrients"]["protein"], 10)
        self.assertEqual(report["nutrient_sample_counts"]["protein"], 1)
        self.assertEqual(report["nutrient_sample_counts"]["calcium"], 3)
        self.assertIsNone(report["daily_series"][-1]["nutrients"]["protein"])
        self.assertEqual(report["coverage"]["nutrition_days"], 3)

    def test_energy_chart_uses_common_complete_days(self):
        start, end = self._add_logs({"protein": 10, "fat": 10, "carbohydrate": 10})
        logs = NutritionLog.query.order_by(NutritionLog.log_date).all()
        logs[1].nutrient_totals = {"protein": 1000}
        logs[2].nutrient_totals = {"fat": 1000}
        db.session.commit()
        report = NutritionService().generate_personal_report(self.student_id, start, end)
        self.assertEqual(report["energy_summary"]["sample_count"], 1)
        self.assertEqual(report["energy_summary"]["percentages"], {"carbohydrate": 24, "fat": 52, "protein": 24})

    def test_daily_computation_preserves_zero_and_invalidates_partial_dish_values(self):
        day = date(2026, 7, 1)
        image = CapturedImage(capture_date=day, channel_id="test", captured_at=datetime(2026, 7, 1, 12), image_path="test.jpg")
        complete = Dish(name="完整菜品", price=1, category=CategoryEnum.meat, protein=10, calcium=0)
        partial = Dish(name="部分缺失菜品", price=1, category=CategoryEnum.vegetable, protein=None, calcium=0)
        db.session.add_all([image, complete, partial])
        db.session.flush()
        db.session.add_all([
            DishRecognition(image_id=image.id, dish_id=dish.id, dish_name_raw=dish.name, confidence=.99)
            for dish in (complete, partial)
        ])
        # Multiple transaction matches for the same image must not duplicate nutrients.
        for _ in range(2):
            db.session.add(MatchResult(student_id=self.student_id, match_date=day, image_id=image.id, status=MatchStatusEnum.confirmed))
        db.session.commit()
        log = NutritionService().compute_daily_log(self.student_id, day)
        self.assertIsNone(log.nutrient_totals["protein"])
        self.assertIsNone(log.nutrient_totals["calories"])
        self.assertEqual(log.nutrient_totals["calcium"], 0)
        self.assertEqual(log.meal_count, 1)
        self.assertEqual(log.nutrient_totals["_meal_slots"], ["lunch"])
        partial.protein = 5
        db.session.commit()
        log = NutritionService().compute_daily_log(self.student_id, day)
        self.assertEqual(log.nutrient_totals["protein"], 15)

    def test_empty_period_has_no_invented_score_or_nutrition(self):
        report = NutritionService().generate_personal_report(self.student_id, date(2026, 7, 1), date(2026, 7, 7))
        self.assertEqual(report["coverage"]["nutrition_days"], 0)
        self.assertTrue(all(value is None for value in report["avg_nutrients"].values()))
        self.assertEqual(len(report["daily_series"]), 7)
        self.assertIsNone(report["energy_summary"]["percentages"])

    def test_legacy_day_is_projected_from_source_without_rewriting_its_totals(self):
        day = date(2026, 7, 1)
        historical = NutritionLog(student_id=self.student_id, log_date=day, meal_count=1, nutrient_totals={"protein": 1})
        image = CapturedImage(capture_date=day, channel_id="test", captured_at=datetime(2026, 7, 1, 12), image_path="test.jpg")
        dish = Dish(name="午餐", price=1, category=CategoryEnum.meat, protein=12)
        db.session.add_all([historical, image, dish])
        db.session.flush()
        db.session.add(DishRecognition(image_id=image.id, dish_id=dish.id, dish_name_raw=dish.name, confidence=.99))
        db.session.add(MatchResult(student_id=self.student_id, match_date=day, image_id=image.id, status=MatchStatusEnum.confirmed))
        db.session.commit()
        report = NutritionService().generate_personal_report(self.student_id, day, day)
        self.assertEqual(report["meal_projection"]["avg_nutrients"]["protein"], 30)
        self.assertEqual(report["meal_projection"]["nutrient_weight_sums"]["protein"], .4)
        self.assertEqual(report["avg_nutrients"]["protein"], 1)
        self.assertEqual(db.session.get(NutritionLog, historical.id).nutrient_totals, {"protein": 1})

    def test_incomplete_breakfast_does_not_hide_complete_lunch_for_projection(self):
        day = date(2026, 7, 1)
        for hour, protein in ((7, None), (12, 12)):
            image = CapturedImage(capture_date=day, channel_id="test", captured_at=datetime(2026, 7, 1, hour), image_path=f"{hour}.jpg")
            dish = Dish(name=f"{hour}点菜品", price=1, category=CategoryEnum.meat, protein=protein)
            db.session.add_all([image, dish])
            db.session.flush()
            db.session.add(DishRecognition(image_id=image.id, dish_id=dish.id, dish_name_raw=dish.name, confidence=.99))
            db.session.add(MatchResult(student_id=self.student_id, match_date=day, image_id=image.id, status=MatchStatusEnum.confirmed))
        db.session.commit()
        log = NutritionService().compute_daily_log(self.student_id, day)
        self.assertEqual(log.nutrient_totals["_quality_version"], 3)
        self.assertIsNone(log.nutrient_totals["protein"])
        report = NutritionService().generate_personal_report(self.student_id, day, day)
        self.assertEqual(report["meal_projection"]["avg_nutrients"]["protein"], 30)
        self.assertEqual(report["meal_projection"]["nutrient_meal_counts"]["protein"], 1)


if __name__ == "__main__":
    unittest.main()
