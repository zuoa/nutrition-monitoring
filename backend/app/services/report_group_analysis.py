"""Meal-scoped group indicators, with equal weight for each student."""

from collections import defaultdict
from datetime import timedelta
import math
from types import SimpleNamespace

from app.services.report_assessment import student_reference_profile
from app.services.report_projection import MEAL_WEIGHTS, project_meals
from app.services.report_reference import REFERENCE_TABLE

SLOTS = frozenset(("lunch", "dinner"))
SHARE = sum(MEAL_WEIGHTS[slot] for slot in SLOTS)
MACROS = {"carbohydrate": 4, "fat": 9, "protein": 4}


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def _mean(values):
    values = [value for value in values if value is not None]
    return round(sum(values) / len(values), 1) if values else None


def _scoped_logs(logs, *, common_macros=False):
    result = []
    for log in logs:
        totals = log.nutrient_totals or {}
        if totals.get("_quality_version") == 3:
            meals = [meal for meal in totals.get("_meals", []) if meal.get("slot") in SLOTS]
            if common_macros:
                meals = [meal for meal in meals if all(_number((meal.get("nutrients") or {}).get(key)) is not None for key in MACROS)]
            scoped = {**totals, "_meals": meals}
        elif totals.get("_quality_version") == 2 and set(totals.get("_meal_slots") or []) and set(totals["_meal_slots"]) <= SLOTS:
            if common_macros and any(_number(totals.get(key)) is None for key in MACROS):
                continue
            scoped = dict(totals)
        else:
            # An opaque total containing breakfast cannot be split into two meals.
            continue
        scoped["_skipped_meal_slots"] = sorted(set(totals.get("_skipped_meal_slots") or []) & SLOTS)
        result.append(SimpleNamespace(log_date=log.log_date, meal_count=log.meal_count, nutrient_totals=scoped))
    return result


def _percentages(values):
    if any(values.get(key) is None for key in MACROS):
        return None
    total = sum(values[key] * factor for key, factor in MACROS.items())
    return {key: round(values[key] * factor / total * 100, 1) for key, factor in MACROS.items()} if total else None


def _coverage(ids, attendance, logs_by_student, start, end):
    recorded, estimated, recognized = set(), set(), set()
    for sid in ids:
        for row in attendance.get(sid, {}).get("records", []):
            if row["slot"] in SLOTS and start.isoformat() <= row["date"] <= end.isoformat():
                (estimated if row.get("source") == "estimated" else recorded).add((sid, row["date"], row["slot"]))
        for log in logs_by_student.get(sid, []):
            totals = log.nutrient_totals or {}
            if totals.get("_quality_version") == 3:
                slots = {meal.get("slot") for meal in totals.get("_meals", [])
                         if any(_number(value) is not None and key not in (meal.get("estimated_nutrients") or {})
                                for key, value in (meal.get("nutrients") or {}).items())}
            elif totals.get("_quality_version") == 2 and any(_number(totals.get(key)) is not None for key in MACROS):
                slots = set(totals.get("_meal_slots") or [])
            else:
                slots = set()
            recognized.update((sid, log.log_date.isoformat(), slot) for slot in slots & SLOTS)
    days = {(start + timedelta(days=offset)).isoformat() for offset in range((end - start).days + 1)
            if (start + timedelta(days=offset)).weekday() < 5}
    days.update(day for _, day, _ in recorded)
    expected = len(ids) * len(days) * len(SLOTS)
    return {"recorded_meals": len(recorded), "estimated_meals": len(estimated - recorded), "expected_meals": expected,
            "meal_coverage_rate": round(len(recorded) / expected * 100, 1) if expected else None,
            "matched_meals": len(recognized & recorded),
            "recognition_match_rate": round(len(recognized & recorded) / len(recorded) * 100, 1) if recorded else None}


def build_group_analysis(students, logs_by_student, attendance, start, end):
    """No student names, identifiers, or individual assessments leave this service."""
    samples = []
    for student in students:
        logs = logs_by_student.get(student.id, [])
        projection = project_meals(_scoped_logs(logs), start, end)
        macro_projection = project_meals(_scoped_logs(logs, common_macros=True), start, end)
        nutrients = {key: round(value * SHARE, 1) if value is not None else None for key, value in projection["avg_nutrients"].items()}
        macros = macro_projection["avg_nutrients"]
        profile = student_reference_profile(student, end)
        reference = next((row for row in REFERENCE_TABLE if row["age_group"] == profile["age_group"] and row["sex"] == profile["gender"]), None)
        ratios = _percentages(macros)
        eligible = reference is not None and ratios is not None and nutrients.get("calories") is not None and nutrients.get("fiber") is not None
        passed = eligible and reference["calories"] * .65 <= nutrients["calories"] <= reference["calories"] * SHARE \
            and 20 <= ratios["fat"] <= 30 and 50 <= ratios["carbohydrate"] <= 65 and 10 <= ratios["protein"] <= 15 \
            and nutrients["fiber"] >= reference["fiber"] * SHARE
        samples.append({"id": student.id, "class_id": student.class_id, "nutrients": nutrients, "macros": macros,
                        "reference": reference, "eligible": eligible, "passed": bool(passed), "daily": projection["daily_series"]})

    def summarize(items):
        nutrients = {key: _mean(item["nutrients"][key] for item in items) for key in ("calories", "fiber")}
        complete = [item for item in items if _percentages(item["macros"]) is not None]
        ratios = _percentages({key: _mean(item["macros"][key] for item in complete) for key in MACROS})
        reference = {}
        for key, factor in (("calories", .65), ("fiber", SHARE)):
            measured = [item for item in items if item["nutrients"][key] is not None]
            valid = bool(measured) and all(item["reference"] is not None for item in measured)
            reference[f"{key}_lower"] = _mean(item["reference"][key] * factor for item in measured) if valid else None
            if key == "calories":
                reference["calories_upper"] = _mean(item["reference"][key] * SHARE for item in measured) if valid else None
        assessed = sum(item["eligible"] for item in items)
        passed = sum(item["passed"] for item in items)
        return {"avg_nutrients": nutrients, "energy_percentages": ratios, "macro_sample_count": len(complete), "reference": reference,
                "assessed_students": assessed, "passed_students": passed, "compliance_rate": round(passed / assessed * 100, 1) if assessed else None,
                "coverage": _coverage([item["id"] for item in items], attendance, logs_by_student, start, end)}

    summary = summarize(samples)
    daily = []
    for offset in range((end - start).days + 1):
        values = [item["daily"][offset]["nutrients"].get("calories") for item in samples]
        daily.append({"date": (start + timedelta(days=offset)).isoformat(), "calories": _mean(value * SHARE for value in values if value is not None),
                      "sample_count": sum(value is not None for value in values)})
    by_class = defaultdict(list)
    for item in samples:
        by_class[item["class_id"]].append(item)
    return {"version": 1, "basis": "lunch_dinner", "daily_share": SHARE,
            "coverage_note": "应记录餐次按工作日及有实际记录的周末、每人午晚两餐计算；不含预计餐次，未扣除节假日与请假。",
            "supply_note": "已记录午晚餐按餐次份额折算双餐日均（午餐40%、晚餐35%），每位学生等权；含可用的预计营养，不代表实际摄入。",
            "compliance_note": "达标率为可评价学生中，双餐能量、三大营养素供能比及膳食纤维均在参考范围的人数占比；缺少年龄、性别或营养数据者不参与评价。",
            **summary, "daily_series": daily}, {class_id: summarize(items) for class_id, items in by_class.items()}


def group_fat_contributors(student_ids, start, end, config):
    """Use complete, uniquely assigned observed images; never substitute estimates."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    from sqlalchemy.orm import joinedload
    from app.models import DishRecognition, MatchResult, MatchStatusEnum
    from app.models.menu import resolve_meal_slot_for_datetime
    from app.services.consumption_location_filter import get_enabled_transaction_location_ids

    if not student_ids:
        return {"items": [], "image_count": 0}
    try:
        tz = ZoneInfo(config.get("APP_TIMEZONE") or "Asia/Shanghai")
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("Asia/Shanghai")
    matches = MatchResult.query.options(joinedload(MatchResult.image), joinedload(MatchResult.consumption_record)).filter(
        MatchResult.student_id.in_(student_ids), MatchResult.match_date >= start - timedelta(days=1), MatchResult.match_date <= end + timedelta(days=1),
        MatchResult.image_id.isnot(None), MatchResult.status.in_([MatchStatusEnum.matched, MatchStatusEnum.confirmed]),
    ).order_by(MatchResult.id).all()
    image_ids = {match.image_id for match in matches}
    owners = defaultdict(set)
    for image_id, sid in MatchResult.query.with_entities(MatchResult.image_id, MatchResult.student_id).filter(
        MatchResult.image_id.in_(image_ids), MatchResult.student_id.isnot(None),
        MatchResult.status.in_([MatchStatusEnum.matched, MatchStatusEnum.confirmed]),
    ).all() if image_ids else []:
        owners[image_id].add(sid)
    by_image = defaultdict(list)
    for rec in DishRecognition.query.options(joinedload(DishRecognition.dish)).filter(DishRecognition.image_id.in_(image_ids)).all() if image_ids else []:
        by_image[rec.image_id].append(rec)
    totals, names, processed = defaultdict(float), {}, set()
    locations = get_enabled_transaction_location_ids(config)
    for match in matches:
        transaction = match.consumption_record
        if transaction is not None and ((locations and transaction.channel_id not in locations) or transaction.amount >= 0):
            continue
        timestamp = (transaction.transaction_time if transaction else None) or match.captured_at or (match.image.captured_at if match.image else None)
        if timestamp is None or match.image_id in processed or len(owners[match.image_id]) != 1:
            continue
        local = timestamp.replace(tzinfo=tz) if timestamp.tzinfo is None else timestamp.astimezone(tz)
        if not start <= local.date() <= end or resolve_meal_slot_for_datetime(local, str(tz), config) not in SLOTS:
            continue
        recs = by_image[match.image_id]
        if not recs or any(rec.is_low_confidence or not rec.dish or _number(rec.dish.fat) is None for rec in recs):
            continue
        processed.add(match.image_id)
        for rec in recs:
            totals[rec.dish_id] += float(rec.dish.fat)
            names[rec.dish_id] = rec.dish.name
    total = sum(totals.values())
    return {"image_count": len(processed), "items": [
        {"name": names[key], "fat": round(totals[key], 1), "share": round(totals[key] / total * 100, 1)}
        for key in sorted(totals, key=lambda key: (-totals[key], key))[:5] if total and totals[key] > 0
    ]}
