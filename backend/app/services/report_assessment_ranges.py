"""Configurable absolute ranges, saved in each report's reference snapshot."""

from copy import deepcopy
import math

from app.nutrition_metadata import NUTRITION_FIELD_LABELS

ASSESSMENT_RANGE_KEYS = ("calories", "protein", "fat", "carbohydrate", "fiber")
DEFAULT_ASSESSMENT_RANGES = {
    "enabled": False,
    "nutrients": {
        "calories": {"lower": 1800., "upper": 2200.},
        "protein": {"lower": 48., "upper": 72.},
        "fat": {"lower": 44.4, "upper": 66.7},
        "carbohydrate": {"lower": 250., "upper": 325.},
        "fiber": {"lower": 25., "upper": None},
    },
}


def validate_report_assessment_ranges(value, current=None):
    if not isinstance(value, dict) or set(value) - {"enabled", "nutrients"}:
        raise ValueError("达标区间配置必须包含启用选项和营养素区间")
    result = deepcopy(current or DEFAULT_ASSESSMENT_RANGES)
    if "enabled" in value:
        if not isinstance(value["enabled"], bool):
            raise ValueError("达标区间启用选项必须为布尔值")
        result["enabled"] = value["enabled"]
    nutrients = value.get("nutrients", {})
    if not isinstance(nutrients, dict) or set(nutrients) - set(ASSESSMENT_RANGE_KEYS):
        raise ValueError("包含不支持的营养素达标区间")
    for key, bounds in nutrients.items():
        label = NUTRITION_FIELD_LABELS[key]
        if not isinstance(bounds, dict) or set(bounds) != {"lower", "upper"}:
            raise ValueError(f"{label}需填写下限和上限；无上限时上限可为空")
        lower, upper = bounds["lower"], bounds["upper"]
        if type(lower) not in (int, float) or not math.isfinite(lower) or lower < 0:
            raise ValueError(f"{label}下限必须是大于等于 0 的数字")
        if upper is not None and (type(upper) not in (int, float) or not math.isfinite(upper) or upper <= lower):
            raise ValueError(f"{label}上限必须大于下限，或留空表示无上限")
        result["nutrients"][key] = {"lower": float(lower), "upper": float(upper) if upper is not None else None}
    return result


def get_report_assessment_ranges(config):
    try:
        return validate_report_assessment_ranges(config.get("REPORT_ASSESSMENT_RANGES") or {})
    except ValueError:
        return deepcopy(DEFAULT_ASSESSMENT_RANGES)
