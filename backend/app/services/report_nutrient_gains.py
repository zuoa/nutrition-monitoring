"""Apply configured gains to report inputs without changing stored nutrition logs."""

import math
from types import SimpleNamespace

from app.nutrition_metadata import NUTRITION_FIELD_LABELS

REPORT_GAIN_KEYS = ("calories", "protein", "carbohydrate", "fiber", "fat", "calcium")
MIN_GAIN = .01
MAX_GAIN = 10


def _valid_gain(value):
    return type(value) in (int, float) and MIN_GAIN <= value <= MAX_GAIN and math.isfinite(value)


def validate_report_nutrient_gains(value):
    if not isinstance(value, dict):
        raise ValueError("report_nutrient_gains 必须是营养素系数对象")
    unknown = set(value) - set(REPORT_GAIN_KEYS)
    if unknown:
        raise ValueError(f"不支持的报告营养素: {', '.join(sorted(unknown))}")
    for key, factor in value.items():
        if not _valid_gain(factor):
            raise ValueError(f"{NUTRITION_FIELD_LABELS[key]}增益系数必须是 {MIN_GAIN}～{MAX_GAIN} 的数字")
    return {key: float(factor) for key, factor in value.items()}


def get_report_nutrient_gains(config):
    configured = config.get("REPORT_NUTRIENT_GAINS") or {}
    if not isinstance(configured, dict):
        configured = {}
    return {key: float(configured[key]) if _valid_gain(configured.get(key)) else 1. for key in REPORT_GAIN_KEYS}


def adjust_report_logs(logs, gains):
    """Scale daily totals and meal snapshots once, before aggregation and assessment."""
    if all(factor == 1 for factor in gains.values()):
        return list(logs)

    def adjust(values):
        result = dict(values or {})
        for key, factor in gains.items():
            try:
                value = float(result.get(key))
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and value >= 0:
                result[key] = value * factor
        return result

    result = []
    for log in logs:
        nutrients = adjust(log.nutrient_totals)
        if isinstance(nutrients.get("_meals"), list):
            nutrients["_meals"] = [{**meal, "nutrients": adjust(meal.get("nutrients"))} for meal in nutrients["_meals"]]
        result.append(SimpleNamespace(student_id=getattr(log, "student_id", None), log_date=log.log_date,
                                      meal_count=log.meal_count, dish_ids=getattr(log, "dish_ids", []), nutrient_totals=nutrients))
    return result
