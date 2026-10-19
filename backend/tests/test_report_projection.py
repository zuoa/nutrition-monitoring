from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.services.report_projection import MEAL_WEIGHTS, aggregate_projections, project_meals


START = date(2026, 10, 12)


def log(totals, offset=0):
    return SimpleNamespace(log_date=START + timedelta(days=offset), meal_count=1, nutrient_totals=totals)


def snapshot(slots, nutrients, offset=0):
    return log({"_quality_version": 2, "_meal_slots": slots, **nutrients}, offset)


@pytest.mark.parametrize("slots,share", [
    (["breakfast"], .25), (["lunch"], .4), (["dinner"], .35),
    (["breakfast", "lunch"], .65), (["lunch", "dinner"], .75),
    (["breakfast", "dinner"], .6), (["breakfast", "lunch", "dinner"], 1),
])
def test_meal_combinations_normalize_to_the_same_full_day(slots, share):
    projection = project_meals([snapshot(slots, {"calories": 2000 * share})], START, START)
    assert projection["avg_nutrients"]["calories"] == 2000
    assert projection["average_daily_share"] == round(share * 100, 1)
    assert projection["nutrient_meal_counts"]["calories"] == len(slots)
    assert MEAL_WEIGHTS == {"breakfast": .25, "lunch": .4, "dinner": .35}
    assert sum(MEAL_WEIGHTS.values()) == 1


def test_mixed_days_use_total_intake_over_total_shares_and_preserve_gaps():
    projection = project_meals([
        snapshot(["lunch"], {"calories": 400}),
        snapshot(["breakfast", "lunch", "dinner"], {"calories": 2000}, 2),
    ], START, START + timedelta(days=2))
    assert projection["avg_nutrients"]["calories"] == 1714.3
    assert projection["nutrient_weight_sums"]["calories"] == 1.4
    assert projection["daily_series"][0]["nutrients"]["calories"] == 1000
    assert projection["daily_series"][1]["nutrients"]["calories"] is None
    assert projection["daily_series"][2]["nutrients"]["calories"] == 2000


def test_each_nutrient_uses_only_its_complete_meals_and_keeps_real_zero():
    projection = project_meals([log({"_quality_version": 3, "_meals": [
        {"slot": "breakfast", "nutrients": {"calories": None, "protein": 10, "calcium": 0}},
        {"slot": "lunch", "nutrients": {"calories": 800, "protein": None, "calcium": 100}},
        {"slot": "unknown", "nutrients": {"calories": 9999, "protein": 9999}},
    ]})], START, START)
    assert projection["avg_nutrients"]["calories"] == 2000
    assert projection["avg_nutrients"]["protein"] == 40
    assert projection["avg_nutrients"]["calcium"] == 153.8
    assert projection["avg_nutrients"]["fat"] is None
    assert projection["nutrient_meal_counts"]["protein"] == 1
    assert projection["nutrient_weight_sums"]["protein"] == .25
    assert projection["meal_count"] == 2


def test_untraceable_and_unsplittable_days_do_not_invent_weights():
    projection = project_meals([
        log({"calories": 0}),
        snapshot(["lunch", "unknown"], {"calories": 1000}, 1),
        snapshot(["snack"], {"calories": 200}, 2),
        snapshot(["lunch"], {"calories": 0, "protein": -1, "fat": float("nan")}, 3),
    ], START, START + timedelta(days=3))
    assert projection["avg_nutrients"]["calories"] == 0
    assert projection["avg_nutrients"]["protein"] is None
    assert projection["avg_nutrients"]["fat"] is None
    assert projection["excluded_days"] == 3
    assert projection["eligible_days"] == 1


def test_group_aggregation_keeps_each_student_equal_despite_different_coverage():
    lunch = project_meals([snapshot(["lunch"], {"calories": 400})], START, START)
    full_day = project_meals([snapshot(["breakfast", "lunch", "dinner"], {"calories": 3000})], START, START)
    missing = project_meals([log({"calories": 0})], START, START)
    group = aggregate_projections([lunch, full_day, missing])
    assert group["avg_nutrients"]["calories"] == 2000
    assert group["nutrient_sample_counts"]["calories"] == 2
    assert group["excluded_days"] == 1
    assert group["average_daily_share"] == 70


def test_empty_projection_has_no_nutrients_or_coverage():
    projection = project_meals([], START, START)
    assert all(value is None for value in projection["avg_nutrients"].values())
    assert projection["average_daily_share"] is None
    assert aggregate_projections([])["average_daily_share"] is None


def test_known_skips_reduce_supply_without_increasing_meal_counts():
    day = snapshot(["lunch"], {"calories": 800, "protein": 20})
    day.nutrient_totals["_skipped_meal_slots"] = ["breakfast", "dinner", "lunch", "unknown"]
    projection = project_meals([day], START, START)
    assert projection["avg_nutrients"]["calories"] == 800
    assert projection["avg_nutrients"]["protein"] == 20
    assert projection["nutrient_meal_counts"]["calories"] == projection["meal_count"] == 1
    assert projection["nutrient_weight_sums"]["calories"] == 1
    assert projection["avg_nutrients"]["fat"] is None
