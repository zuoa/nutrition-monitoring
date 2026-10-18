"""Student meal reference assessment; levels are product labels, not diagnoses."""

import math
import re
from datetime import date

from app.nutrition_metadata import REPORT_NUTRITION_FIELDS
from app.services.report_reference import REFERENCE_TABLE

ASSESSMENT_LEVELS = (
    (5, "A", "达标良好", "good"),
    (4, "B", "基本达标", "fair"),
    (3, "C", "需改善", "attention"),
    (0, "D", "重点关注", "poor"),
)


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
    group = next((label for low, high, label in ((6, 8, "6—8岁"), (9, 11, "9—11岁"),
                                                (12, 14, "12—14岁"), (15, 17, "15—17岁"))
                  if age is not None and low <= age <= high), None)
    return {"age": age, "age_source": source, "gender": sex, "age_group": group, "grade_name": grade_name}


def student_reference_profile(student, as_of):
    cls = student.class_
    grade = cls.grade if cls else None
    stage = grade.stage if grade else None
    return reference_profile(as_of=as_of, age=student.age, birth_date=student.birth_date, gender=student.gender,
                             grade_name=grade.name if grade else student.grade_name,
                             class_name=cls.name if cls else student.class_name, stage_type=stage.stage_type if stage else None)


def reference_bounds(key, row, energy):
    # WS/T 554—2017 table 1 and its continuation, including the table note.
    if key == "calories" and row:
        return row[key] * .9, row[key] * 1.1
    if key == "protein" and row:
        return row[key] * .8, row[key] * 1.2
    if key == "fiber" and row:
        return row[key], None
    if key == "fat" and energy:
        return energy * .2 / 9, energy * .3 / 9
    if key == "carbohydrate" and energy:
        return energy * .5 / 4, energy * .65 / 4
    return None, None


def assess_personal_report(content):
    profile = content.get("student_profile")
    if profile is None:
        profile = reference_profile(as_of=content["period_end"], age=content.get("age"), birth_date=content.get("birth_date"),
                                    gender=content.get("gender"), grade_name=content.get("grade_name"), class_name=content.get("class_name"))
    table = content.get("reference_table") or REFERENCE_TABLE
    row = next((row for row in table if row["age_group"] == profile.get("age_group") and row["sex"] == profile.get("gender")), None)
    projection = content.get("meal_projection") or {}
    values = projection.get("avg_nutrients") if projection else content.get("avg_nutrients")
    values = values or {}
    counts = content.get("nutrient_sample_counts")
    values = {key: _number(value) if projection or counts is None or counts.get(key, 0) > 0 else None for key, value in values.items()}
    energy = values.get("calories")
    fields = []
    for field in REPORT_NUTRITION_FIELDS:
        value = values.get(field.key)
        lower, upper = reference_bounds(field.key, row, energy)
        lower = round(lower, 1) if lower is not None else None
        upper = round(upper, 1) if upper is not None else None
        tone, status = "unknown", "待评价"
        if value is None:
            status = "暂无数据"
        elif field.key == "cholesterol":
            status = "仅展示"
        elif projection and lower is not None:
            tone = "low" if value < lower else "high" if upper is not None and value > upper else "ok"
            status = {"low": "偏低", "high": "偏高", "ok": "达标"}[tone]
        fields.append({"key": field.key, "label": field.label, "unit": field.unit, "value": value,
                       "lower": lower, "upper": upper, "tone": tone, "status": status})
    assessed = [field for field in fields if field["tone"] != "unknown"]
    passed = sum(field["tone"] == "ok" for field in assessed)
    level, label, tone = None, "待评价", "unknown"
    if len(assessed) == 5 and row:
        _, level, label, tone = next(item for item in ASSESSMENT_LEVELS if passed >= item[0])
    if not profile.get("age_group"):
        reason = "缺少适用的年龄参考"
    elif not profile.get("gender"):
        reason = "请补充学生性别"
    elif not projection or len(assessed) < 5:
        reason = "营养数据不足"
    else:
        reason = ""
    return {"level": level, "label": label, "tone": tone, "passed_count": passed, "assessed_count": len(assessed),
            "reference_count": 5, "profile": profile, "fields": fields, "reason": reason}
