"""Consumption-based attendance, independent of recognition and matching."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app

from app.models import ConsumptionRecord
from app.models.menu import resolve_meal_slot_for_datetime
from app.services.consumption_location_filter import apply_enabled_transaction_location_filter
from app.services.runtime_config import get_effective_config


def report_meal_statistics(student_ids, period_start, period_end, config=None):
    """One query for a cohort; a local date/slot pair counts as one meal."""
    pairs = _meal_pairs(student_ids, period_start, period_end, config)
    return {sid: _summarize(slots) for sid, slots in pairs.items()}


def report_period_meal_statistics(requests, config=None):
    """Fetch once even when a report list spans multiple periods/students.

    requests contains (report_id, student_id, start_date, end_date) tuples.
    """
    if not requests:
        return {}
    pairs = _meal_pairs(
        [sid for _, sid, _, _ in requests],
        min(start for _, _, start, _ in requests),
        max(end for _, _, _, end in requests), config,
    )
    return {report_id: _summarize({(day, slot) for day, slot in pairs[sid] if start <= day <= end})
            for report_id, sid, start, end in requests}


def _summarize(slots):
    return {"meal_count": len(slots), "breakfast_count": sum(slot == "breakfast" for _, slot in slots)}


def _meal_pairs(student_ids, period_start, period_end, config=None):
    ids = set(student_ids)
    meals = {sid: set() for sid in ids}
    if not ids:
        return meals
    cfg = config if config is not None else get_effective_config(current_app.config)
    try:
        tz = ZoneInfo(cfg.get("APP_TIMEZONE") or "Asia/Shanghai")
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("Asia/Shanghai")
    lower = datetime.combine(period_start, time.min, tzinfo=tz)
    upper = datetime.combine(period_end + timedelta(days=1), time.min, tzinfo=tz)
    query = ConsumptionRecord.query.filter(
        ConsumptionRecord.student_id.in_(ids),
        ConsumptionRecord.amount < 0,
        ConsumptionRecord.transaction_time >= lower,
        ConsumptionRecord.transaction_time < upper,
    )
    rows = apply_enabled_transaction_location_filter(query, cfg).with_entities(
        ConsumptionRecord.student_id, ConsumptionRecord.transaction_time,
    ).all()
    for sid, timestamp in rows:
        local = timestamp.replace(tzinfo=tz) if timestamp.tzinfo is None else timestamp.astimezone(tz)
        if not period_start <= local.date() <= period_end:
            continue
        slot = resolve_meal_slot_for_datetime(local, str(tz), cfg)
        if slot:
            meals[sid].add((local.date(), slot))
    return meals
