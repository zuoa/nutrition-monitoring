from copy import deepcopy

import pytest

from app.services.nutrition_service import NutritionService
from app.services.report_assessment import ASSESSMENT_VERSION, assess_personal_report
from app.services.report_assessment_ranges import DEFAULT_ASSESSMENT_RANGES
from app.services.report_assessment_scoring import DEFAULT_ASSESSMENT_SCORING
from app.services.runtime_config import persist_runtime_overrides
from tests.test_report_assessment import content
from tests.test_report_nutrient_gains import DAY, report_db as nutrient_report_db

report_db = nutrient_report_db


@pytest.mark.parametrize("protein,status,points", [
    (0, "偏低", 5), (53.9, "偏低", 5), (54, "轻微偏低", 15), (60, "达标", 20),
    (90, "达标", 20), (99, "轻微偏高", 15), (99.1, "偏高", 5), (120, "偏高", 5),
])
def test_status_points_use_existing_reference_and_ten_percent_boundary(protein, status, points):
    data = content()
    data["report_assessment_scoring"] = deepcopy(DEFAULT_ASSESSMENT_SCORING)
    data["meal_projection"]["avg_nutrients"]["protein"] = protein
    result = assess_personal_report(data)
    field = next(field for field in result["fields"] if field["key"] == "protein")
    assert (field["status"], field["score"]) == (status, points)
    assert result["score"] == 80 + points
    assert result["version"] == ASSESSMENT_VERSION


@pytest.mark.parametrize("values,score,level,label", [
    ([0, 0, 0, 0, 0], 25, "D", "重点关注"),
    ([2900, 54, 110, 0, 0], 50, "D", "重点关注"),
    ([2900, 75, 0, 0, 0], 55, "C", "需改善"),
    ([2900, 75, 80, 0, 0], 70, "B", "基本达标"),
    ([2900, 75, 80, 0, 24], 80, "A", "达标良好"),
    ([2900, 75, 80, 400, 25], 100, "A", "达标良好"),
])
def test_sum_and_default_grade_boundaries(values, score, level, label):
    data = content()
    data["report_assessment_scoring"] = deepcopy(DEFAULT_ASSESSMENT_SCORING)
    data["meal_projection"]["avg_nutrients"].update(zip(("calories", "protein", "fat", "carbohydrate", "fiber"), values))
    result = assess_personal_report(data)
    assert (result["score"], result["level"], result["label"]) == (score, level, label)
    assert sum(field["score"] for field in result["fields"] if field["score"] is not None) == score


@pytest.mark.parametrize("boundaries,level", [
    ({"A": 84, "B": 72, "C": 56}, "A"),
    ({"A": 85, "B": 84, "C": 56}, "B"),
    ({"A": 90, "B": 85, "C": 84}, "C"),
    ({"A": 90, "B": 87, "C": 85}, "D"),
])
def test_custom_starts_include_their_minimum(boundaries, level):
    data = content()
    data["report_assessment_scoring"] = {
        "status_points": {**DEFAULT_ASSESSMENT_SCORING["status_points"], "slightly_low": 4},
        "grade_min_scores": boundaries,
    }
    data["meal_projection"]["avg_nutrients"]["fiber"] = 23
    result = assess_personal_report(data)
    assert (result["score"], result["level"]) == (84, level)


@pytest.mark.parametrize("missing", ["fiber", "reference"])
def test_incomplete_inputs_do_not_get_a_total_or_grade(missing):
    data = content()
    data["report_assessment_scoring"] = deepcopy(DEFAULT_ASSESSMENT_SCORING)
    if missing == "fiber":
        del data["meal_projection"]["avg_nutrients"]["fiber"]
    else:
        data["age"] = 18
    result = assess_personal_report(data)
    assert result["score"] is result["level"] is None
    assert result["label"] == "待评价"


def test_group_grades_match_personal_reports_under_the_same_snapshot(report_db):
    service = NutritionService()

    def generate():
        return service.generate_personal_report(report_db.student.id, DAY, DAY), [
            service.generate_group_report(scope, getattr(report_db, attribute).id, DAY, DAY)
            for scope, attribute in (("class", "class_"), ("grade", "grade"), ("campus", "campus"))
        ]

    persist_runtime_overrides(report_db.app.config, {"REPORT_ASSESSMENT_RANGES": {**DEFAULT_ASSESSMENT_RANGES, "enabled": True}})
    original, groups = generate()
    assert original["report_assessment_scoring"] == DEFAULT_ASSESSMENT_SCORING
    custom = {
        "status_points": {**DEFAULT_ASSESSMENT_SCORING["status_points"], "low": 8},
        "grade_min_scores": {"A": 92, "B": 87, "C": 54},
    }
    persist_runtime_overrides(report_db.app.config, {"REPORT_ASSESSMENT_SCORING": custom})
    updated, new_groups = generate()
    for report, group_reports, rules, grade in [(original, groups, DEFAULT_ASSESSMENT_SCORING, "A"), (updated, new_groups, custom, "B")]:
        assert assess_personal_report(report)["level"] == grade
        for group in group_reports:
            assert group["report_assessment_scoring"] == rules
            assert [row["level"] for row in group["health_summary"]["distribution"] if row["count"]] == [grade]
