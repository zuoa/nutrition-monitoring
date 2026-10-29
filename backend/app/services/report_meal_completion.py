"""Complete meal attendance from independent evidence and conservative history."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import math
from statistics import median
from types import SimpleNamespace
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import joinedload

from app.models import DailyMenu, DishRecognition, MatchResult, MatchStatusEnum, NutritionLog, Student
from app.models.menu import get_meal_slot_keys, get_meal_slots, resolve_meal_slot_for_datetime
from app.nutrition_metadata import NUTRITION_FIELD_KEYS
from app.services.consumption_location_filter import get_enabled_transaction_location_ids

MEAL_COMPLETION_VERSION = 1
HISTORY_DAYS = 56
MIN_PERSONAL_DAYS = 8
MIN_PEERS = 5
MAIN_SLOTS = frozenset(("breakfast", "lunch", "dinner"))


@dataclass
class CompletedMeals:
    attendance: dict
    nutrition_logs: dict


def complete_attendance(evidence, student_classes, student_ids, start, end, slots, *, as_of=None, slot_ends=None, serving_slots=None):
    """Use observed events only to learn habits; inferred meals never train the model."""
    active = defaultdict(set)
    present = defaultdict(set)
    class_active = defaultdict(set)
    class_present = defaultdict(set)
    by_student = defaultdict(list)
    for (sid, day, slot), sources in evidence.items():
        if start <= day <= end:
            by_student[sid].append({"date": day.isoformat(), "slot": slot, "source": "observed", "sources": sorted(sources)})
        if slot not in slots:
            continue
        active[sid].add(day)
        present[sid, slot].add(day)
        class_id = student_classes.get(sid)
        if class_id is not None:
            class_active[class_id, day].add(sid)
            class_present[class_id, day, slot].add(sid)
    result = {}
    for sid in student_ids:
        records, skipped = list(by_student[sid]), []
        for offset in range((end - start).days + 1):
            day = start + timedelta(days=offset)
            # A whole-day absence cannot be distinguished from being off campus.
            if day not in active[sid]:
                continue
            history = [past for past in active[sid] if day - timedelta(days=HISTORY_DAYS) <= past < day
                       and (past.weekday() < 5) == (day.weekday() < 5)]
            if len(history) < MIN_PERSONAL_DAYS:
                continue
            for slot in sorted(MAIN_SLOTS.intersection(slots)):
                if day in (serving_slots or {}) and slot not in serving_slots[day]:
                    continue
                if as_of is not None and (day > as_of.date() or (day == as_of.date() and as_of.time().replace(tzinfo=None) <= (slot_ends or {}).get(slot, time.max))):
                    continue
                if (sid, day, slot) in evidence:
                    continue
                hits = sum(past in present[sid, slot] for past in history)
                probability = (hits + 1) / (len(history) + 2)
                class_id = student_classes.get(sid)
                peers = class_active[class_id, day] - {sid}
                if class_id is None or len(peers) < MIN_PEERS:
                    continue
                today_rate = len(class_present[class_id, day, slot] & peers) / len(peers)
                if probability <= .2 and today_rate >= .6:
                    skipped.append({"date": day.isoformat(), "slot": slot, "source": "likely_skipped", "confidence": round(1 - probability, 3)})
                    continue
                if probability < .85:
                    continue
                rates = []
                for past in history:
                    past_peers = class_active[class_id, past] - {sid}
                    if len(past_peers) >= MIN_PEERS:
                        rates.append(len(class_present[class_id, past, slot] & past_peers) / len(past_peers))
                if len(rates) < MIN_PERSONAL_DAYS:
                    continue
                normal_rate = median(rates)
                # Isolated personal gaps with healthy peer records may be skipped meals.
                confidence = probability * max(0, 1 - today_rate / normal_rate) if normal_rate >= .6 else 0
                if confidence >= .8:
                    records.append({"date": day.isoformat(), "slot": slot, "source": "estimated", "confidence": round(confidence, 3)})
        records.sort(key=lambda row: (row["date"], row["slot"]))
        result[sid] = summarize_attendance(records, skipped)
    return result


def summarize_attendance(records, skipped):
    return {
        "completion_version": MEAL_COMPLETION_VERSION,
        "meal_count": sum(row["slot"] != "unknown" for row in records),
        "breakfast_count": sum(row["slot"] == "breakfast" for row in records),
        "observed_count": sum(row.get("source") != "estimated" and row["slot"] != "unknown" for row in records),
        "estimated_count": sum(row.get("source") == "estimated" for row in records),
        "recorded_days": len({row["date"] for row in records}),
        "unknown_count": sum(row["slot"] == "unknown" for row in records),
        "records": records, "likely_skipped": skipped,
    }


def report_period_completed_meals(requests, cfg):
    """Backfill old snapshots in one retrospective batch, without persisting estimates."""
    if not requests:
        return {}
    completed = complete_report_meals([sid for _, sid, _, _ in requests],
                                      min(start for _, _, start, _ in requests), max(end for _, _, _, end in requests), cfg)
    result = {}
    for report_id, sid, start, end in requests:
        original = completed.attendance[sid]
        records = [row for row in original["records"] if start.isoformat() <= row["date"] <= end.isoformat()]
        skipped = [row for row in original["likely_skipped"] if start.isoformat() <= row["date"] <= end.isoformat()]
        stats = {**summarize_attendance(records, skipped), "slots": original["slots"],
                 "nutrition_estimated_count": sum(bool(row.get("estimated_nutrients")) for row in records)}
        result[report_id] = CompletedMeals({sid: stats}, {sid: [log for log in completed.nutrition_logs.get(sid, []) if start <= log.log_date <= end]})
    return result


def enrich_report_meals(content, completion, sid):
    from app.services.nutrition_service import _calculate_average_nutrients, _daily_series, _energy_summary
    from app.services.report_nutrient_gains import adjust_report_logs, get_report_nutrient_gains
    from app.services.report_projection import project_meals

    stats = dict(completion.attendance[sid])
    previous = content.get("meal_statistics") or {}
    records = {(row["date"], row["slot"]): row for row in stats["records"]}
    for row in previous.get("records", []):
        key = (row["date"], row["slot"])
        if key not in records or records[key].get("source") == "estimated":
            records[key] = {**row, "source": row.get("source", "observed")}
    skipped = [row for row in stats["likely_skipped"] if (row["date"], row["slot"]) not in records]
    stats.update(summarize_attendance(sorted(records.values(), key=lambda row: (row["date"], row["slot"])), skipped))
    for key in ("meal_count", "breakfast_count", "recorded_days"):
        stats[key] = max(stats[key], previous.get(key, 0))
    content["meal_statistics"] = stats
    logs = [log for log in completion.nutrition_logs.get(sid, []) if log.meal_count > 0]
    if not logs:
        return
    # Historical reports retain the factors from their own generation snapshot.
    gains = get_report_nutrient_gains({"REPORT_NUTRIENT_GAINS": content.get("report_nutrient_gains")})
    logs = adjust_report_logs(logs, gains)
    start, end = date.fromisoformat(content["period_start"]), date.fromisoformat(content["period_end"])
    projection = project_meals(logs, start, end)
    if not any(value is not None for value in projection["avg_nutrients"].values()):
        return
    content.update(meal_projection=projection, daily_series=_daily_series(logs, start, end), energy_summary=_energy_summary(logs))
    content["nutrition_records"] = [{"date": log.log_date.isoformat(), "slot": slot} for log in logs for slot in (log.nutrient_totals or {}).get("_meal_slots", [])]
    averages, counts = _calculate_average_nutrients(logs)
    content["avg_nutrients"] = {**averages, **{key: value for key, value in (content.get("avg_nutrients") or {}).items() if value is not None}}
    original_counts = content.get("nutrient_sample_counts") or {}
    content["nutrient_sample_counts"] = {key: original_counts.get(key) or value for key, value in counts.items()}
    content["coverage"] = {**(content.get("coverage") or {}), "recorded_days": stats["recorded_days"],
                           "nutrition_days": sum(any(_nutrient_number((log.nutrient_totals or {}).get(key)) is not None for key in NUTRITION_FIELD_KEYS) for log in logs)}


def complete_report_meals(student_ids, start, end, cfg, *, include_nutrition=True):
    """Collect one retrospective batch for requested students and their classmates."""
    from app.services.report_meals import _meal_pairs

    ids = set(student_ids)
    if not ids:
        return CompletedMeals({}, {})
    targets = Student.query.filter(Student.id.in_(ids)).with_entities(Student.id, Student.class_id).all()
    classes = {class_id for _, class_id in targets if class_id is not None}
    cohort = Student.query.filter(Student.class_id.in_(classes), Student.is_active.is_(True)).with_entities(Student.id, Student.class_id).all() if classes else []
    student_classes = dict([*targets, *cohort])
    all_ids = ids | student_classes.keys()
    history_start = start - timedelta(days=HISTORY_DAYS)
    evidence = defaultdict(set)
    for sid, pairs in _meal_pairs(all_ids, history_start, end, cfg).items():
        for day, slot in pairs:
            evidence[sid, day, slot].add("consumption")
    keys = set(get_meal_slot_keys(cfg))
    logs = NutritionLog.query.filter(NutritionLog.student_id.in_(all_ids), NutritionLog.log_date >= history_start, NutritionLog.log_date <= end).all()
    for log in logs:
        totals = log.nutrient_totals or {}
        if totals.get("_quality_version") in (2, 3):
            slots = set(totals.get("_meal_slots") or []) if log.meal_count else set()
            if totals.get("_quality_version") == 3:
                slots.update(meal.get("slot") for meal in totals.get("_meals") or []
                             if any(_nutrient_number(value) is not None and float(value) > 0 for value in (meal.get("nutrients") or {}).values()))
            for slot in slots:
                if slot in keys or slot == "unknown":
                    evidence[log.student_id, log.log_date, slot].add("nutrition")
    matches = MatchResult.query.options(joinedload(MatchResult.image), joinedload(MatchResult.consumption_record)).filter(
        MatchResult.student_id.in_(all_ids), MatchResult.match_date >= history_start - timedelta(days=1),
        MatchResult.match_date <= end + timedelta(days=1), MatchResult.image_id.isnot(None),
        MatchResult.status.in_([MatchStatusEnum.matched, MatchStatusEnum.confirmed]),
    ).all()
    try:
        tz = ZoneInfo(cfg.get("APP_TIMEZONE") or "Asia/Shanghai")
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("Asia/Shanghai")
    locations = get_enabled_transaction_location_ids(cfg)
    valid_matches = []
    for match in matches:
        transaction = match.consumption_record
        if transaction is not None and locations and transaction.channel_id not in locations:
            continue
        timestamp = (transaction.transaction_time if transaction else None) or match.captured_at or (match.image.captured_at if match.image else None)
        if timestamp is None:
            continue
        local = timestamp.replace(tzinfo=tz) if timestamp.tzinfo is None else timestamp.astimezone(tz)
        if history_start <= local.date() <= end:
            slot = resolve_meal_slot_for_datetime(local, str(tz), cfg) or "unknown"
            evidence[match.student_id, local.date(), slot].add("recognition")
            valid_matches.append((match, local.date()))
    menus = DailyMenu.query.filter(DailyMenu.menu_date >= start, DailyMenu.menu_date <= end, DailyMenu.is_default.is_(False)).all()
    serving_slots = {menu.menu_date: {slot for slot, dishes in menu.normalized_meal_dish_ids(cfg).items() if dishes} for menu in menus}
    snapshots = complete_attendance(evidence, student_classes, ids, start, end, keys, as_of=datetime.now(tz), serving_slots=serving_slots,
                                    slot_ends={slot["key"]: time.fromisoformat(slot["end"]) for slot in get_meal_slots(cfg)})
    for snapshot in snapshots.values():
        snapshot["slots"] = [{"key": slot["key"], "label": slot["label"]} for slot in get_meal_slots(cfg)]
    completed_logs = complete_nutrition(logs, valid_matches, snapshots, student_classes, cfg, start, end) if include_nutrition else {}
    return CompletedMeals(snapshots, completed_logs)


def _nutrient_number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except (TypeError, ValueError):
        return None


def _donor_meals(logs):
    meals = {}
    opaque = {}
    for log in logs:
        totals = log.nutrient_totals or {}
        slots = set(totals.get("_meal_slots") or [])
        if totals.get("_quality_version") == 3:
            for meal in totals.get("_meals") or []:
                meals[log.student_id, log.log_date, meal.get("slot")] = dict(meal.get("nutrients") or {})
        elif totals.get("_quality_version") == 2 and len(slots) == 1:
            meals[log.student_id, log.log_date, next(iter(slots))] = {key: totals.get(key) for key in NUTRITION_FIELD_KEYS}
        else:
            opaque[log.student_id, log.log_date] = log
    return meals, opaque


def nutrient_donor_index(meals, student_classes):
    personal, peers = defaultdict(list), defaultdict(list)
    for (sid, day, slot), nutrients in meals.items():
        personal[sid, slot].append((sid, day, nutrients))
        class_id = student_classes.get(sid)
        if class_id is not None:
            peers[class_id, slot].append((sid, day, nutrients))
    return personal, peers


def estimate_nutrients(sid, day, slot, donors, student_classes):
    """Medians use observed donors only, never future or already completed meals."""
    personal = []
    peers_today, peers_recent = [], []
    class_id = student_classes.get(sid)
    personal_index, peer_index = donors
    candidates = [*personal_index.get((sid, slot), []), *peer_index.get((class_id, slot), [])]
    seen = set()
    for owner, past, nutrients in candidates:
        if (owner, past) in seen or past > day or day - past > timedelta(days=HISTORY_DAYS):
            continue
        seen.add((owner, past))
        if owner == sid and past < day and (past.weekday() < 5) == (day.weekday() < 5):
            personal.append(nutrients)
        elif owner != sid and class_id is not None and student_classes.get(owner) == class_id:
            if past == day:
                peers_today.append((owner, nutrients))
            elif day - past <= timedelta(days=14) and (past.weekday() < 5) == (day.weekday() < 5):
                peers_recent.append((owner, nutrients))
    estimated, sources = {}, {}
    for key in NUTRITION_FIELD_KEYS:
        values = [number for item in personal if (number := _nutrient_number(item.get(key))) is not None]
        source = "student_history"
        if len(values) < 3:
            candidates = [(owner, number) for owner, item in peers_today if (number := _nutrient_number(item.get(key))) is not None]
            if len({owner for owner, _ in candidates}) < 3:
                candidates = [(owner, number) for owner, item in peers_recent if (number := _nutrient_number(item.get(key))) is not None]
                if len(candidates) < 5 or len({owner for owner, _ in candidates}) < 3:
                    candidates = []
            values, source = [number for _, number in candidates], "class_peers"
        if values:
            estimated[key] = round(median(values), 2)
            sources[key] = source
    return estimated, sources


def complete_nutrition(logs, matches, snapshots, student_classes, cfg, start, end):
    from app.services.nutrition_service import _nutrition_snapshot

    meals, opaque = _donor_meals(logs)
    by_day = defaultdict(list)
    for match, day in matches:
        by_day[match.student_id, day].append(match)
    image_ids = {match.image_id for match, _ in matches}
    recognitions = DishRecognition.query.options(joinedload(DishRecognition.dish)).filter(DishRecognition.image_id.in_(image_ids)).all() if image_ids else []
    by_image = defaultdict(list)
    for rec in recognitions:
        by_image[rec.image_id].append(rec)
    for (sid, day), items in by_day.items():
        snapshot = _nutrition_snapshot(items, by_image, cfg, day)
        recovered, _ = _donor_meals([SimpleNamespace(student_id=sid, log_date=day, nutrient_totals=snapshot.nutrient_totals)])
        for key, nutrients in recovered.items():
            # Existing complete snapshots remain authoritative; recover missing fields.
            previous = meals.setdefault(key, {})
            for nutrient, value in nutrients.items():
                if _nutrient_number(previous.get(nutrient)) is None and _nutrient_number(value) is not None:
                    previous[nutrient] = value
        if any(any(_nutrient_number(value) is not None for value in nutrients.values()) for nutrients in recovered.values()):
            opaque.pop((sid, day), None)
    output = defaultdict(list)
    donors = nutrient_donor_index(meals, student_classes)
    for sid, stats in snapshots.items():
        days = defaultdict(list)
        skipped_by_day = defaultdict(set)
        for record in stats["likely_skipped"]:
            skipped_by_day[date.fromisoformat(record["date"])].add(record["slot"])
        estimated_count = 0
        for record in stats["records"]:
            day = date.fromisoformat(record["date"])
            slot = record["slot"]
            if (sid, day) in opaque:
                continue
            observed = meals.get((sid, day, slot), {})
            values = {key: _nutrient_number(observed.get(key)) for key in NUTRITION_FIELD_KEYS}
            estimates, sources = estimate_nutrients(sid, day, slot, donors, student_classes) if slot != "unknown" else ({}, {})
            filled = {}
            for key, value in estimates.items():
                if values[key] is None:
                    values[key] = value
                    filled[key] = sources[key]
            if filled:
                estimated_count += 1
                record["estimated_nutrients"] = filled
            days[day].append({"slot": slot, "nutrients": values, "estimated_nutrients": filled})
        stats["nutrition_estimated_count"] = estimated_count
        dates = {date.fromisoformat(row["date"]) for row in stats["records"]}
        for day in sorted(dates):
            if (sid, day) in opaque:
                original = opaque[sid, day]
                output[sid].append(SimpleNamespace(student_id=sid, log_date=day, meal_count=original.meal_count, dish_ids=original.dish_ids,
                                                   nutrient_totals={**(original.nutrient_totals or {}), "_skipped_meal_slots": sorted(skipped_by_day[day])}))
                continue
            items = days[day]
            totals = {key: round(sum(meal["nutrients"][key] for meal in items), 2)
                      if items and all(meal["nutrients"][key] is not None for meal in items) else None for key in NUTRITION_FIELD_KEYS}
            output[sid].append(SimpleNamespace(student_id=sid, log_date=day, meal_count=len(items), dish_ids=[], nutrient_totals={
                **totals, "_quality_version": 3, "_meal_slots": sorted({meal["slot"] for meal in items}), "_meals": items,
                "_skipped_meal_slots": sorted(skipped_by_day[day]),
            }))
        completed_days = {log.log_date for log in output[sid]}
        output[sid].extend(log for log in logs if log.student_id == sid and start <= log.log_date <= end and log.log_date not in completed_days)
        output[sid].sort(key=lambda log: log.log_date)
    return dict(output)
