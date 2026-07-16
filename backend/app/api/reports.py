import logging
from flask import Blueprint, request
from app.models import Campus, Class, Grade, Report, Student
from app.utils.jwt_utils import login_required, role_required, api_ok, api_error
from app.utils.pagination import paginate, paginated_response

bp = Blueprint("reports", __name__)
logger = logging.getLogger(__name__)

GROUP_REPORT_TYPES = {
    "class": "class_weekly",
    "grade": "grade_weekly",
    "campus": "campus_weekly",
}
GENERATABLE_REPORT_TYPES = {
    "personal_weekly",
    "personal_monthly",
    "class_weekly",
    "grade_weekly",
    "campus_weekly",
    "grade_monthly",
    "school_monthly",
}


def _student_access_allowed(user, student: Student) -> bool:
    role = user.role.value
    if role == "admin":
        return True
    if role == "parent":
        return student.id in (user.student_ids or [])
    if role == "teacher":
        return student.class_id in (user.managed_class_ids or [])
    if role == "grade_leader":
        grade_id = student.class_.grade_id if student.class_ and student.class_.grade else None
        return grade_id in (user.managed_grade_ids or [])
    return False


def _group_access_allowed(user, scope_type: str, scope_id: int) -> bool:
    role = user.role.value
    if role == "admin":
        return True
    if scope_type == "class":
        if role == "teacher":
            return scope_id in (user.managed_class_ids or [])
        if role == "grade_leader":
            class_ = Class.query.get(scope_id)
            return bool(class_ and class_.grade_id in (user.managed_grade_ids or []))
    if scope_type == "grade" and role == "grade_leader":
        return scope_id in (user.managed_grade_ids or [])
    return False


@bp.route("/student/<int:student_id>", methods=["GET"])
@login_required
def get_student_report(student_id):
    user = request.current_user
    student = Student.query.get_or_404(student_id)

    if not _student_access_allowed(user, student):
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
    student = Student.query.get_or_404(student_id)
    if not _student_access_allowed(user, student):
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
    # target_id 以「整型班级主键的字符串」存储，统一转 int 做作用域比较
    try:
        class_id_int = int(class_id)
    except (TypeError, ValueError):
        class_id_int = None
    if class_id_int is None or not _group_access_allowed(user, "class", class_id_int):
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


@bp.route("/<string:scope_type>/<int:scope_id>/latest", methods=["GET"])
@login_required
def get_group_latest_report(scope_type, scope_id):
    """Return the latest aggregate report without any individual-level details."""
    report_type = GROUP_REPORT_TYPES.get(scope_type)
    if report_type is None:
        return api_error("不支持的报告范围", 404)

    scope_model = {"class": Class, "grade": Grade, "campus": Campus}[scope_type]
    scope_model.query.get_or_404(scope_id)
    if not _group_access_allowed(request.current_user, scope_type, scope_id):
        return api_error("无权访问该范围报告", 403)

    report = Report.query.filter_by(
        report_type=report_type,
        target_id=str(scope_id),
    ).order_by(Report.period_start.desc(), Report.created_at.desc()).first()
    if report and (report.content or {}).get("analysis_basis") != "student_period_average":
        report = None
    return api_ok(report.to_dict(include_content=True) if report else None)


@bp.route("/<int:report_id>", methods=["GET"])
@login_required
def get_report(report_id):
    report = Report.query.get_or_404(report_id)
    user = request.current_user
    try:
        target_id = int(report.target_id)
    except (TypeError, ValueError):
        target_id = None

    report_type = report.report_type.value if report.report_type else ""
    allowed = False
    if report_type.startswith("personal_") and target_id is not None:
        student = Student.query.get(target_id)
        allowed = bool(student and _student_access_allowed(user, student))
    elif report_type == "class_weekly" and target_id is not None:
        allowed = _group_access_allowed(user, "class", target_id)
    elif report_type in {"grade_weekly", "grade_monthly"} and target_id is not None:
        allowed = _group_access_allowed(user, "grade", target_id)
    elif report_type == "campus_weekly" and target_id is not None:
        allowed = _group_access_allowed(user, "campus", target_id)
    elif user.role.value == "admin":
        allowed = True
    if not allowed:
        return api_error("无权访问该报告", 403)
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
    if report_type not in GENERATABLE_REPORT_TYPES:
        return api_error("不支持的报告类型")
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
