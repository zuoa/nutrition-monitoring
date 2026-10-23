"""Period sports evidence and the immediately preceding personal report snapshot."""

from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
import math
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app

from app.models import Report, ReportTypeEnum, SportRecord, Student
from app.services.report_assessment import report_nutrient_values
from app.services.runtime_config import get_effective_config
from app.services.sport_ingestion_service import SPORT_NAMES


def previous_report_period(start, end, report_type=None):
    monthly = bool(report_type and report_type.endswith("_monthly")) or (report_type is None and (end - start).days > 7)
    if monthly:
        last = start.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last, "personal_monthly"
    return start - timedelta(days=7), end - timedelta(days=7), "personal_weekly"


def _positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def summarize_sports(records, start, end, previous_start, previous_end, tz):
    current, previous = [], []
    measurements = {}
    for record in records:
        day = datetime.fromtimestamp(record.start_time / 1000, tz).date()
        if day > end:
            continue
        raw = record.raw_result or {}
        for key in ("height", "weight"):
            if _positive(raw.get(key)) and record.start_time >= measurements.get(key, {}).get("timestamp", -1):
                measurements[key] = {"value": raw[key], "date": day.isoformat(), "timestamp": record.start_time}
        if start <= day <= end:
            current.append(record)
        elif previous_start <= day <= previous_end:
            previous.append(record)
    # Lung capacity and BMI measurements are not exercise sessions, even if a
    # device supplied practice mode. Test mode never inflates practice counts.

    def practices(rows):
        return Counter(row.sport_type for row in rows if row.mode == 1 and row.sport_type not in (9, 10))

    counts, old_counts = practices(current), practices(previous)
    total, old_total = sum(counts.values()), sum(old_counts.values())
    projects = [{"sport_type": key, "name": SPORT_NAMES.get(key, f"项目 {key}"), "count": count,
                 "previous_count": old_counts[key], "delta": count - old_counts[key], "share": round(count / total * 100, 1)}
                for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]
    return {"version": 1, "practice_count": total, "previous_practice_count": old_total,
            "practice_delta": total - old_total, "project_count": len(counts), "previous_project_count": len(old_counts),
            "test_count": sum(row.mode == 2 for row in current), "top_projects": projects[:3], "projects": projects,
            "has_records": bool(current), "previous_has_records": bool(previous),
            "measurements": {key: {"value": value["value"], "date": value["date"]} for key, value in measurements.items()}}


def period_health_context(requests, config=None):
    """Batch (key, student id, start, end, optional type) without changing reports."""
    if not requests:
        return {}
    cfg = config if config is not None else get_effective_config(current_app.config)
    try:
        tz = ZoneInfo(cfg.get("APP_TIMEZONE") or "Asia/Shanghai")
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("Asia/Shanghai")
    periods = {row[0]: previous_report_period(row[2], row[3], row[4] if len(row) > 4 else None) for row in requests}
    ids = {row[1] for row in requests}
    students = {student.id: student.student_no for student in Student.query.filter(Student.id.in_(ids)).all()}
    first, last = min(value[0] for value in periods.values()), max(row[3] for row in requests)
    lower = int(datetime.combine(first, time.min, tz).timestamp() * 1000)
    upper = int(datetime.combine(last + timedelta(days=1), time.min, tz).timestamp() * 1000)
    by_person = defaultdict(list)
    if students:
        rows = SportRecord.query.filter(SportRecord.person_id.in_(set(students.values()) - {""}),
                                        SportRecord.start_time >= lower, SportRecord.start_time < upper).all()
        for record in rows:
            by_person[record.person_id].append(record)
        # Carry forward the latest available BMI measurement before the period.
        # A later measurement can never leak into an earlier report.
        for record in SportRecord.query.filter(SportRecord.person_id.in_(set(students.values()) - {""}),
                                               SportRecord.sport_type == 10, SportRecord.start_time < lower).order_by(SportRecord.start_time).all():
            by_person[record.person_id].append(record)
    snapshots = {}
    for report in Report.query.filter(Report.target_id.in_({str(sid) for sid in ids}),
                                      Report.report_type.in_([ReportTypeEnum.personal_weekly, ReportTypeEnum.personal_monthly]),
                                      Report.period_start >= first, Report.period_end < max(row[2] for row in requests)).order_by(Report.created_at.desc(), Report.id.desc()).all():
        snapshots.setdefault((report.target_id, report.report_type.value, report.period_start, report.period_end), report.content or {})
    result = {}
    for request in requests:
        key, sid, start, end = request[:4]
        old_start, old_end, kind = periods[key]
        old = snapshots.get((str(sid), kind, old_start, old_end))
        result[key] = {
            "sports": summarize_sports(by_person[students[sid]] if sid in students else [], start, end, old_start, old_end, tz),
            "period_comparison": {"version": 1, "period_start": old_start.isoformat(), "period_end": old_end.isoformat(),
                                  "has_report": old is not None,
                                  "meal_count": (old.get("meal_statistics") or {}).get("meal_count") if old is not None else None,
                                  "nutrients": report_nutrient_values(old) if old is not None else {}},
        }
    return result
