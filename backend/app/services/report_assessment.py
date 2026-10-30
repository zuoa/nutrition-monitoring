"""Student meal reference assessment; levels are product labels, not diagnoses."""

import math
import re
from datetime import date

from app.nutrition_metadata import REPORT_NUTRITION_FIELDS
from app.services.report_reference import REFERENCE_TABLE, select_reference
from app.services.report_assessment_ranges import get_report_assessment_ranges

ASSESSMENT_LEVELS = (
    (90, "A", "达标良好", "good"),
    (75, "B", "基本达标", "fair"),
    (60, "C", "需改善", "attention"),
    (0, "D", "重点关注", "poor"),
)
ASSESSMENT_WEIGHTS = {"calories": 30, "protein": 25, "fat": 20, "carbohydrate": 15, "fiber": 10}
ASSESSMENT_VERSION = 5


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (ValueError, TypeError):
        return None


def age_from_grade(name, stage_type=None):
    name = str(name or "").strip()
    chinese = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
               "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}
    for prefix, offset, maximum in (("高", 14, 3), ("初", 11, 3), ("小", 5, 6)):
        match = re.match(rf"{prefix}(?:中|学)?([一二三四五六1-6])", name)
        if match:
            number = chinese.get(match[1]) or int(match[1])
            return offset + number if number <= maximum else None
    match = re.fullmatch(r"(十一|十二|[一二三四五六七八九十]|1[0-2]|[1-9])年级", name)
    if not match:
        match = re.fullmatch(r"[Gg]rade\s*(1[0-2]|[1-9])", name)
    if match:
        number = chinese.get(match[1]) or int(match[1])
        stage = getattr(stage_type, "value", stage_type)
        if stage == "senior" and number <= 3:
            return 14 + number
        if stage == "junior" and number <= 3:
            return 11 + number
        return number + 5
    return None


def reference_profile(*, as_of, age=None, birth_date=None, gender=None, grade_name=None, class_name=None, stage_type=None):
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of)
    if birth_date:
        try:
            birthday = birth_date if isinstance(birth_date, date) else date.fromisoformat(str(birth_date))
            age = as_of.year - birthday.year - ((as_of.month, as_of.day) < (birthday.month, birthday.day))
            source = "birth_date"
        except (TypeError, ValueError):
            age, source = None, "unknown"
    elif _number(age) is not None and float(age).is_integer():
        age, source = int(age), "age"
    else:
        age = age_from_grade(grade_name, stage_type)
        if age is None:
            age = age_from_grade(class_name, stage_type)
        source = "grade" if age is not None else "unknown"
    sex = {"男": "男", "男生": "男", "male": "男", "m": "男", "1": "男",
           "女": "女", "女生": "女", "female": "女", "f": "女", "2": "女"}.get(str(gender or "").strip().lower())
    age_groups = ((6, 8, "6—8岁"), (9, 11, "9—11岁"), (12, 14, "12—14岁"), (15, 17, "15—17岁"))
    group = next((label for low, high, label in age_groups
                  if age is not None and low <= age <= high), None)
    return {"age": age, "age_source": source, "gender": sex, "age_group": group, "grade_name": grade_name}


def student_reference_profile(student, as_of):
    cls = student.class_
    grade = cls.grade if cls else None
    stage = grade.stage if grade else None
    return reference_profile(as_of=as_of, age=student.age, birth_date=student.birth_date, gender=student.gender,
                             grade_name=grade.name if grade else student.grade_name,
                             class_name=cls.name if cls else student.class_name, stage_type=stage.stage_type if stage else None)


def reference_bounds(key, row):
    # Reference amounts depend only on the demographic reference, never on
    # observed energy or report gains. Keep the two sides of the comparison separate.
    if not row:
        return None, None
    energy = row["calories"]
    if key == "calories":
        return row[key] * .9, row[key] * 1.1
    if key == "protein":
        return row[key] * .8, row[key] * 1.2
    if key == "fiber":
        return row[key], None
    if key == "fat" and energy:
        return energy * .2 / 9, energy * .3 / 9
    if key == "carbohydrate" and energy:
        return energy * .5 / 4, energy * .65 / 4
    return None, None


def _interval_score(value, lower, upper):
    """Continuous loss outside the reference interval, with no jump at its edge."""
    if value < lower:
        deviation = (lower - value) / lower
    elif upper is not None and value > upper:
        deviation = (value - upper) / upper if upper else 1
    else:
        deviation = 0
    # Small deviations have a small effect; the loss increases progressively.
    distance = min(deviation / .5, 1)
    return 100 * (1 - distance * distance), deviation


def report_nutrient_values(content):
    """Use final estimates when present, preserving missing values and real zeros."""
    projection = content.get("meal_projection") or {}
    values = projection.get("avg_nutrients") if projection else content.get("avg_nutrients")
    counts = content.get("nutrient_sample_counts")
    return {key: _number(value) if projection or counts is None or counts.get(key, 0) > 0 else None
            for key, value in (values or {}).items()}


def assess_personal_report(content):
    profile = content.get("student_profile")
    if profile is None:
        profile = reference_profile(as_of=content["period_end"], age=content.get("age"), birth_date=content.get("birth_date"),
                                    gender=content.get("gender"), grade_name=content.get("grade_name"), class_name=content.get("class_name"))
    table = content.get("reference_table") or REFERENCE_TABLE
    row = select_reference(profile, table)
    ranges = get_report_assessment_ranges({"REPORT_ASSESSMENT_RANGES": content.get("report_assessment_ranges")})
    fixed_ranges = ranges["nutrients"] if ranges["enabled"] else None
    projection = content.get("meal_projection") or {}
    values = report_nutrient_values(content)
    fields = []
    component_scores = {}
    component_deviations = {}
    for field in REPORT_NUTRITION_FIELDS:
        value = values.get(field.key)
        bounds = fixed_ranges.get(field.key) if fixed_ranges else None
        lower, upper = (bounds["lower"], bounds["upper"]) if bounds else reference_bounds(field.key, row)
        lower = round(lower, 1) if lower is not None else None
        upper = round(upper, 1) if upper is not None else None
        tone, status = "unknown", "待评价"
        score, deviation = None, None
        if value is None:
            status = "暂无数据"
        elif field.key == "cholesterol":
            status = "仅展示"
        elif projection and lower is not None:
            score, deviation = _interval_score(value, lower, upper)
            component_scores[field.key] = score
            component_deviations[field.key] = deviation
            tone = "low" if value < lower else "high" if upper is not None and value > upper else "ok"
            if tone == "ok":
                status = "达标"
            else:
                degree = "轻微" if deviation <= .1 + 1e-9 else "明显" if deviation > .3 + 1e-9 else ""
                status = degree + ("偏低" if tone == "low" else "偏高")
        fields.append({"key": field.key, "label": field.label, "unit": field.unit, "value": value,
                       "lower": lower, "upper": upper, "tone": tone, "status": status,
                       "reference": (f"{lower:g}–{upper:g}" if upper is not None else f"≥{lower:g}") if lower is not None else "",
                       "score": round(score, 1) if score is not None else None})
    assessed = [field for field in fields if field["tone"] != "unknown"]
    passed = sum(field["tone"] == "ok" for field in assessed)
    level, label, tone = None, "待评价", "unknown"
    overall_score = None
    if len(assessed) == 5 and (row or fixed_ranges):
        weighted_score = sum(component_scores[key] * weight for key, weight in ASSESSMENT_WEIGHTS.items()) / sum(ASSESSMENT_WEIGHTS.values())
        # Ordinary deviations are already reflected in the component scores.
        # Apply an additional, continuous penalty only for a clearly large
        # deviation (>30%), reaching its maximum at 50% outside the interval.
        worst_deviation = max(component_deviations.values())
        severe_distance = min(max((worst_deviation - .3) / .2, 0), 1)
        overall_score = round(weighted_score * (1 - .5 * severe_distance * severe_distance), 1)
        _, level, label, tone = next(item for item in ASSESSMENT_LEVELS if overall_score >= item[0])
    if not fixed_ranges and not profile.get("age_group"):
        reason = "缺少适用的年龄参考"
    elif not fixed_ranges and row is None:
        reason = "缺少适用的营养参考"
    elif not projection or len(assessed) < 5:
        reason = "营养数据不足"
    else:
        reason = ""
    if fixed_ranges:
        energy_range = fixed_ranges["calories"]
        reference_energy = (energy_range["lower"] + energy_range["upper"]) / 2 if energy_range["upper"] is not None else energy_range["lower"]
        reference_source = "fixed_ranges"
    elif row:
        reference_energy = row["calories"]
        reference_source = "age_group_minimum" if not profile.get("gender") else "demographic"
    else:
        reference_energy, reference_source = None, "unavailable"
    return {"version": ASSESSMENT_VERSION, "score": overall_score,
            "level": level, "label": label, "tone": tone, "passed_count": passed, "assessed_count": len(assessed),
            "reference_count": 5, "reference_energy": reference_energy,
            "reference_source": reference_source,
            "profile": profile, "fields": fields, "reason": reason}
