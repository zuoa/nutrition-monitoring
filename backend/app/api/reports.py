import logging
from flask import Blueprint, request
from app.models import Report, Student
from app.utils.jwt_utils import login_required, role_required, api_ok, api_error
from app.utils.pagination import paginate, paginated_response

bp = Blueprint("reports", __name__)
logger = logging.getLogger(__name__)


@bp.route("/student/<int:student_id>", methods=["GET"])
@login_required
def get_student_report(student_id):
    user = request.current_user
    student = Student.query.get_or_404(student_id)

    # Permission check
    if user.role.value == "parent":
        if student_id not in (user.student_ids or []):
            return api_error("无权访问该学生数据", 403)
    elif user.role.value == "teacher":
        if student.class_id not in (user.managed_class_ids or []):
            return api_error("无权访问该学生数据", 403)
    elif user.role.value == "grade_leader":
        if student.grade_id not in (user.managed_grade_ids or []):
            return api_error("无权访问该学生数据", 403)

    report_type = request.args.get("type", "personal_weekly")
    period = request.args.get("period")  # YYYY-Www or YYYY-MM

    q = Report.query.filter_by(
        report_type=report_type,
        target_id=str(student_id),
    ).order_by(Report.period_start.desc())

    if period:
        # Could filter by period in future
        pass

    items, total, page, page_size = paginate(q)
    include_content = request.args.get("include_content") == "true"
    return api_ok(paginated_response(
        [r.to_dict(include_content=include_content) for r in items], total, page, page_size
    ))


@bp.route("/student/<int:student_id>/latest", methods=["GET"])
@login_required
def get_student_latest_report(student_id):
    user = request.current_user
    Student.query.get_or_404(student_id)

    if user.role.value == "parent":
        if student_id not in (user.student_ids or []):
            return api_error("无权访问该学生数据", 403)

    report = Report.query.filter_by(
        report_type="personal_weekly",
        target_id=str(student_id),
    ).order_by(Report.period_start.desc()).first()

    if not report:
        return api_ok(None)
    return api_ok(report.to_dict(include_content=True))


@bp.route("/class/<string:class_id>", methods=["GET"])
@login_required
def get_class_report(class_id):
    user = request.current_user
    if user.role.value == "teacher":
        if class_id not in (user.managed_class_ids or []):
            return api_error("无权访问该班级数据", 403)

    q = Report.query.filter_by(
        report_type="class_weekly",
        target_id=class_id,
    ).order_by(Report.period_start.desc())

    items, total, page, page_size = paginate(q)
    include_content = request.args.get("include_content") == "true"
    return api_ok(paginated_response(
        [r.to_dict(include_content=include_content) for r in items], total, page, page_size
    ))


@bp.route("/<int:report_id>", methods=["GET"])
@login_required
def get_report(report_id):
    report = Report.query.get_or_404(report_id)
    # TODO: add permission checks based on report type and target
    return api_ok(report.to_dict(include_content=True))


@bp.route("/<int:report_id>/push", methods=["POST"])
@role_required("admin")
def push_report(report_id):
    Report.query.get_or_404(report_id)
    from app.tasks.reports import push_report_task
    push_report_task.delay(report_id)
    return api_ok({"message": "推送任务已提交"})


@bp.route("/generate", methods=["POST"])
@role_required("admin")
def generate_reports():
    """Manually trigger report generation."""
    data = request.get_json() or {}
    report_type = data.get("type", "personal_weekly")
    period_start = data.get("period_start")
    period_end = data.get("period_end")

    from app.tasks.reports import generate_all_reports
    generate_all_reports.delay(report_type, period_start, period_end)
    return api_ok({"message": "报告生成任务已提交"})


@bp.route("/alerts", methods=["GET"])
@login_required
def get_alerts():
    """Get nutrition alerts for current user's scope."""
    user = request.current_user
    from app.services.nutrition_service import NutritionService
    svc = NutritionService()
    alerts = svc.get_alerts_for_user(user)
    return api_ok(alerts)
