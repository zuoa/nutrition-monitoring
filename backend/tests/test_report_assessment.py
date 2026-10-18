from datetime import date

import pytest

from app.services.report_assessment import age_from_grade, assess_personal_report, reference_profile
from app.services.report_presentation import personal_report_view
from app.services.report_reference import reference_snapshot


def content():
    return {**reference_snapshot(), "student_name": "测试学生", "period_start": "2026-10-12", "period_end": "2026-10-18",
            "age": 15, "gender": "男", "avg_nutrients": {"calories": 1000},
            "meal_projection": {"avg_nutrients": {"calories": 2900, "protein": 75, "fat": 80,
                                                  "cholesterol": 350, "carbohydrate": 400, "fiber": 25}}}


@pytest.mark.parametrize("grade,stage,age", [
    ("一年级", "primary", 6), ("三年级", "primary", 8), ("四年级", "primary", 9), ("六年级", "primary", 11),
    ("七年级", "junior", 12), ("初一", None, 12), ("初三年级", None, 14), ("九年级", None, 14),
    ("高一（5）班", None, 15), ("高三", None, 17), ("一年级", "senior", 15), ("Grade 10", None, 15),
    ("毕业班", None, None), ("2026级", None, None),
])
def test_grade_age_mapping(grade, stage, age):
    assert age_from_grade(grade, stage) == age


def test_birth_date_takes_precedence_and_age_is_calculated_at_report_end():
    values = dict(birth_date="2017-10-18", age=17, grade_name="高三", gender="female")
    profile = reference_profile(as_of=date(2026, 10, 17), **values)
    assert profile["age"] == 8 and profile["age_group"] == "6—8岁"
    assert profile["age_source"] == "birth_date" and profile["gender"] == "女"
    profile = reference_profile(as_of=date(2026, 10, 18), **values)
    assert profile["age"] == 9 and profile["age_group"] == "9—11岁"
    profile = reference_profile(as_of="2026-10-18", age=12, grade_name="高三", gender="1")
    assert profile["age"] == 12 and profile["age_source"] == "age"
    profile = reference_profile(as_of="2026-10-18", grade_name="高三", gender="2")
    assert profile["age"] == 17 and profile["age_source"] == "grade"


@pytest.mark.parametrize("changes,level,passed", [
    ({}, "A", 5), ({"protein": 0}, "B", 4), ({"protein": 0, "fiber": 0}, "C", 3),
    ({"protein": 0, "fiber": 0, "fat": 0}, "D", 2),
])
def test_levels_use_final_values_and_cholesterol_does_not_invent_a_threshold(changes, level, passed):
    data = content()
    data["meal_projection"]["avg_nutrients"].update(changes)
    result = assess_personal_report(data)
    assert result["level"] == level and result["passed_count"] == passed
    cholesterol = next(field for field in result["fields"] if field["key"] == "cholesterol")
    assert cholesterol["status"] == "仅展示" and cholesterol["upper"] is None


@pytest.mark.parametrize("key,value,status", [
    ("calories", 2610, "达标"), ("calories", 3190, "达标"), ("calories", 2609, "偏低"), ("calories", 3191, "偏高"),
    ("protein", 60, "达标"), ("protein", 90, "达标"), ("protein", 59.9, "偏低"), ("protein", 90.1, "偏高"),
    ("fat", 64.4, "达标"), ("fat", 96.7, "达标"), ("fat", 96.8, "偏高"),
    ("carbohydrate", 362.5, "达标"), ("carbohydrate", 471.2, "达标"), ("carbohydrate", 471.3, "偏高"),
    ("fiber", 25, "达标"), ("fiber", 100, "达标"), ("fiber", 24.9, "偏低"),
])
def test_reference_boundaries_and_one_sided_fiber_reference(key, value, status):
    data = content()
    data["meal_projection"]["avg_nutrients"][key] = value
    result = assess_personal_report(data)
    assert next(field for field in result["fields"] if field["key"] == key)["status"] == status


@pytest.mark.parametrize("key,value", [("protein", None), ("fat", float("nan")), ("calories", -1), ("fiber", float("inf"))])
def test_incomplete_data_never_receives_a_level(key, value):
    data = content()
    data["meal_projection"]["avg_nutrients"][key] = value
    assert assess_personal_report(data)["level"] is None


def test_missing_demographics_and_school_only_values_are_not_marked_as_achieved():
    data = content()
    data["gender"] = None
    assert assess_personal_report(data)["reason"] == "请补充学生性别"
    data["gender"] = "男"
    data["age"] = 18
    assert assess_personal_report(data)["level"] is None
    data["age"] = 15
    data.pop("meal_projection")
    assert assess_personal_report(data)["level"] is None


def test_reference_markers_use_final_values_preserve_zero_and_remain_inside_plot():
    data = content()
    data["meal_projection"]["avg_nutrients"].update(calories=10000, cholesterol=0, protein=None)
    view = personal_report_view(data)
    charts = {chart["key"]: chart for chart in view["reference_charts"]}
    assert charts["calories"]["result"] == "10000"
    assert 0 < charts["calories"]["value_x"] < 100
    assert charts["cholesterol"]["value_x"] == 0
    assert charts["protein"]["value_x"] is None
