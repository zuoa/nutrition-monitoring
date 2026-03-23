import logging
from datetime import date, timedelta
from celery_app import celery
from app import db
from app.models import Report, ReportTypeEnum, Student

logger = logging.getLogger(__name__)


@celery.task(name="app.tasks.reports.generate_all_reports")
def generate_all_reports(report_type: str = "personal_weekly", period_start_str: str = None, period_end_str: str = None):
    from app.services.nutrition_service import NutritionService
    svc = NutritionService()
    today = date.today()

    if report_type in ("personal_weekly", "class_weekly"):
        # Last Mon-Sun
        last_monday = today - timedelta(days=today.weekday() + 7)
        period_start = date.fromisoformat(period_start_str) if period_start_str else last_monday
        period_end = period_start + timedelta(days=6)
    else:
        # Last month
        first_this_month = today.replace(day=1)
        last_month_end = first_this_month - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        period_start = date.fromisoformat(period_start_str) if period_start_str else last_month_start
        period_end = date.fromisoformat(period_end_str) if period_end_str else last_month_end

    if report_type == "personal_weekly":
        students = Student.query.filter_by(is_active=True).all()
        for student in students:
            _generate_personal_report.delay(student.id, period_start.isoformat(), period_end.isoformat())
    elif report_type == "class_weekly":
        class_ids = db.session.query(Student.class_id).distinct().all()
        for (class_id,) in class_ids:
            _generate_class_report.delay(class_id, period_start.isoformat(), period_end.isoformat())
    elif report_type == "school_monthly":
        students = Student.query.filter_by(is_active=True).all()
        for student in students:
            _generate_personal_report.delay(student.id, period_start.isoformat(), period_end.isoformat(), "personal_monthly")
        class_ids = db.session.query(Student.class_id).distinct().all()
        for (class_id,) in class_ids:
            _generate_class_report.delay(class_id, period_start.isoformat(), period_end.isoformat(), "class_weekly")

    logger.info(f"Report generation triggered: {report_type} {period_start} - {period_end}")


@celery.task(name="app.tasks.reports.generate_personal_report")
def _generate_personal_report(
    student_id: int, period_start_str: str, period_end_str: str, report_type_str: str = "personal_weekly"
):
    from app.services.nutrition_service import NutritionService
    svc = NutritionService()
    period_start = date.fromisoformat(period_start_str)
    period_end = date.fromisoformat(period_end_str)

    content = svc.generate_personal_report(student_id, period_start, period_end)

    report = Report(
        report_type=ReportTypeEnum(report_type_str),
        target_id=str(student_id),
        period_start=period_start,
        period_end=period_end,
        content=content,
        summary=_summarize_personal(content),
        push_status="pending",
    )
    db.session.add(report)
    db.session.commit()

    # Push
    push_report_task.delay(report.id)


@celery.task(name="app.tasks.reports.generate_class_report")
def _generate_class_report(
    class_id: str, period_start_str: str, period_end_str: str, report_type_str: str = "class_weekly"
):
    from app.services.nutrition_service import NutritionService
    svc = NutritionService()
    period_start = date.fromisoformat(period_start_str)
    period_end = date.fromisoformat(period_end_str)

    content = svc.generate_class_report(class_id, period_start, period_end)

    report = Report(
        report_type=ReportTypeEnum(report_type_str),
        target_id=class_id,
        period_start=period_start,
        period_end=period_end,
        content=content,
        summary=f"班级 {class_id} 营养报告 {period_start} - {period_end}",
        push_status="pending",
    )
    db.session.add(report)
    db.session.commit()
    push_report_task.delay(report.id)


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
    score = content.get("overall_score", 0)
    alerts = content.get("alerts", [])

    title = f"[周报] {student_name} 本周营养摄入分析" if student_name else f"[报告] {report.target_id}"
    subtitle = alerts[0]["message"] if alerts else "营养摄入均衡，请继续保持"
    summary = f"{period_start} 至 {period_end} | 综合评分：{score}分"
    jump_url = f"{cfg.get('FRONTEND_URL', 'http://localhost')}/reports/{report_id}"

    recipients = []
    if report.report_type in (ReportTypeEnum.personal_weekly, ReportTypeEnum.personal_monthly):
        student_id = int(report.target_id)
        # Find parents
        parents = User.query.filter(
            User.role == RoleEnum.parent,
            User.is_active == True,
        ).all()
        recipients.extend(
            u for u in parents if student_id in (u.student_ids or [])
        )
        # Find teacher
        student = Student.query.get(student_id)
        if student:
            teachers = User.query.filter(
                User.role == RoleEnum.teacher,
                User.is_active == True,
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
    meals = content.get("meal_days", 0)
    total = content.get("total_days", 7)
    score = content.get("overall_score", 0)
    alerts = content.get("alerts", [])
    alert_text = f"，{alerts[0]['message']}" if alerts else ""
    return f"{name}本期就餐{meals}/{total}天，综合评分{score}分{alert_text}"
