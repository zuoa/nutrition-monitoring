import logging
import io
from datetime import date
from flask import Blueprint, current_app, request, send_file
from app import db
from app.models import Campus, Class, Grade, Report, Student, TaskLog
from app.services.report_presentation import REPORT_FONTS, REPORT_FORMATS, TEMPLATE_DIR, serialize_group_report, serialize_personal_reports
from app.services.report_periods import generation_period, parse_report_period
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
    "class_monthly",
    "grade_weekly",
    "campus_weekly",
    "campus_monthly",
    "grade_monthly",
    "school_monthly",
}


@bp.route("/fonts/<string:name>", methods=["GET"])
def report_font(name):
    """Public font assets contain no report data and can be shared by previews."""
    if name not in REPORT_FONTS:
        return api_error("字体不存在", 404)
    return send_file(TEMPLATE_DIR / "fonts" / name, mimetype="font/woff2", max_age=31536000, conditional=True)


def _report_query(scope: str, target_id: int | str):
    report_type = request.args.get("type", f"{scope}_weekly")
    if report_type not in {f"{scope}_weekly", f"{scope}_monthly"}:
        raise ValueError("不支持的报告类型")
    query = Report.query.filter_by(report_type=report_type, target_id=str(target_id))
    if period := request.args.get("period"):
        start, end = parse_report_period(report_type, period)
        query = query.filter_by(period_start=start, period_end=end)
    return query.order_by(Report.period_start.desc(), Report.created_at.desc(), Report.id.desc())


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

    try:
        q = _report_query("personal", student_id)
    except ValueError as exc:
        return api_error(str(exc))

    items, total, page, page_size = paginate(q)
    include_content = request.args.get("include_content") == "true"
    return api_ok(paginated_response(
        serialize_personal_reports(items) if include_content else [r.to_dict() for r in items], total, page, page_size
    ))


@bp.route("/student/<int:student_id>/latest", methods=["GET"])
@login_required
def get_student_latest_report(student_id):
    user = request.current_user
    student = Student.query.get_or_404(student_id)
    if not _student_access_allowed(user, student):
        return api_error("无权访问该学生数据", 403)

    try:
        report = _report_query("personal", student_id).first()
    except ValueError as exc:
        return api_error(str(exc))

    if not report:
        return api_ok(None)
    return api_ok(serialize_personal_reports([report], include_html=True)[0])


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

    try:
        q = _report_query("class", class_id)
    except ValueError as exc:
        return api_error(str(exc))

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

    try:
        report = _report_query(scope_type, scope_id).first()
    except ValueError as exc:
        return api_error(str(exc))
    if report and (report.content or {}).get("analysis_basis") != "student_period_average":
        report = None
    return api_ok(serialize_group_report(report, include_html=True) if report else None)


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
    elif report_type in {"class_weekly", "class_monthly"} and target_id is not None:
        allowed = _group_access_allowed(user, "class", target_id)
    elif report_type in {"grade_weekly", "grade_monthly"} and target_id is not None:
        allowed = _group_access_allowed(user, "grade", target_id)
    elif report_type in {"campus_weekly", "campus_monthly"} and target_id is not None:
        allowed = _group_access_allowed(user, "campus", target_id)
    elif user.role.value == "admin":
        allowed = True
    if not allowed:
        return api_error("无权访问该报告", 403)
    return api_ok(serialize_personal_reports([report], include_html=True)[0])


@bp.route("/<int:report_id>/export.png", methods=["GET"])
@login_required
def export_personal_png(report_id):
    from app.services.report_exports import render_group_png, render_personal_png, safe_filename

    report = Report.query.get_or_404(report_id)
    kind = report.report_type.value
    scope = kind.split("_")[0]
    if not str(report.target_id).isdigit() or scope not in ("personal", "class", "grade", "campus"):
        return api_error("不支持的报告类型", 400)
    student = db.session.get(Student, int(report.target_id)) if scope == "personal" else None
    allowed = bool(student and _student_access_allowed(request.current_user, student)) if scope == "personal" else \
        _group_access_allowed(request.current_user, scope, int(report.target_id))
    if not allowed:
        return api_error("无权访问该报告", 403)
    report_format = request.args.get("format", "detailed")
    if report_format not in REPORT_FORMATS:
        return api_error("不支持的报告版本", 400)
    try:
        content = serialize_personal_reports([report])[0]["content"]
        if scope == "personal":
            png = render_personal_png(content, report_format="brief") if report_format == "brief" else render_personal_png(content)
        else:
            png = render_group_png(content)
    except Exception:
        logger.exception("Failed to render report %s", report.id)
        return api_error("报告图片生成失败，请稍后重试", 500)
    label = f"{student.student_no}_{student.name}" if student else content.get("scope_name", "报告")
    filename = safe_filename(f"{label}_{report.period_start}_{report.period_end}") + ".png"
    if report_format == "brief" and scope == "personal":
        filename = filename[:-4] + "_简报.png"
    response = send_file(io.BytesIO(png), mimetype="image/png", as_attachment=True, download_name=filename, max_age=0)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.route("/class/<int:class_id>/export-periods", methods=["GET"])
@login_required
def class_export_periods(class_id):
    Class.query.get_or_404(class_id)
    if not _group_access_allowed(request.current_user, "class", class_id):
        return api_error("无权导出该班级报告", 403)
    report_type = request.args.get("type", "personal_weekly")
    if report_type not in {"personal_weekly", "personal_monthly"}:
        return api_error("不支持的报告类型")
    ids = db.session.query(Student.id).filter_by(class_id=class_id, is_active=True, enrollment_status="enrolled").all()
    periods = db.session.query(Report.period_start, Report.period_end).filter(
        Report.report_type == report_type, Report.target_id.in_([str(sid) for (sid,) in ids]),
    ).distinct().order_by(Report.period_start.desc(), Report.period_end.desc()).all() if ids else []
    return api_ok([{"period_start": start.isoformat(), "period_end": end.isoformat()} for start, end in periods])


@bp.route("/class/<int:class_id>/exports", methods=["POST"])
@login_required
def create_class_export(class_id):
    from app.services.report_exports import EXPORT_TASK_TYPE, class_report_selection, export_task_payload
    from app.tasks.reports import export_class_reports

    class_ = Class.query.get_or_404(class_id)
    if not _group_access_allowed(request.current_user, "class", class_id):
        return api_error("无权导出该班级报告", 403)
    data = request.get_json(silent=True) or {}
    report_type = data.get("type", "personal_weekly")
    if report_type not in {"personal_weekly", "personal_monthly"}:
        return api_error("不支持的报告类型")
    report_format = data.get("format", "detailed")
    if report_format not in REPORT_FORMATS:
        return api_error("不支持的报告版本", 400)
    try:
        start, end = date.fromisoformat(data["period_start"]), date.fromisoformat(data["period_end"])
        if report_type == "personal_monthly":
            generation_period(report_type, data["period_start"], data["period_end"])
        elif end < start or (end - start).days > 6:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return api_error("请选择有效的报告周期", 400)
    students = class_report_selection(class_id, start, end, report_type)
    if not any(s["report_id"] for s in students):
        return api_error("该班级在所选周期没有可导出的学生报告", 400)
    task = TaskLog(task_type=EXPORT_TASK_TYPE, status="pending", total_count=len(students), meta={
        "requested_by": request.current_user.id, "class_id": class_id, "class_name": class_.name,
        "period_start": start.isoformat(), "period_end": end.isoformat(), "students": students,
        "report_type": report_type,
        "report_format": report_format,
    })
    db.session.add(task)
    db.session.commit()
    try:
        export_class_reports.delay(task.id)
    except Exception:
        logger.exception("Failed to enqueue class report export")
        task.status, task.error_message = "failed", "导出服务暂不可用，请稍后重试"
        db.session.commit()
        return api_error(task.error_message, 503)
    return api_ok(export_task_payload(task)), 202


def _accessible_export(task_id):
    from app.services.report_exports import EXPORT_TASK_TYPE

    task = db.session.get(TaskLog, task_id)
    if task is None or task.task_type != EXPORT_TASK_TYPE:
        return None, api_error("导出任务不存在", 404)
    meta = task.meta or {}
    if meta.get("requested_by") != request.current_user.id or not _group_access_allowed(request.current_user, "class", meta.get("class_id")):
        return None, api_error("无权访问该导出任务", 403)
    return task, None


@bp.route("/exports/<int:task_id>", methods=["GET"])
@login_required
def get_class_export(task_id):
    from app.services.report_exports import export_task_payload

    task, error = _accessible_export(task_id)
    return error if error else api_ok(export_task_payload(task))


@bp.route("/exports/<int:task_id>/download", methods=["GET"])
@login_required
def download_class_export(task_id):
    from app.services.report_exports import artifact_path, export_task_payload, safe_filename

    task, error = _accessible_export(task_id)
    if error:
        return error
    payload = export_task_payload(task)
    if payload["expired"]:
        return api_error("下载已过期，请重新导出", 410)
    if not payload["download_ready"]:
        return api_error("压缩包尚未生成或生成失败，请查看任务状态", 409)
    period_label = "月报" if payload["report_type"] == "personal_monthly" else "周报"
    format_label = "_简报" if payload["report_format"] == "brief" else ""
    filename = safe_filename(f"{payload['class_name']}_营养{period_label}_{payload['period_start']}_{payload['period_end']}{format_label}") + ".zip"
    response = send_file(artifact_path(task.id), mimetype="application/zip", as_attachment=True, download_name=filename, max_age=0)
    response.headers["Cache-Control"] = "private, no-store"
    return response


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
    overwrite = data.get("overwrite", False)
    if not isinstance(overwrite, bool):
        return api_error("覆盖选项必须为布尔值")
    from app.tasks.reports import _weekly_report_now, generate_all_reports

    try:
        period_start, period_end = generation_period(
            report_type, data.get("period_start"), data.get("period_end"), data.get("period"),
            today=_weekly_report_now(current_app.config).date(),
        )
    except ValueError as exc:
        return api_error(str(exc))

    from app.services.report_generation import GENERATION_TASK_TYPE, fail_generation_task, generation_task_payload

    task = TaskLog(task_type=GENERATION_TASK_TYPE, task_date=period_start, status="pending", meta={
        "report_type": report_type, "period_start": period_start.isoformat(), "period_end": period_end.isoformat(),
        "overwrite": overwrite, "requested_by": request.current_user.id,
    })
    db.session.add(task)
    db.session.commit()
    try:
        generate_all_reports.delay(report_type, period_start.isoformat(), period_end.isoformat(), overwrite=overwrite, task_log_id=task.id)
    except Exception:
        logger.exception("Failed to enqueue report generation batch %s", task.id)
        fail_generation_task(task.id, "报告生成任务提交失败，请重试")
        return api_error("报告生成任务提交失败，请重试", 503)
    return api_ok({"message": "报告生成任务已提交", **generation_task_payload(task)})


@bp.route("/generation-tasks", methods=["GET"])
@role_required("admin")
def generation_tasks():
    from app.services.report_generation import GENERATION_TASK_TYPE, generation_task_payload

    query = TaskLog.query.filter(TaskLog.task_type == GENERATION_TASK_TYPE, TaskLog.meta["report_type"].as_string().in_(GENERATABLE_REPORT_TYPES))
    active = query.filter(TaskLog.status.in_(("pending", "running"))).order_by(TaskLog.id.desc()).all()
    recent = query.filter(TaskLog.status.in_(("success", "partial", "failed"))).order_by(TaskLog.id.desc()).limit(3).all()
    return api_ok([generation_task_payload(task) for task in active + recent])


@bp.route("/alerts", methods=["GET"])
@login_required
def get_alerts():
    """Get nutrition alerts for current user's scope."""
    user = request.current_user
    from app.services.nutrition_service import NutritionService
    svc = NutritionService()
    alerts = svc.get_alerts_for_user(user)
    return api_ok(alerts)
