"""Normalize recorded meals by their planned share of a full day's supply."""

import math
from collections import defaultdict
from datetime import timedelta

from app.nutrition_metadata import NUTRITION_FIELD_KEYS

# WS/T 554—2017 §3.3: breakfast 25–30%, lunch 35–40%, dinner 30–35%.
# These defaults are a planning choice within those ranges, not an observation.
MEAL_WEIGHTS = {"breakfast": .25, "lunch": .4, "dinner": .35}
MEAL_WEIGHT_RANGES = {"breakfast": [25, 30], "lunch": [35, 40], "dinner": [30, 35]}
PROJECTION_NOTE = "假设未记录餐次与已记录餐次具有相同的营养供给水平，按餐次份额折算全天；不代表实际全天摄入。"


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (ValueError, TypeError):
        return None


def _daily_observations(log):
    totals = log.nutrient_totals or {}
    meals = totals.get("_meals")
    if totals.get("_quality_version") == 3 and isinstance(meals, list):
        # All images in a date/slot were combined before storing this snapshot.
        # An incomplete nutrient invalidates that meal only, not another meal.
        for meal in meals:
            if meal.get("slot") in MEAL_WEIGHTS:
                yield meal["slot"], meal.get("nutrients") or {}, 1
    elif totals.get("_quality_version") == 2:
        slots = set(totals.get("_meal_slots") or [])
        # A v2 total cannot be split: unknown/custom meals contaminate its sum.
        if slots and slots <= MEAL_WEIGHTS.keys():
            yield None, totals, len(slots)


def project_meals(logs, start, end):
    accum = {key: {"total": 0., "weight": 0., "meals": 0, "days": 0} for key in NUTRITION_FIELD_KEYS}
    series = {}
    eligible_days = meal_count = 0
    shares = []
    for log in logs:
        if log.meal_count <= 0:
            continue
        day = {key: {"total": 0., "weight": 0., "meals": 0} for key in accum}
        eligible_slots = set()
        for slot, nutrients, count in _daily_observations(log):
            slots = {slot} if slot else set(log.nutrient_totals["_meal_slots"])
            weight = sum(MEAL_WEIGHTS[key] for key in slots)
            values = {key: _number(nutrients.get(key)) for key in accum}
            if any(value is not None for value in values.values()):
                eligible_slots.update(slots)
            for key, value in values.items():
                if value is not None:
                    day[key]["total"] += value
                    day[key]["weight"] += weight
                    day[key]["meals"] += count
        if eligible_slots:
            eligible_days += 1
            meal_count += len(eligible_slots)
            shares.append(sum(MEAL_WEIGHTS[slot] for slot in eligible_slots))
        values, counts, weights = {}, {}, {}
        for key, sample in day.items():
            weight = sample["weight"]
            values[key] = round(sample["total"] / weight, 1) if weight else None
            counts[key] = int(weight > 0)
            weights[key] = round(weight, 4)
            if weight:
                accum[key]["total"] += sample["total"]
                accum[key]["weight"] += weight
                accum[key]["meals"] += sample["meals"]
                accum[key]["days"] += 1
        series[log.log_date.isoformat()] = {"nutrients": values, "sample_counts": counts, "weights": weights}
    daily_series = []
    for offset in range((end - start).days + 1):
        day = (start + timedelta(days=offset)).isoformat()
        daily_series.append({"date": day, **series.get(day, {
            "nutrients": {key: None for key in accum}, "sample_counts": {key: 0 for key in accum}, "weights": {key: 0 for key in accum},
        })})
    return {
        "method": "meal_share_ratio", "weights": dict(MEAL_WEIGHTS), "weight_ranges": dict(MEAL_WEIGHT_RANGES),
        "source_id": "ws554-2017", "source_section": "3.3", "assumption": PROJECTION_NOTE,
        "avg_nutrients": {key: round(v["total"] / v["weight"], 1) if v["weight"] else None for key, v in accum.items()},
        "nutrient_sample_counts": {key: v["days"] for key, v in accum.items()},
        "nutrient_meal_counts": {key: v["meals"] for key, v in accum.items()},
        "nutrient_weight_sums": {key: round(v["weight"], 4) for key, v in accum.items()},
        "recorded_totals": {key: round(v["total"], 2) if v["weight"] else None for key, v in accum.items()},
        "eligible_days": eligible_days, "excluded_days": sum(log.meal_count > 0 for log in logs) - eligible_days,
        "meal_count": meal_count, "average_daily_share": round(sum(shares) / len(shares) * 100, 1) if shares else None,
        "daily_series": daily_series,
    }


def aggregate_projections(projections):
    """Each student's period estimate has equal weight, regardless of meal count."""
    values = defaultdict(list)
    for projection in projections:
        for key, value in projection["avg_nutrients"].items():
            if value is not None:
                values[key].append(value)
    days = sum(p["eligible_days"] for p in projections)
    return {
        "method": "student_equal_mean", "weights": dict(MEAL_WEIGHTS), "weight_ranges": dict(MEAL_WEIGHT_RANGES),
        "source_id": "ws554-2017", "source_section": "3.3", "assumption": PROJECTION_NOTE,
        "avg_nutrients": {key: round(sum(values[key]) / len(values[key]), 1) if values[key] else None for key in NUTRITION_FIELD_KEYS},
        "nutrient_sample_counts": {key: len(values[key]) for key in NUTRITION_FIELD_KEYS},
        "eligible_days": days, "excluded_days": sum(p["excluded_days"] for p in projections),
        "meal_count": sum(p["meal_count"] for p in projections),
        "average_daily_share": round(sum(p["average_daily_share"] * p["eligible_days"] for p in projections if p["eligible_days"]) / days, 1) if days else None,
    }
