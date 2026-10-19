from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.services.report_group_analysis import build_group_analysis

START = date(2026, 10, 12)
END = START + timedelta(days=4)


def student(sid=1, gender="男", class_id=10):
    return SimpleNamespace(id=sid, class_id=class_id, class_=None, class_name="高一", grade_name="高一", age=16,
                           birth_date=None, gender=gender)


def log(meals, offset=0, skipped=()):
    return SimpleNamespace(log_date=START + timedelta(days=offset), meal_count=len(meals), nutrient_totals={
        "_quality_version": 3, "_meals": meals, "_skipped_meal_slots": list(skipped),
    })


def meal(slot="lunch", **values):
    return {"slot": slot, "nutrients": values}


def balanced_meal():
    return meal(calories=1040, carbohydrate=140, fat=28, protein=32, fiber=11.2)


def analyze(students, logs, attendance=None):
    return build_group_analysis(students, logs, attendance or {}, START, END)


def test_normalizes_only_lunch_and_dinner_and_weights_students_equally():
    first = log([balanced_meal(), meal("breakfast", calories=99999)])
    second = [log([meal("dinner", calories=700)], offset) for offset in range(4)]
    result, classes = analyze([student(), student(2, class_id=20)], {1: [first], 2: second})
    assert result["avg_nutrients"]["calories"] == 1725  # (1950 + 1500) / 2, not a meal-weighted mean.
    assert classes[10]["avg_nutrients"]["calories"] == 1950
    assert classes[20]["avg_nutrients"]["calories"] == 1500
    assert result["daily_series"][0]["calories"] == 1725
    assert result["daily_series"][1]["calories"] == 1500
    assert result["daily_series"][4]["calories"] is None


def test_energy_ratios_use_common_complete_meals():
    result, _ = analyze([student()], {1: [log([balanced_meal(), meal("dinner", fat=10000)])]})
    assert result["energy_percentages"]["fat"] == pytest.approx(26.8)
    assert result["energy_percentages"]["carbohydrate"] == pytest.approx(59.6)
    assert result["macro_sample_count"] == 1
    assert result["compliance_rate"] == 100


def test_skipped_breakfast_does_not_reduce_two_meal_supply():
    result, _ = analyze([student()], {1: [log([balanced_meal()], skipped=("breakfast",))]})
    assert result["avg_nutrients"]["calories"] == 1950


def test_unsplittable_v2_totals_with_breakfast_are_not_treated_as_two_meal_records():
    opaque = SimpleNamespace(log_date=START, meal_count=3, nutrient_totals={
        "_quality_version": 2, "_meal_slots": ["breakfast", "lunch", "dinner"], "calories": 2100,
    })
    result, _ = analyze([student()], {1: [opaque]})
    assert result["avg_nutrients"]["calories"] is None
    assert result["energy_percentages"] is None
    assert result["compliance_rate"] is None


def test_real_zero_is_preserved_and_missing_profiles_are_excluded_from_assessment():
    result, _ = analyze([student(), student(2, gender=None)], {
        1: [log([balanced_meal()])], 2: [log([meal(calories=0, fiber=0, carbohydrate=0, fat=0, protein=0)])],
    })
    assert result["avg_nutrients"]["calories"] == 975
    assert result["avg_nutrients"]["fiber"] == 10.5
    assert result["reference"]["calories_lower"] is None  # No mismatched denominator for references.
    assert result["reference"]["fiber_lower"] is None
    assert result["assessed_students"] == 1
    assert result["passed_students"] == 1
    assert result["compliance_rate"] == 100


def test_coverage_deduplicates_records_and_excludes_estimated_nutrition():
    lunch = {"date": START.isoformat(), "slot": "lunch", "source": "observed"}
    dinner = {"date": START.isoformat(), "slot": "dinner", "source": "observed"}
    estimated = meal("dinner", calories=700)
    estimated["estimated_nutrients"] = {"calories": "history"}
    result, _ = analyze([student()], {1: [log([balanced_meal(), estimated])]}, {1: {"records": [
        lunch, lunch, dinner, {"date": (START + timedelta(days=1)).isoformat(), "slot": "lunch", "source": "estimated"},
    ]}})
    assert result["coverage"] == {
        "recorded_meals": 2, "estimated_meals": 1, "expected_meals": 10, "meal_coverage_rate": 20,
        "matched_meals": 1, "recognition_match_rate": 50,
    }


def test_weekend_records_increase_expected_days_instead_of_exceeding_one_hundred_percent():
    saturday = START + timedelta(days=5)
    result, _ = build_group_analysis([student()], {}, {1: {"records": [{"date": saturday.isoformat(), "slot": "lunch"}]}}, saturday, saturday)
    assert result["coverage"]["expected_meals"] == 2
    assert result["coverage"]["meal_coverage_rate"] == 50


def test_empty_group_has_no_fabricated_values_or_reference():
    result, classes = analyze([], {})
    assert result["avg_nutrients"]["calories"] is None
    assert result["energy_percentages"] is None
    assert result["coverage"]["meal_coverage_rate"] is None
    assert result["coverage"]["recognition_match_rate"] is None
    assert result["reference"]["fiber_lower"] is None
    assert classes == {}
