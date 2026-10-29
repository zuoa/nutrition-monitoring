import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from celery_app import celery
from app import db
from app.models import Report, ReportTypeEnum, Student, TaskLog
from app.services.report_periods import generation_period

logger = logging.getLogger(__name__)

WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
SCHEDULED_WEEKLY_REPORT_TASK_TYPE = "scheduled_weekly_report"


def _weekly_report_schedule(cfg: dict) -> tuple[int, int, int]:
    weekday = str(cfg.get("WEEKLY_REPORT_DAY_OF_WEEK") or "sunday").strip().lower()
    weekday_index = WEEKDAY_INDEX.get(weekday, WEEKDAY_INDEX["sunday"])
    raw_time = str(cfg.get("WEEKLY_REPORT_TIME") or "08:00").strip()
    try:
        hour_text, minute_text = raw_time.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        logger.warning("Invalid WEEKLY_REPORT_TIME=%r, fallback to 08:00", raw_time)
        hour, minute = 8, 0
    return weekday_index, hour, minute


def _weekly_report_now(cfg: dict, now_iso: str | None = None) -> datetime:
    timezone_name = str(cfg.get("APP_TIMEZONE") or "Asia/Shanghai")
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo("Asia/Shanghai")
    if not now_iso:
        return datetime.now(tz)
    parsed = datetime.fromisoformat(now_iso)
    return parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed.astimezone(tz)


@celery.task(name="app.tasks.reports.dispatch_scheduled_weekly_reports")
def dispatch_scheduled_weekly_reports(now_iso: str | None = None):
    """Dispatch this week's personal reports when the runtime schedule is due."""
    from flask import current_app
    from app.services.runtime_config import get_effective_config

    cfg = get_effective_config(current_app.config)
    now = _weekly_report_now(cfg, now_iso)
    weekday, hour, minute = _weekly_report_schedule(cfg)
    if now.weekday() != weekday or (now.hour, now.minute) != (hour, minute):
        return {"scheduled": False, "reason": "not_due"}

    existing = TaskLog.query.filter_by(
        task_type=SCHEDULED_WEEKLY_REPORT_TASK_TYPE,
        task_date=now.date(),
    ).first()
    if existing is not None:
        return {"scheduled": False, "reason": "already_dispatched", "task_id": existing.id}

    period_start = now.date() - timedelta(days=now.weekday())
    period_end = period_start + timedelta(days=4)
    task_log = TaskLog(
        task_type=SCHEDULED_WEEKLY_REPORT_TASK_TYPE,
        task_date=now.date(),
        status="running",
        meta={
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "scheduled_at": now.isoformat(),
        },
    )
    db.session.add(task_log)
    db.session.commit()

    try:
        async_results = [
            generate_all_reports.delay(
                report_type,
                period_start.isoformat(),
                period_end.isoformat(),
                overwrite=True,
            )
            for report_type in ("personal_weekly", "class_weekly", "grade_weekly", "campus_weekly")
        ]
        task_log.status = "success"
        task_log.finished_at = datetime.now(timezone.utc)
        task_log.meta = {
            **dict(task_log.meta or {}),
            "celery_task_id": getattr(async_results[0], "id", None),
            "celery_task_ids": [getattr(result, "id", None) for result in async_results],
        }
        db.session.commit()
        return {
            "scheduled": True,
            "task_id": task_log.id,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
        }
    except Exception as exc:
        db.session.rollback()
        task_log = db.session.get(TaskLog, task_log.id)
        task_log.status = "failed"
        task_log.error_message = str(exc)[:1000]
        task_log.finished_at = datetime.now(timezone.utc)
        db.session.commit()
        raise


@celery.task(name="app.tasks.reports.generate_all_reports")
def generate_all_reports(report_type: str = "personal_weekly",
                         period_start_str: str = None, period_end_str: str = None, overwrite: bool = False, task_log_id: int = None):
    if task_log_id is not None:
        from app.services.report_generation import fail_generation_task
        task_log = db.session.get(TaskLog, task_log_id)
        if task_log is None or task_log.status != "pending":
            return
        try:
            return _dispatch_reports(report_type, period_start_str, period_end_str, overwrite, task_log)
        except Exception:
            logger.exception("Failed to dispatch report generation batch %s", task_log_id)
            fail_generation_task(task_log_id, "报告任务分发失败，请重新提交")
            raise
    return _dispatch_reports(report_type, period_start_str, period_end_str, overwrite)


def _dispatch_reports(report_type, period_start_str, period_end_str, overwrite, task_log=None):
    from flask import current_app

    ReportTypeEnum(report_type)
    period_start, period_end = generation_period(
        report_type, period_start_str, period_end_str, today=_weekly_report_now(current_app.config).date(),
    )
    jobs = []

    def enqueue(task, *args, **kwargs):
        jobs.append((task, args, kwargs))

    if report_type in ("personal_weekly", "personal_monthly"):
        students = Student.query.filter_by(is_active=True).all()
        for student in students:
            enqueue(_generate_personal_report, student.id, period_start.isoformat(), period_end.isoformat(), report_type, overwrite=overwrite)
    elif report_type in ("class_weekly", "class_monthly"):
        class_ids = (
            db.session.query(Student.class_id)
            .filter(Student.class_id.isnot(None))
            .distinct()
            .all()
        )
        for (class_id,) in class_ids:
            enqueue(_generate_class_report, class_id, period_start.isoformat(), period_end.isoformat(), report_type, overwrite=overwrite)
    elif report_type in ("grade_weekly", "grade_monthly"):
        from app.models import Class

        grade_ids = (
            db.session.query(Class.grade_id)
            .join(Student, Student.class_id == Class.id)
            .filter(Student.is_active.is_(True))
            .distinct()
            .all()
        )
        for (grade_id,) in grade_ids:
            enqueue(_generate_group_report, "grade", grade_id, period_start.isoformat(), period_end.isoformat(), report_type, overwrite=overwrite)
    elif report_type in ("campus_weekly", "campus_monthly"):
        from app.models import Class, Grade, Stage

        campus_ids = (
            db.session.query(Stage.campus_id)
            .join(Grade, Grade.stage_id == Stage.id)
            .join(Class, Class.grade_id == Grade.id)
            .join(Student, Student.class_id == Class.id)
            .filter(Student.is_active.is_(True))
            .distinct()
            .all()
        )
        for (campus_id,) in campus_ids:
            enqueue(_generate_group_report, "campus", campus_id, period_start.isoformat(), period_end.isoformat(), report_type, overwrite=overwrite)
    elif report_type == "school_monthly":
        students = Student.query.filter_by(is_active=True).all()
        for student in students:
            enqueue(
                _generate_personal_report, student.id, period_start.isoformat(),
                period_end.isoformat(), "personal_monthly", overwrite=overwrite,
            )
        class_ids = (
            db.session.query(Student.class_id)
            .filter(Student.class_id.isnot(None))
            .distinct()
            .all()
        )
        for (class_id,) in class_ids:
            enqueue(_generate_class_report, class_id, period_start.isoformat(), period_end.isoformat(), "class_monthly", overwrite=overwrite)

    if task_log is None:
        for task, args, kwargs in jobs:
            task.delay(*args, **kwargs)
    else:
        from app.services.report_generation import record_generation_result
        task_log.status = "running"
        task_log.total_count = len(jobs)
        db.session.commit()
        for index, (task, args, kwargs) in enumerate(jobs):
            try:
                _generate_batch_report.delay(task_log.id, str(index), task.name, args, kwargs)
            except Exception as exc:
                logger.exception("Failed to enqueue report %s in batch %s", index, task_log.id)
                db.session.rollback()
                record_generation_result(task_log.id, str(index), "failed", str(exc))
        record_generation_result(task_log.id, dispatch_complete=True)

    logger.info(f"Report generation triggered: {report_type} {period_start} - {period_end}")


@celery.task(name="app.tasks.reports.generate_batch_report")
def _generate_batch_report(task_log_id, key, task_name, args, kwargs):
    from app.services.report_generation import record_generation_result

    task_log = db.session.get(TaskLog, task_log_id)
    if task_log is None or key in (task_log.meta or {}).get("completed_keys", []):
        return
    try:
        task = {
            _generate_personal_report.name: _generate_personal_report,
            _generate_class_report.name: _generate_class_report,
            _generate_group_report.name: _generate_group_report,
        }[task_name]
        target_id = args[1] if task == _generate_group_report else args[0]
        start, end = generation_period(args[-1], args[-3], args[-2])
        existing = _existing_report(args[-1], target_id, start, end)
        if existing is not None and not kwargs.get("overwrite"):
            outcome = "skipped"
        else:
            outcome = "success" if task.run(*args, **kwargs) is not None else "skipped"
    except Exception as exc:
        logger.exception("Failed to generate report %s in batch %s", key, task_log_id)
        db.session.rollback()
        record_generation_result(task_log_id, key, "failed", str(exc))
    else:
        record_generation_result(task_log_id, key, outcome)


def _existing_report(report_type: str, target_id: int, period_start: date, period_end: date) -> Report | None:
    return Report.query.filter_by(
        report_type=ReportTypeEnum(report_type), target_id=str(target_id),
        period_start=period_start, period_end=period_end,
    ).order_by(Report.created_at.desc(), Report.id.desc()).first()


def _save_report(report: Report | None, report_type: str, target_id: int,
                 period_start: date, period_end: date, content: dict, summary: str) -> Report:
    if report is None:
        report = Report(
            report_type=ReportTypeEnum(report_type), target_id=str(target_id),
            period_start=period_start, period_end=period_end,
        )
        db.session.add(report)
    report.content = content
    report.summary = summary
    report.created_at = datetime.now(timezone.utc)
    report.push_status = "pending"
    report.pushed_at = None
    report.push_retry_count = 0
    db.session.commit()
    return report


@celery.task(name="app.tasks.reports.generate_personal_report")
def _generate_personal_report(
    student_id: int, period_start_str: str, period_end_str: str, report_type_str: str = "personal_weekly", overwrite: bool = False
):
    from app.services.nutrition_service import NutritionService
    svc = NutritionService()
    period_start, period_end = generation_period(report_type_str, period_start_str, period_end_str)
    report = _existing_report(report_type_str, student_id, period_start, period_end)
    if report is not None and not overwrite:
        return report.id

    content = svc.generate_personal_report(student_id, period_start, period_end)

    report = _save_report(
        report, report_type_str, student_id, period_start, period_end,
        content, _summarize_personal(content),
    )

    # Push
    push_report_task.delay(report.id)
    return report.id


@celery.task(name="app.tasks.reports.generate_class_report")
def _generate_class_report(
    class_id: int, period_start_str: str, period_end_str: str, report_type_str: str = "class_weekly", overwrite: bool = False
):
    return _persist_group_report("class", class_id, period_start_str, period_end_str, report_type_str, overwrite)


@celery.task(name="app.tasks.reports.generate_group_report")
def _generate_group_report(
    scope_type: str, scope_id: int, period_start_str: str, period_end_str: str, report_type_str: str, overwrite: bool = False
):
    return _persist_group_report(scope_type, scope_id, period_start_str, period_end_str, report_type_str, overwrite)


def _persist_group_report(
    scope_type: str, scope_id: int, period_start_str: str, period_end_str: str, report_type_str: str, overwrite: bool = False
):
    from app.services.nutrition_service import NutritionService
    from app.models import Campus, Class, Grade

    svc = NutritionService()
    period_start, period_end = generation_period(report_type_str, period_start_str, period_end_str)
    report = _existing_report(report_type_str, scope_id, period_start, period_end)
    if report is not None and not overwrite:
        return report.id

    content = svc.generate_group_report(scope_type, scope_id, period_start, period_end)
    if not content:
        logger.warning("Skip empty %s report for scope_id=%s", scope_type, scope_id)
        return None
    scope_model = {"class": Class, "grade": Grade, "campus": Campus}[scope_type]
    scope = scope_model.query.get(scope_id)
    scope_label = scope.name if scope else str(scope_id)
    scope_label_zh = {"class": "班级", "grade": "年级", "campus": "校区"}[scope_type]

    report = _save_report(
        report, report_type_str, scope_id, period_start, period_end, content,
        (
            f"{scope_label_zh} {scope_label} 在校膳食记录，"
            f"数据覆盖率 {content.get('data_coverage_rate', 0)}%"
        ),
    )
    return report.id


@celery.task(name="app.tasks.reports.push_report_task", bind=True, max_retries=3)
def push_report_task(self, report_id: int):
    from flask import current_app
    from app.services.dingtalk import DingTalkService
    from app.models import User, RoleEnum, Student, ReportPushLog
    from datetime import datetime, timezone

    cfg = current_app.config
    report = Report.query.get(report_id)
    if not report:
        return

    dt = DingTalkService(cfg)
    content = report.content or {}
    student_name = content.get("student_name", "")
    period_start = report.period_start.isoformat() if report.period_start else ""
    period_end = report.period_end.isoformat() if report.period_end else ""

    period_label = "月报" if report.report_type.value.endswith("_monthly") else "周报"
    title = f"[{period_label}] {student_name} 营养摄入分析" if student_name else f"[报告] {report.target_id}"
    subtitle = "查看在校膳食记录、营养估算与饮食建议"
    summary = f"{period_start} 至 {period_end} | 在校膳食营养记录"
    jump_url = f"{cfg.get('FRONTEND_URL', 'http://localhost')}/reports/{report_id}"

    recipients = []
    if report.report_type in (ReportTypeEnum.personal_weekly, ReportTypeEnum.personal_monthly):
        student_id = int(report.target_id)
        # Find parents
        parents = User.query.filter(
            User.role == RoleEnum.parent,
            User.is_active.is_(True),
        ).all()
        recipients.extend(
            u for u in parents if student_id in (u.student_ids or [])
        )
        # Find teacher
        student = Student.query.get(student_id)
        if student:
            teachers = User.query.filter(
                User.role == RoleEnum.teacher,
                User.is_active.is_(True),
            ).all()
            recipients.extend(
                t for t in teachers if student.class_id in (t.managed_class_ids or [])
            )

    errors = 0
    for user in recipients:
        try:
            ok = dt.send_card_message(
                user.dingtalk_user_id, title, subtitle, summary, jump_url
            )
            log = ReportPushLog(
                report_id=report_id,
                user_id=user.id,
                status="sent" if ok else "failed",
            )
            db.session.add(log)
            if not ok:
                errors += 1
        except Exception as e:
            errors += 1
            log = ReportPushLog(
                report_id=report_id,
                user_id=user.id,
                status="failed",
                error_message=str(e),
            )
            db.session.add(log)

    report.push_status = "sent" if errors == 0 else ("failed" if errors == len(recipients) else "partial")
    report.pushed_at = datetime.now(timezone.utc)
    report.push_retry_count = (report.push_retry_count or 0) + 1
    db.session.commit()

    if errors > 0 and self.request.retries < 3:
        raise self.retry(countdown=300)


def _summarize_personal(content: dict) -> str:
    name = content.get("student_name", "")
    coverage = content.get("coverage") or {}
    return f"{name}在校膳食营养记录，有效营养数据 {coverage.get('nutrition_days', 0)} 天；仅反映已记录膳食。"


@celery.task(name="app.tasks.reports.export_class_reports", soft_time_limit=1500, time_limit=1800)
def export_class_reports(task_id: int):
    from app.services.report_exports import build_class_export
    build_class_export(task_id)


@celery.task(name="app.tasks.reports.cleanup_exports")
def cleanup_exports():
    from app.services.report_exports import cleanup_report_exports
    return {"cleaned": cleanup_report_exports()}
