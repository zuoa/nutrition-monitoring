"""Observed dining patterns, independent of nutrient assessments and estimates."""

from collections import Counter, defaultdict
import math
from datetime import datetime, time, timedelta
from statistics import median, quantiles
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app
from sqlalchemy.orm import joinedload

from app.dish_tags import FOOD_GROUPS
from app.models import ConsumptionRecord, Dish, DishRecognition, MatchResult, MatchStatusEnum, NutritionLog
from app.models.menu import get_meal_slots, resolve_meal_slot_for_datetime
from app.services.consumption_location_filter import apply_enabled_transaction_location_filter, get_enabled_transaction_location_ids
from app.services.runtime_config import get_effective_config

DINING_FEATURES_VERSION = 2
FEATURES = (("regularity", "就餐规律", "breakfast"), ("variety", "选餐多样性", "vegetables"),
            ("pairing", "搭配习惯", "carbohydrate"), ("food_groups", "食物选择倾向", "protein"))


def _clock(minutes):
    rounded = int(round(minutes))
    return f"{rounded // 60:02d}:{rounded % 60:02d}"


def empty_dining_features():
    return {"version": DINING_FEATURES_VERSION, "composition": [], "contributors": {}, "items": [
        {"key": key, "title": title, "icon": icon, "tone": "neutral", "status": "暂无数据",
         "description": "暂无实际就餐记录。" if key == "regularity" else "暂无有效菜品识别记录。", "evidence": {}}
        for key, title, icon in FEATURES
    ]}


def summarize_dining_features(meals, slot_labels):
    """meals maps (local date, slot) to actual timestamps and unique dish snapshots."""
    result = empty_dining_features()
    regularity, variety, pairing, groups = result["items"]
    known = {key: value for key, value in meals.items() if key[1] in slot_labels}
    counts = Counter(slot for _, slot in known)
    time_stats = {}
    for slot in slot_labels:
        minutes = []
        for (_, meal_slot), meal in known.items():
            times = meal.get("transaction_times") or meal.get("capture_times") or []
            if meal_slot == slot and times:
                minutes.append(min(times))
        if len(minutes) >= 5:
            q1, _, q3 = quantiles(minutes, n=4, method="inclusive")
            time_stats[slot] = {"samples": len(minutes), "median": _clock(median(minutes)), "middle_half": [_clock(q1), _clock(q3)]}
    if known:
        regularity.update(status=f"记录 {len(known)} 餐", evidence={"meal_counts": dict(counts), "times": time_stats})
        copy = "、".join(f"{label} {counts[slot]} 次" for slot, label in slot_labels.items() if slot in counts) + "。"
        if time_stats:
            slot = max(time_stats, key=lambda key: time_stats[key]["samples"])
            stats = time_stats[slot]
            copy += f"{slot_labels[slot]}时间中位数 {stats['median']}，中间50%为 {'—'.join(stats['middle_half'])}。"
        else:
            copy += "同餐次时间样本不足5次，暂不概括时段。"
        regularity["description"] = copy
    recognized = [meal["dishes"] for meal in known.values() if meal.get("dishes")]
    if not recognized:
        return result
    dish_counts = Counter(dish_id for dishes in recognized for dish_id in dishes)
    names = {dish_id: dish["name"] for dishes in recognized for dish_id, dish in dishes.items()}
    snapshots = {dish_id: dish for dishes in recognized for dish_id, dish in dishes.items()}
    result["dish_choices"] = [{"id": key, "name": names[key], "count": count,
                               "food_groups": snapshots[key].get("food_groups") or []}
                              for key, count in sorted(dish_counts.items(), key=lambda item: (-item[1], item[0]))]
    top = sorted(dish_counts, key=lambda dish_id: (-dish_counts[dish_id], dish_id))[:3]
    selections = sum(dish_counts.values())
    category_counts = Counter()
    for dishes in recognized:
        category_counts.update(dish["category"] if dish["category"] in ("荤菜", "素菜", "主食") else "其他" for dish in dishes.values())
    result["composition"] = [{"label": label, "count": category_counts[label], "share": round(category_counts[label] / selections * 100, 1), "color": color}
                             for label, color in (("荤菜", "#006442"), ("素菜", "#54B65B"), ("主食", "#FF8133"), ("其他", "#AAAAB2"))]
    for nutrient in ("fat", "protein"):
        totals = defaultdict(float)
        complete = [dishes for dishes in recognized if all(type(dish.get(nutrient)) in (int, float)
                    and math.isfinite(dish[nutrient]) and dish[nutrient] >= 0 for dish in dishes.values())]
        for dishes in complete:
            for dish_id, dish in dishes.items():
                totals[dish_id] += dish[nutrient]
        total = sum(totals.values())
        if total:
            top_id = max(totals, key=lambda key: (totals[key], -key))
            result["contributors"][nutrient] = {"name": names[top_id], "share": round(totals[top_id] / total * 100, 1),
                                                "meal_count": len(complete)}
    top_share = round(sum(dish_counts[dish_id] for dish_id in top) * 100 / selections, 1)
    variety.update(status=f"选过 {len(dish_counts)} 种菜品",
                   description=f"共记录 {selections} 次菜品选择，最常选“{names[top[0]]}” {dish_counts[top[0]]} 次。前{len(top)}道占 {top_share:g}%。",
                   evidence={"recognized_meals": len(recognized), "selection_count": selections, "unique_dishes": len(dish_counts),
                             "top_share": top_share, "top_dishes": [{"id": key, "name": names[key], "count": dish_counts[key]} for key in top]})
    categories = [{dish["category"] for dish in dishes.values()} for dishes in recognized]
    paired = sum({"荤菜", "素菜"} <= values for values in categories)
    staple = sum("主食" in values for values in categories)
    pairing.update(status=f"荤素搭配 {paired / len(recognized):.0%}",
                   description=f"{len(recognized)} 餐有效识别中，{paired} 餐同时识别到荤菜与素菜，{staple} 餐识别到主食。",
                   evidence={"recognized_meals": len(recognized), "paired_meals": paired, "staple_meals": staple})
    group_counts = Counter()
    tagged_meals = tagged_selections = 0
    for dishes in recognized:
        meal_groups = set()
        for dish in dishes.values():
            tags = set(dish.get("food_groups") or []) & set(FOOD_GROUPS)
            tagged_selections += bool(tags)
            meal_groups.update(tags)
        if meal_groups:
            tagged_meals += 1
            group_counts.update(meal_groups)
    group_evidence = {"recognized_meals": len(recognized), "tagged_meals": tagged_meals, "selection_count": selections,
                      "tagged_selections": tagged_selections, "group_meal_counts": dict(group_counts)}
    if group_counts:
        top_groups = sorted(group_counts, key=lambda group: (-group_counts[group], FOOD_GROUPS.index(group)))[:2]
        copy = "、".join(f"{group}出现 {group_counts[group]} 餐" for group in top_groups)
        groups.update(status="常选：" + "、".join(top_groups), evidence=group_evidence,
                      description=f"已标注的 {tagged_meals} 餐中，{copy}。标签覆盖 {tagged_selections}/{selections} 次菜品选择。")
    else:
        groups.update(status="待补充标签", evidence=group_evidence, description="菜品尚未标注食物组，暂不判断细分类选择倾向。")
    return result


def period_dining_features(requests, config=None):
    """Bulk-load evidence once for (request key, student id, start, end) tuples."""
    if not requests:
        return {}
    cfg = config if config is not None else get_effective_config(current_app.config)
    try:
        tz = ZoneInfo(cfg.get("APP_TIMEZONE") or "Asia/Shanghai")
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("Asia/Shanghai")
    start, end = min(row[2] for row in requests), max(row[3] for row in requests)
    ids = {row[1] for row in requests}
    lower, upper = datetime.combine(start, time.min, tz), datetime.combine(end + timedelta(days=1), time.min, tz)
    by_student = defaultdict(dict)

    def meal_for(sid, timestamp):
        if timestamp is None:
            return None, None
        local = timestamp.replace(tzinfo=tz) if timestamp.tzinfo is None else timestamp.astimezone(tz)
        if not start <= local.date() <= end:
            return None, None
        slot = resolve_meal_slot_for_datetime(local, str(tz), cfg) or "unknown"
        return by_student[sid].setdefault((local.date(), slot), {}), local.hour * 60 + local.minute

    transactions = apply_enabled_transaction_location_filter(ConsumptionRecord.query.filter(
        ConsumptionRecord.student_id.in_(ids), ConsumptionRecord.amount < 0,
        ConsumptionRecord.transaction_time >= lower, ConsumptionRecord.transaction_time < upper,
    ), cfg).with_entities(ConsumptionRecord.student_id, ConsumptionRecord.transaction_time).all()
    for sid, timestamp in transactions:
        meal, minutes = meal_for(sid, timestamp)
        if meal is not None:
            meal.setdefault("transaction_times", []).append(minutes)
    matches = MatchResult.query.options(joinedload(MatchResult.image), joinedload(MatchResult.consumption_record)).filter(
        MatchResult.student_id.in_(ids), MatchResult.match_date >= start - timedelta(days=1), MatchResult.match_date <= end + timedelta(days=1),
        MatchResult.image_id.isnot(None), MatchResult.status.in_([MatchStatusEnum.matched, MatchStatusEnum.confirmed]),
    ).order_by(MatchResult.id).all()
    image_ids = {match.image_id for match in matches}
    recognitions = DishRecognition.query.join(Dish).with_entities(DishRecognition.image_id, Dish.id, Dish.name, Dish.category, Dish.food_groups, Dish.fat, Dish.protein).filter(
        DishRecognition.image_id.in_(image_ids), DishRecognition.is_low_confidence.is_(False), DishRecognition.dish_id.isnot(None),
    ).all() if image_ids else []
    by_image = defaultdict(dict)
    for image_id, dish_id, name, category, food_groups, fat, protein in recognitions:
        by_image[image_id][dish_id] = {
            "name": name, "category": category.value, "food_groups": list(food_groups or []),
            "fat": float(fat) if fat is not None else None, "protein": float(protein) if protein is not None else None,
        }
    owners = defaultdict(set)
    owner_rows = MatchResult.query.with_entities(MatchResult.image_id, MatchResult.student_id).filter(
        MatchResult.image_id.in_(image_ids), MatchResult.student_id.isnot(None),
        MatchResult.status.in_([MatchStatusEnum.matched, MatchStatusEnum.confirmed]),
    ).all() if image_ids else []
    for image_id, student_id in owner_rows:
        owners[image_id].add(student_id)
    processed = set()
    locations = get_enabled_transaction_location_ids(cfg)
    for match in matches:
        if match.image_id in processed or len(owners[match.image_id]) != 1:
            continue
        transaction = match.consumption_record
        if transaction is not None and ((locations and transaction.channel_id not in locations) or transaction.amount >= 0):
            continue
        timestamp = (transaction.transaction_time if transaction else None) or match.captured_at or (match.image.captured_at if match.image else None)
        meal, minutes = meal_for(match.student_id, timestamp)
        if meal is None:
            continue
        processed.add(match.image_id)
        meal.setdefault("transaction_times" if transaction else "capture_times", []).append(minutes)
        meal.setdefault("dishes", {}).update(by_image[match.image_id])
    # Persisted nutrition logs carry observed slot evidence but no reliable timestamps.
    logs = NutritionLog.query.filter(NutritionLog.student_id.in_(ids), NutritionLog.log_date >= start, NutritionLog.log_date <= end).all()
    for log in logs:
        totals = log.nutrient_totals or {}
        if log.meal_count and totals.get("_quality_version") in (2, 3):
            for slot in totals.get("_meal_slots") or []:
                by_student[log.student_id].setdefault((log.log_date, slot), {})
    labels = {slot["key"]: slot["label"] for slot in get_meal_slots(cfg)}
    return {key: summarize_dining_features({pair: meal for pair, meal in by_student[sid].items() if first <= pair[0] <= last}, labels)
            for key, sid, first, last in requests}
