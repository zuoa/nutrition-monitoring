from copy import deepcopy

import pytest

from app.services.nutrition_service import NutritionService
from app.services.report_assessment import assess_personal_report
from app.services.report_assessment_ranges import DEFAULT_ASSESSMENT_RANGES, validate_report_assessment_ranges
from app.services.report_presentation import render_personal_html
from app.services.runtime_config import persist_runtime_overrides
from tests.test_report_assessment import content
from tests.test_report_nutrient_gains import DAY, report_db as nutrient_report_db

report_db = nutrient_report_db


@pytest.mark.parametrize("value", [
    None, [], "fixed", {"enabled": 1}, {"other": True}, {"nutrients": []}, {"nutrients": {"sodium": {}}},
    {"nutrients": {"calories": {"lower": True, "upper": 2200}}},
    {"nutrients": {"calories": {"lower": -1, "upper": 2200}}},
    {"nutrients": {"calories": {"lower": float("nan"), "upper": 2200}}},
    {"nutrients": {"calories": {"lower": 2200, "upper": 1800}}},
    {"nutrients": {"calories": {"lower": 1800, "upper": float("inf")}}},
    {"nutrients": {"fiber": {"lower": 25}}},
])
def test_invalid_ranges_cannot_be_saved(value):
    with pytest.raises(ValueError):
        validate_report_assessment_ranges(value)


def test_fixed_ranges_evaluate_without_demographics_and_never_follow_gains():
    data = content()
    data.update(age=None, gender=None, report_assessment_ranges={**deepcopy(DEFAULT_ASSESSMENT_RANGES), "enabled": True})
    data["meal_projection"]["avg_nutrients"].update(calories=2000, protein=60, fat=55, carbohydrate=280, fiber=25)
    baseline = assess_personal_report(data)
    assert baseline["level"] == "A" and baseline["score"] == 100
    assert baseline["reference_source"] == "fixed_ranges" and baseline["reference_energy"] == 2000
    data["report_nutrient_gains"] = {"calories": 2}
    data["meal_projection"]["avg_nutrients"]["calories"] = 2391.6
    updated = assess_personal_report(data)
    assert [(field["lower"], field["upper"]) for field in updated["fields"]] == [(field["lower"], field["upper"]) for field in baseline["fields"]]
    assert next(field for field in updated["fields"] if field["key"] == "calories")["status"] == "轻微偏高"


@pytest.mark.parametrize("scope", ["personal", "class", "grade", "campus"])
def test_generating_reports_snapshots_ranges_separately_from_gains(report_db, scope):
    report_db.student.gender = None
    rules = deepcopy(DEFAULT_ASSESSMENT_RANGES)
    rules["enabled"] = True
    rules["nutrients"]["fiber"]["lower"] = 20
    persist_runtime_overrides(report_db.app.config, {"REPORT_ASSESSMENT_RANGES": rules})
    service = NutritionService()
    target = report_db.student if scope == "personal" else report_db.class_ if scope == "class" else getattr(report_db, scope)

    def generate():
        if scope == "personal":
            return service.generate_personal_report(target.id, DAY, DAY)
        return service.generate_group_report(scope, target.id, DAY, DAY)
    baseline = generate()
    persist_runtime_overrides(report_db.app.config, {"REPORT_NUTRIENT_GAINS": {"calories": 2}})
    updated = generate()
    assert baseline["report_assessment_ranges"] == updated["report_assessment_ranges"] == rules
    assert updated["avg_nutrients"]["calories"] == baseline["avg_nutrients"]["calories"] * 2
    fields = updated["nutrition_assessment"]["fields"] if scope == "personal" else updated["health_summary"]["core"]
    energy = next(field for field in fields if field["key"] == "calories")
    assert (energy["lower"], energy["upper"]) == (1800, 2200)
    if scope != "personal":
        reference = updated["group_analysis"]["reference"]
        assert (reference["calories_lower"], reference["calories_upper"]) == (1350, 1650)
        assert "后台配置" in updated["group_analysis"]["compliance_note"]
    persist_runtime_overrides(report_db.app.config, {"REPORT_ASSESSMENT_RANGES": {"enabled": False}})
    assert baseline["report_assessment_ranges"] == rules
    if scope == "personal":
        assert assess_personal_report(baseline)["reference_source"] == "fixed_ranges"
        for report_format in ("brief", "detailed"):
            html = render_personal_html(updated, report_format=report_format)
            assert "后台配置的固定达标区间" in html and "参考 1800–2200 kcal/天" in html


def test_upper_bound_can_be_disabled_without_mutating_defaults():
    original = deepcopy(DEFAULT_ASSESSMENT_RANGES)
    result = validate_report_assessment_ranges({"enabled": True, "nutrients": {"fiber": {"lower": 0, "upper": None}}})
    assert result["nutrients"]["fiber"] == {"lower": 0, "upper": None}
    assert DEFAULT_ASSESSMENT_RANGES == original
