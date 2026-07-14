"""学生 API：查询、本地维护、导入与监护人。"""
from io import BytesIO

from flask import Blueprint, request, send_file

from app.utils.jwt_utils import role_required, login_required, api_ok, api_error
from app.utils.pagination import paginated_response
from app.modules.students.services import student_service

bp = Blueprint("students", __name__)


def _uploaded_student_file():
    if "file" not in request.files:
        raise student_service.StudentManagementError("请上传文件")
    file = request.files["file"]
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ("csv", "xls", "xlsx"):
        raise student_service.StudentManagementError("仅支持 CSV、XLS、XLSX 格式")
    return file.read(), ext


@bp.route("", methods=["GET"])
@bp.route("/", methods=["GET"])
@role_required("admin", "teacher", "grade_leader", "parent")
def list_students():
    user = request.current_user
    args = request.args.to_dict()
    include_latest_report = str(args.pop("include_latest_report", "")).lower() in {"1", "true", "yes"}
    try:
        items, total, page, page_size = student_service.list_students(user, args, include_latest_report)
    except student_service.StudentManagementError as exc:
        return api_error(str(exc), exc.status_code)
    return api_ok(paginated_response(items, total, page, page_size))


@bp.route("", methods=["POST"])
@bp.route("/", methods=["POST"])
@role_required("admin")
def create_student():
    try:
        student = student_service.create_student(request.get_json() or {})
        return api_ok(student.to_dict())
    except student_service.StudentManagementError as exc:
        return api_error(str(exc), exc.status_code)


@bp.route("/import-template", methods=["GET"])
@role_required("admin")
def download_student_import_template():
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "学生名单"
    sheet.append(["学号", "姓名", "学校", "校区", "学段", "年级", "班级", "消费卡号", "性别"])
    sheet.append(["2026001", "示例学生", "示范学校", "本部", "初中部", "七年级", "七年级（1）班", "", "女"])
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name="学生导入模板.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@bp.route("/import/preview", methods=["POST"])
@role_required("admin")
def preview_student_import():
    from app.modules.students.services.student_import_service import StudentImportService
    try:
        content, ext = _uploaded_student_file()
        return api_ok(StudentImportService().preview_file(content, ext))
    except student_service.StudentManagementError as exc:
        return api_error(str(exc), exc.status_code)
    except Exception as exc:
        return api_error(f"读取导入文件失败：{exc}")


@bp.route("/import/apply", methods=["POST"])
@role_required("admin")
def apply_student_import():
    from app.modules.students.services.student_import_service import (
        StudentImportError,
        StudentImportService,
    )
    try:
        content, ext = _uploaded_student_file()
        return api_ok(StudentImportService().import_file(content, ext))
    except (student_service.StudentManagementError, StudentImportError) as exc:
        status_code = getattr(exc, "status_code", 400)
        return api_error(str(exc), status_code)
    except Exception as exc:
        return api_error(f"导入失败：{exc}")


@bp.route("/<int:student_id>", methods=["GET"])
@login_required
def get_student(student_id):
    student = student_service.get_student(student_id)
    if not student:
        return api_error("学生不存在", 404)
    denied = _check_view_permission(request.current_user, student)
    if denied:
        return denied
    return api_ok(student.to_dict())


@bp.route("/<int:student_id>", methods=["PUT"])
@role_required("admin")
def update_student(student_id):
    data = request.get_json() or {}
    try:
        student = student_service.update_student(student_id, data)
    except student_service.StudentManagementError as exc:
        return api_error(str(exc), exc.status_code)
    if not student:
        return api_error("学生不存在", 404)
    return api_ok(student.to_dict())


@bp.route("/<int:student_id>/guardians", methods=["GET"])
@role_required("admin", "teacher", "grade_leader", "parent")
def student_guardians(student_id):
    student = student_service.get_student(student_id)
    if not student:
        return api_error("学生不存在", 404)
    denied = _check_view_permission(request.current_user, student)
    if denied:
        return denied
    return api_ok([g.to_dict() for g in student_service.list_guardians(student_id)])


@bp.route("/<int:student_id>/guardians", methods=["POST"])
@role_required("admin")
def create_student_guardian(student_id):
    try:
        guardian = student_service.create_guardian(student_id, request.get_json() or {})
        return api_ok(guardian.to_dict())
    except student_service.StudentManagementError as exc:
        return api_error(str(exc), exc.status_code)


@bp.route("/<int:student_id>/guardians/<int:guardian_id>", methods=["PUT"])
@role_required("admin")
def update_student_guardian(student_id, guardian_id):
    try:
        guardian = student_service.update_guardian(
            student_id,
            guardian_id,
            request.get_json() or {},
        )
        return api_ok(guardian.to_dict())
    except student_service.StudentManagementError as exc:
        return api_error(str(exc), exc.status_code)


@bp.route("/<int:student_id>/guardians/<int:guardian_id>", methods=["DELETE"])
@role_required("admin")
def delete_student_guardian(student_id, guardian_id):
    try:
        student_service.delete_guardian(student_id, guardian_id)
        return api_ok({"deleted": True})
    except student_service.StudentManagementError as exc:
        return api_error(str(exc), exc.status_code)


def _check_view_permission(user, student):
    """返回 api_error 响应（无权）或 None（通过）。"""
    role = user.role.value if user.role else None
    if role == "admin":
        return None
    if role == "parent":
        if student.id not in (user.student_ids or []):
            return api_error("无权访问该学生数据", 403)
    elif role == "teacher":
        if student.class_id not in (user.managed_class_ids or []):
            return api_error("无权访问该学生数据", 403)
    elif role == "grade_leader":
        grade_id = student.class_.grade_id if student.class_ and student.class_.grade else None
        if grade_id not in (user.managed_grade_ids or []):
            return api_error("无权访问该学生数据", 403)
    return None
