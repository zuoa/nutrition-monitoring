import logging
import math
from collections import defaultdict
from types import SimpleNamespace
from sqlalchemy.orm import joinedload
from datetime import date, timedelta
from app import db
from app.models import NutritionLog, MatchResult, DishRecognition, MatchStatusEnum, Student
from app.nutrition_metadata import DAILY_RECOMMENDED
from app.services.report_reference import GENERAL_SUGGESTIONS, reference_snapshot
from app.services.report_meals import report_meal_snapshots
from app.services.report_projection import aggregate_projections, project_meals
from app.models.menu import resolve_meal_slot_for_datetime
from app.services.runtime_config import get_effective_config
from flask import current_app

logger = logging.getLogger(__name__)

ALERT_DAYS_THRESHOLD = 5
ALERT_SKIP_MEAL_DAYS = 3


def nutrient_number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except (ValueError, TypeError):
        return None


def _calculate_average_nutrients(meal_days):
    values = {key: [] for key in DAILY_RECOMMENDED}
    for log in meal_days:
        for key in values:
            value = nutrient_number((log.nutrient_totals or {}).get(key))
            if value is not None:
                values[key].append(value)
    return ({key: round(sum(items) / len(items), 1) if items else None for key, items in values.items()},
            {key: len(items) for key, items in values.items()})


def _daily_series(logs, start, end):
    by_date = defaultdict(list)
    for log in logs:
        if log.meal_count > 0:
            by_date[log.log_date].append(log)
    result = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        values, counts = _calculate_average_nutrients(by_date[day])
        result.append({"date": day.isoformat(), "nutrients": values, "sample_counts": counts})
    return result


def _energy_summary(logs):
    # A shared sample is essential: ratios of averages with different denominators mislead.
    keys = ("carbohydrate", "fat", "protein")
    complete = [log for log in logs if all(nutrient_number((log.nutrient_totals or {}).get(key)) is not None for key in keys)]
    if not complete:
        return {"sample_count": 0, "percentages": None}
    means, _ = _calculate_average_nutrients(complete)
    energy = [means[key] * factor for key, factor in zip(keys, (4, 9, 4))]
    total = sum(energy)
    if total <= 0:
        return {"sample_count": len(complete), "percentages": None}
    pct = [round(value * 100 / total) for value in energy]
    pct[pct.index(max(pct))] += 100 - sum(pct)
    return {"sample_count": len(complete), "percentages": dict(zip(keys, pct))}


def _student_organization(student):
    cls = student.class_
    campus = cls.grade.stage.campus if cls and cls.grade and cls.grade.stage else None
    if campus:
        return f"{campus.school.name} · {campus.name}" if campus.school else campus.name
    return "校园膳食营养监测"


def _nutrition_snapshot(matches, by_image, cfg, log_date):
    totals = {key: 0.0 for key in DAILY_RECOMMENDED}
    complete = {key: True for key in totals}
    dish_ids, processed_images, nutrition_slots = set(), set(), set()
    meal_totals, meal_complete = {}, {}
    for match in matches:
        if match.image_id in processed_images:
            continue
        processed_images.add(match.image_id)
        timestamp = (match.consumption_record.transaction_time if match.consumption_record else None) or match.captured_at or (match.image.captured_at if match.image else None)
        slot = resolve_meal_slot_for_datetime(timestamp, cfg.get("APP_TIMEZONE") or "Asia/Shanghai", cfg) or "unknown"
        if slot not in meal_totals:
            meal_totals[slot] = {key: 0. for key in totals}
            meal_complete[slot] = {key: True for key in totals}
        image_recs = by_image.get(match.image_id, [])
        valid = [rec for rec in image_recs if rec.dish and not rec.is_low_confidence]
        if not valid or len(valid) != len(image_recs):
            complete = {key: False for key in totals}
            meal_complete[slot] = {key: False for key in totals}
        if valid:
            if any(nutrient_number(getattr(rec.dish, key)) is not None for rec in valid for key in totals):
                nutrition_slots.add(slot)
        for rec in valid:
            dish_ids.add(rec.dish_id)
            for key in totals:
                value = nutrient_number(getattr(rec.dish, key))
                if value is None:
                    complete[key] = False
                    meal_complete[slot][key] = False
                else:
                    totals[key] += value
                    meal_totals[slot][key] += value
    nutrients = {
        **{key: round(value, 2) if complete[key] and dish_ids else None for key, value in totals.items()},
        "_quality_version": 3, "_meal_slots": sorted(nutrition_slots),
        "_meals": [{"slot": slot, "nutrients": {
            key: round(value, 2) if meal_complete[slot][key] else None
            for key, value in meal_totals[slot].items()
        }} for slot in sorted(meal_totals)],
    }

    return SimpleNamespace(log_date=log_date, nutrient_totals=nutrients, meal_count=len(nutrition_slots), dish_ids=sorted(dish_ids))


def _projection_logs(logs, start, end):
    """Reconstruct older days in two bulk reads, without rewriting stored logs."""
    keys = {(log.student_id, log.log_date) for log in logs if (log.nutrient_totals or {}).get("_quality_version") != 3}
    if not keys:
        return logs
    matches = MatchResult.query.options(joinedload(MatchResult.image), joinedload(MatchResult.consumption_record)).filter(
        MatchResult.student_id.in_({sid for sid, _ in keys}), MatchResult.match_date >= start, MatchResult.match_date <= end,
        MatchResult.status.in_([MatchStatusEnum.matched, MatchStatusEnum.confirmed]),
    ).all()
    by_day = defaultdict(list)
    for match in matches:
        if (match.student_id, match.match_date) in keys:
            by_day[(match.student_id, match.match_date)].append(match)
    image_ids = {match.image_id for items in by_day.values() for match in items if match.image_id}
    recs = DishRecognition.query.options(joinedload(DishRecognition.dish)).filter(DishRecognition.image_id.in_(image_ids)).all() if image_ids else []
    by_image = defaultdict(list)
    for rec in recs:
        by_image[rec.image_id].append(rec)
    cfg = get_effective_config(current_app.config)
    result = []
    for log in logs:
        items = by_day.get((log.student_id, log.log_date))
        if items:
            snapshot = _nutrition_snapshot(items, by_image, cfg, log.log_date)
            snapshot.student_id = log.student_id
            # Retain an empty reconstructed day so it counts as excluded.
            snapshot.meal_count = max(snapshot.meal_count, log.meal_count)
            result.append(snapshot)
        else:
            result.append(log)
    return result


class NutritionService:
    def compute_daily_log(self, student_id: int, log_date: date) -> NutritionLog:
        """Compute and persist daily nutrition log for a student."""
        matches = MatchResult.query.options(joinedload(MatchResult.image), joinedload(MatchResult.consumption_record)).filter(
            MatchResult.student_id == student_id, MatchResult.match_date == log_date,
            MatchResult.status.in_([MatchStatusEnum.matched, MatchStatusEnum.confirmed]),
        ).all()
        image_ids = {match.image_id for match in matches if match.image_id}
        recs = DishRecognition.query.options(joinedload(DishRecognition.dish)).filter(DishRecognition.image_id.in_(image_ids)).all() if image_ids else []
        by_image = defaultdict(list)
        for rec in recs:
            by_image[rec.image_id].append(rec)
        snapshot = _nutrition_snapshot(matches, by_image, get_effective_config(current_app.config), log_date)
        log = NutritionLog.query.filter_by(student_id=student_id, log_date=log_date).first()
        if not log:
            log = NutritionLog(student_id=student_id, log_date=log_date)
            db.session.add(log)
        log.nutrient_totals = snapshot.nutrient_totals
        log.meal_count = snapshot.meal_count
        log.dish_ids = snapshot.dish_ids
        db.session.commit()
        return log

    def generate_personal_report(
        self, student_id: int, period_start: date, period_end: date
    ) -> dict:
        """Report observed nutrition alongside estimates normalized by meal shares."""
        student = db.session.get(Student, student_id)
        if not student:
            return {}

        logs = NutritionLog.query.filter(
            NutritionLog.student_id == student_id,
            NutritionLog.log_date >= period_start,
            NutritionLog.log_date <= period_end,
        ).order_by(NutritionLog.log_date).all()

        recorded_days = [log for log in logs if log.meal_count > 0]
        projection_logs = _projection_logs(recorded_days, period_start, period_end)
        avg_nutrients, nutrient_sample_counts = _calculate_average_nutrients(recorded_days)
        attendance = report_meal_snapshots([student_id], period_start, period_end)[student_id]
        nutrition_days = sum(any(nutrient_number((log.nutrient_totals or {}).get(key)) is not None for key in DAILY_RECOMMENDED) for log in recorded_days)
        nutrition_records = [{"date": log.log_date.isoformat(), "slot": slot}
                             for log in recorded_days for slot in (log.nutrient_totals or {}).get("_meal_slots", [])]

        return {
            **reference_snapshot(),
            "student_id": student_id,
            "student_name": student.name,
            "class_name": (student.class_.name if student.class_ else student.class_name),
            "organization_name": _student_organization(student),
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "analysis_basis": "period_daily_average",
            "avg_nutrients": avg_nutrients,
            "meal_projection": project_meals(projection_logs, period_start, period_end),
            "recommended_nutrients": {},
            "nutrient_sample_counts": nutrient_sample_counts,
            "alerts": [], "overall_score": None,
            "suggestions": list(GENERAL_SUGGESTIONS),
            "meal_statistics": attendance,
            "nutrition_records": nutrition_records,
            "daily_series": _daily_series(recorded_days, period_start, period_end),
            "energy_summary": _energy_summary(recorded_days),
            "coverage": {"period_days": (period_end - period_start).days + 1,
                         "recorded_days": attendance["recorded_days"], "nutrition_days": nutrition_days,
                         "historical_quality_days": sum((log.nutrient_totals or {}).get("_quality_version") not in (2, 3) for log in recorded_days)},
        }

    def generate_class_report(
        self, class_id: int, period_start: date, period_end: date
    ) -> dict:
        return self.generate_group_report("class", class_id, period_start, period_end)

    def generate_group_report(
        self, scope_type: str, scope_id: int, period_start: date, period_end: date
    ) -> dict:
        """Generate class, grade, or campus analysis without individual details."""
        from app.models import Campus, Class, Grade, Stage

        scope_models = {"class": Class, "grade": Grade, "campus": Campus}
        scope_model = scope_models.get(scope_type)
        scope = db.session.get(scope_model, scope_id) if scope_model else None
        if scope is None:
            return {}

        students_query = Student.query.filter(Student.is_active.is_(True))
        if scope_type == "class":
            students_query = students_query.filter(Student.class_id == scope_id)
        elif scope_type == "grade":
            students_query = students_query.join(Class, Student.class_id == Class.id).filter(Class.grade_id == scope_id)
        else:
            students_query = (
                students_query
                .join(Class, Student.class_id == Class.id)
                .join(Grade, Class.grade_id == Grade.id)
                .join(Stage, Grade.stage_id == Stage.id)
                .filter(Stage.campus_id == scope_id)
            )
        students = students_query.order_by(Student.id).all()
        student_ids = [student.id for student in students]

        logs_by_student = {student_id: [] for student_id in student_ids}
        if student_ids:
            logs = NutritionLog.query.filter(
                NutritionLog.student_id.in_(student_ids),
                NutritionLog.log_date >= period_start,
                NutritionLog.log_date <= period_end,
                NutritionLog.meal_count > 0,
            ).order_by(NutritionLog.student_id, NutritionLog.log_date).all()
            for log in logs:
                logs_by_student[log.student_id].append(log)

        projection_logs_by_student = defaultdict(list)
        for log in _projection_logs([log for logs in logs_by_student.values() for log in logs], period_start, period_end):
            projection_logs_by_student[log.student_id].append(log)
        student_averages = []
        for student in students:
            averages, counts = _calculate_average_nutrients(logs_by_student[student.id])
            student_averages.append({"averages": averages, "counts": counts, "class_id": student.class_id,
                                     "projection": project_meals(projection_logs_by_student[student.id], period_start, period_end)})
        student_count = len(students)
        students_with_data = sum(any(item["counts"].values()) for item in student_averages)
        avg_nutrients, sample_counts, distributions = {}, {}, {}
        for key in DAILY_RECOMMENDED:
            values = [item["averages"][key] for item in student_averages if item["counts"][key]]
            avg_nutrients[key] = round(sum(values) / len(values), 1) if values else None
            sample_counts[key] = len(values)
            # Numeric distributions describe observations, never health categories.
            distributions[key] = {"measured_count": len(values), "no_data": student_count - len(values),
                                  "coverage_rate": round(len(values) * 100 / student_count) if student_count else 0,
                                  "min": min(values) if values else None, "max": max(values) if values else None}
        all_logs = [log for logs in logs_by_student.values() for log in logs]
        attendance = report_meal_snapshots(student_ids, period_start, period_end)
        slot_counts = defaultdict(int)
        for stats in attendance.values():
            for record in stats["records"]:
                slot_counts[record["slot"]] += 1
        class_ids = {student.class_id for student in students if student.class_id}
        classes = Class.query.filter(Class.id.in_(class_ids)).order_by(Class.id).all() if class_ids else []
        cohorts = []
        for cls in classes:
            items = [item for item in student_averages if item["class_id"] == cls.id]
            means, counts = {}, {}
            for key in DAILY_RECOMMENDED:
                values = [item["averages"][key] for item in items if item["counts"][key]]
                means[key] = round(sum(values) / len(values), 1) if values else None
                counts[key] = len(values)
            ids = [student.id for student in students if student.class_id == cls.id]
            cohorts.append({"name": f"{cls.grade.name} / {cls.name}" if cls.grade else cls.name, "student_count": len(items), "nutrients": means, "sample_counts": counts,
                            "meal_projection": aggregate_projections([item["projection"] for item in items]),
                            "meal_count": sum(attendance[sid]["meal_count"] for sid in ids),
                            "meal_distribution": {slot: sum(sum(rec["slot"] == slot for rec in attendance[sid]["records"]) for sid in ids) for slot in slot_counts}})
        result = {
            **reference_snapshot(), "scope_type": scope_type, "scope_id": scope_id, "scope_name": scope.name,
            "period_start": period_start.isoformat(), "period_end": period_end.isoformat(),
            "analysis_basis": "student_period_average", "student_count": student_count, "students_with_data": students_with_data,
            "data_coverage_rate": round(students_with_data * 100 / student_count) if student_count else 0,
            "avg_nutrients": avg_nutrients, "recommended_nutrients": {}, "nutrient_sample_counts": sample_counts,
            "meal_projection": aggregate_projections([item["projection"] for item in student_averages]),
            "nutrient_distributions": distributions, "average_score": None, "score_distribution": None, "focus_nutrients": [],
            "suggestions": list(GENERAL_SUGGESTIONS), "daily_series": _daily_series(all_logs, period_start, period_end),
            "meal_distribution": dict(slot_counts), "meal_count": sum(s["meal_count"] for s in attendance.values()),
            "meal_slots": next(iter(attendance.values()))["slots"] if attendance else [], "cohorts": cohorts,
        }
        if scope_type == "class":
            result.update(class_id=scope_id, class_avg_score=None)
        return result

    def get_alerts_for_user(self, user) -> list:
        alerts = []
        if user.role.value == "parent":
            for student_id in (user.student_ids or []):
                student_alerts = self._check_student_alerts(student_id)
                alerts.extend(student_alerts)
        elif user.role.value == "teacher":
            for class_id in (user.managed_class_ids or []):
                students = Student.query.filter_by(class_id=class_id, is_active=True).all()
                for s in students[:20]:  # limit
                    alerts.extend(self._check_student_alerts(s.id))
        return alerts[:50]

    def _check_student_alerts(self, student_id: int) -> list:
        today = date.today()
        start = today - timedelta(days=ALERT_DAYS_THRESHOLD)
        logs = NutritionLog.query.filter(
            NutritionLog.student_id == student_id,
            NutritionLog.log_date >= start,
        ).all()
        alerts = []

        # Check skip meal
        meal_days = sum(1 for log in logs if log.meal_count > 0)
        if len(logs) >= ALERT_SKIP_MEAL_DAYS and meal_days == 0:
            student = db.session.get(Student, student_id)
            alerts.append({
                "type": "no_meal",
                "student_id": student_id,
                "student_name": student.name if student else "",
                "message": f"连续{ALERT_SKIP_MEAL_DAYS}天无有效营养识别记录，请检查采集情况",
            })
        return alerts
