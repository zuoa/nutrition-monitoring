"""学生 API：列表（组织筛选 + 角色作用域）、详情、本地字段编辑、监护人。"""
from flask import Blueprint, request

from app.utils.jwt_utils import role_required, login_required, api_ok, api_error
from app.utils.pagination import paginated_response
from app.modules.students.services import student_service

bp = Blueprint("students", __name__)


@bp.route("", methods=["GET"])
@bp.route("/", methods=["GET"])
@role_required("admin", "teacher", "grade_leader", "parent")
def list_students():
    user = request.current_user
    args = request.args.to_dict()
    include_latest_report = str(args.pop("include_latest_report", "")).lower() in {"1", "true", "yes"}
    items, total, page, page_size = student_service.list_students(user, args, include_latest_report)
    return api_ok(paginated_response(items, total, page, page_size))


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
    student = student_service.update_student(student_id, data)
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
