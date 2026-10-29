from copy import deepcopy
from datetime import date
from types import SimpleNamespace

from flask import Flask
import pytest

from app import db
from app.models import Campus, Class, Grade, NutritionLog, School, Stage, Student
from app.services.nutrition_service import NutritionService, _energy_summary
from app.services.report_assessment import assess_personal_report
from app.services.report_brief import brief_report_view
from app.services.report_meal_completion import complete_report_meals, enrich_report_meals
from app.services.report_nutrient_gains import REPORT_GAIN_KEYS, adjust_report_logs, get_report_nutrient_gains
from app.services.runtime_config import persist_runtime_overrides

DAY = date(2026, 10, 19)
VALUES = {"calories": 800, "protein": 24, "carbohydrate": 100, "fiber": 8, "fat": 20, "calcium": 200, "cholesterol": 80, "sodium": 400}
GAINS = {"calories": 1.5, "protein": 2, "carbohydrate": 1.2, "fiber": 1.25, "fat": .75, "calcium": 2.5}


@pytest.fixture
def report_db(tmp_path):
    app = Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI="sqlite:///:memory:", SQLALCHEMY_TRACK_MODIFICATIONS=False,
                      APP_TIMEZONE="Asia/Shanghai", LOCAL_RUNTIME_CONFIG_PATH=str(tmp_path / "runtime.json"))
    db.init_app(app)
    with app.app_context():
        db.create_all()
        school = School(name="测试学校")
        db.session.add(school)
        db.session.flush()
        campus = Campus(name="校区", school_id=school.id)
        db.session.add(campus)
        db.session.flush()
        stage = Stage(name="高中", campus_id=campus.id)
        db.session.add(stage)
        db.session.flush()
        grade = Grade(name="高二", stage_id=stage.id)
        db.session.add(grade)
        db.session.flush()
        class_ = Class(name="一班", grade_id=grade.id)
        db.session.add(class_)
        db.session.flush()
        student = Student(student_no="gain-1", name="测试学生", class_id=class_.id, age=16, gender="男", is_active=True)
        db.session.add(student)
        db.session.flush()
        totals = {**VALUES, "_quality_version": 3, "_meal_slots": ["lunch"],
                  "_meals": [{"slot": "lunch", "nutrients": dict(VALUES)}]}
        log = NutritionLog(student_id=student.id, log_date=DAY, nutrient_totals=totals, meal_count=1, dish_ids=[])
        db.session.add(log)
        db.session.commit()
        yield SimpleNamespace(app=app, student=student, class_=class_, grade=grade, campus=campus, log=log)
        db.session.remove()
        db.drop_all()


def test_adjustment_preserves_inputs_missing_values_zero_and_provenance():
    values = {**VALUES, "protein": None, "fiber": 0, "fat": -1, "calcium": float("inf")}
    totals = {
        **values, "_quality_version": 3,
        "_meals": [{"slot": "lunch", "nutrients": dict(values), "estimated_nutrients": {"calories": "personal_history"}}],
    }
    original = deepcopy(totals)
    log = SimpleNamespace(log_date=DAY, nutrient_totals=totals, meal_count=1)
    adjusted = adjust_report_logs([log], GAINS)[0]
    assert totals == original
    assert adjusted.nutrient_totals["calories"] == 1200
    assert adjusted.nutrient_totals["protein"] is None
    assert adjusted.nutrient_totals["fiber"] == 0
    assert adjusted.nutrient_totals["fat"] == -1
    assert adjusted.nutrient_totals["sodium"] == 400
    assert adjusted.nutrient_totals["_meals"][0]["nutrients"]["calories"] == 1200
    assert adjusted.nutrient_totals["_meals"][0]["estimated_nutrients"] == {"calories": "personal_history"}
    assert adjust_report_logs([log], dict.fromkeys(REPORT_GAIN_KEYS, 1))[0].nutrient_totals == original


def test_personal_reports_apply_all_factors_once_before_assessment(report_db):
    original = deepcopy(report_db.log.nutrient_totals)
    service = NutritionService()
    baseline = service.generate_personal_report(report_db.student.id, DAY, DAY)
    persist_runtime_overrides(report_db.app.config, {"REPORT_NUTRIENT_GAINS": GAINS})
    report = service.generate_personal_report(report_db.student.id, DAY, DAY)
    for key, factor in GAINS.items():
        assert baseline["avg_nutrients"][key] == VALUES[key]
        assert report["avg_nutrients"][key] == round(VALUES[key] * factor, 1)
        assert report["meal_projection"]["avg_nutrients"][key] == round(VALUES[key] * factor / .4, 1)
        assert report["daily_series"][0]["nutrients"][key] == round(VALUES[key] * factor, 1)
        assert report["meal_projection"]["daily_series"][0]["nutrients"][key] == round(VALUES[key] * factor / .4, 1)
    assert report["avg_nutrients"]["cholesterol"] == VALUES["cholesterol"]
    assert report["report_nutrient_gains"] == GAINS
    assert report["nutrition_assessment"] == assess_personal_report(report)
    assert report["nutrition_assessment"] != baseline["nutrition_assessment"]
    expected = SimpleNamespace(nutrient_totals={key: value * GAINS.get(key, 1) for key, value in VALUES.items()})
    assert report["energy_summary"] == _energy_summary([expected])
    calcium = next(field for field in brief_report_view(report)["core"] if field["key"] == "calcium")
    assert calcium["value"] == 1250
    assert service.generate_personal_report(report_db.student.id, DAY, DAY)["avg_nutrients"] == report["avg_nutrients"]
    db.session.expire_all()
    assert report_db.log.nutrient_totals == original
    assert baseline["report_nutrient_gains"] == dict.fromkeys(REPORT_GAIN_KEYS, 1)


@pytest.mark.parametrize("scope", ["class", "grade", "campus"])
def test_group_reports_use_same_adjusted_values_and_counts(report_db, scope):
    persist_runtime_overrides(report_db.app.config, {"REPORT_NUTRIENT_GAINS": GAINS})
    target = report_db.class_ if scope == "class" else getattr(report_db, scope)
    report = NutritionService().generate_group_report(scope, target.id, DAY, DAY)
    for key, factor in GAINS.items():
        observed = round(VALUES[key] * factor, 1)
        projected = round(VALUES[key] * factor / .4, 1)
        assert report["avg_nutrients"][key] == observed
        assert report["cohorts"][0]["nutrients"][key] == observed
        assert report["meal_projection"]["avg_nutrients"][key] == projected
        assert report["cohorts"][0]["meal_projection"]["avg_nutrients"][key] == projected
        assert report["nutrient_distributions"][key]["min"] == observed
        assert report["daily_series"][0]["nutrients"][key] == observed
        assert report["nutrient_sample_counts"][key] == 1
    assert report["group_analysis"]["avg_nutrients"]["calories"] == 2250
    core = {field["key"]: field for field in report["health_summary"]["core"]}
    assert core["calcium"]["value"] == 1250
    assert core["protein"]["value"] == 120
    assert report["report_nutrient_gains"] == GAINS
    db.session.expire_all()
    assert report_db.log.nutrient_totals["calories"] == VALUES["calories"]


def test_meal_enrichment_keeps_report_snapshot_instead_of_current_config(report_db):
    persist_runtime_overrides(report_db.app.config, {"REPORT_NUTRIENT_GAINS": GAINS})
    service = NutritionService()
    content = service.generate_personal_report(report_db.student.id, DAY, DAY)
    expected = deepcopy(content)
    persist_runtime_overrides(report_db.app.config, {"REPORT_NUTRIENT_GAINS": dict.fromkeys(REPORT_GAIN_KEYS, 3)})
    completion = complete_report_meals([report_db.student.id], DAY, DAY, report_db.app.config)
    enrich_report_meals(content, completion, report_db.student.id)
    assert content["meal_projection"] == expected["meal_projection"]
    assert content["daily_series"] == expected["daily_series"]
    assert content["energy_summary"] == expected["energy_summary"]
    legacy = deepcopy(expected)
    legacy.pop("report_nutrient_gains")
    legacy["avg_nutrients"] = {}
    enrich_report_meals(legacy, completion, report_db.student.id)
    assert legacy["meal_projection"]["avg_nutrients"]["calories"] == 2000


def test_invalid_runtime_gains_fall_back_to_one():
    configured = {"protein": True, "fat": float("nan"), "fiber": -1, "calcium": "2", "calories": 2}
    assert get_report_nutrient_gains({"REPORT_NUTRIENT_GAINS": configured}) == {
        **dict.fromkeys(REPORT_GAIN_KEYS, 1), "calories": 2,
    }
