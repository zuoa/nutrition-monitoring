"""组织架构 API：树、手工维护、班级学生与升年级。"""
from flask import Blueprint, request
from sqlalchemy import func

from app import db
from app.modules.students.models.organization import School, Campus, Stage, Grade, Class
from app.modules.students.models.student import Student
from app.utils.jwt_utils import role_required, api_ok, api_error
from app.utils.pagination import paginate, paginated_response

bp = Blueprint("org", __name__)


@bp.route("/schools", methods=["GET"])
@role_required("admin", "teacher", "grade_leader")
def list_schools():
    schools = School.query.filter_by(is_active=True).order_by(School.sort_order, School.id).all()
    return api_ok([s.to_dict() for s in schools])


@bp.route("/tree", methods=["GET"])
@role_required("admin", "teacher", "grade_leader")
def org_tree():
    """返回完整组织树，班级节点附带 student_count。"""
    include_archived = (
        request.current_user.role.value == "admin"
        and str(request.args.get("include_archived", "")).lower() in {"1", "true", "yes"}
    )
    counts = dict(
        db.session.query(Student.class_id, func.count(Student.id))
        .filter(Student.class_id.isnot(None), Student.is_active.is_(True))
        .group_by(Student.class_id)
        .all()
    )

    school_query = School.query
    if not include_archived:
        school_query = school_query.filter_by(is_active=True)
    schools = school_query.order_by(School.sort_order, School.id).all()
    tree = []
    for school in schools:
        campus_query = Campus.query.filter_by(school_id=school.id)
        if not include_archived:
            campus_query = campus_query.filter_by(is_active=True)
        campuses = campus_query.order_by(Campus.sort_order, Campus.id).all()
        campus_nodes = []
        for campus in campuses:
            campus_nodes.append({
                **campus.to_dict(),
                "stages": _stages(campus.id, counts, include_archived),
            })
        tree.append({**school.to_dict(), "campuses": campus_nodes})
    return api_ok(tree)


def _stages(campus_id, counts, include_archived=False):
    stage_query = Stage.query.filter_by(campus_id=campus_id)
    if not include_archived:
        stage_query = stage_query.filter_by(is_active=True)
    stages = stage_query.order_by(Stage.sort_order, Stage.id).all()
    out = []
    for stage in stages:
        grade_query = Grade.query.filter_by(stage_id=stage.id)
        if not include_archived:
            grade_query = grade_query.filter_by(is_active=True)
        grades = grade_query.order_by(Grade.sort_order, Grade.id).all()
        grade_nodes = []
        for grade in grades:
            class_query = Class.query.filter_by(grade_id=grade.id)
            if not include_archived:
                class_query = class_query.filter_by(is_active=True)
            classes = class_query.order_by(Class.sort_order, Class.id).all()
            grade_nodes.append({
                **grade.to_dict(),
                "classes": [
                    {**c.to_dict(), "student_count": counts.get(c.id, 0)}
                    for c in classes
                ],
            })
        out.append({**stage.to_dict(), "grades": grade_nodes})
    return out


@bp.route("/classes/<int:class_id>/students", methods=["GET"])
@role_required("admin", "teacher", "grade_leader")
def class_students(class_id):
    cls = Class.query.get_or_404(class_id)
    q = Student.query.filter_by(class_id=cls.id, is_active=True).order_by(Student.student_no)
    items, total, page, page_size = paginate(q)
    return api_ok(paginated_response([s.to_dict() for s in items], total, page, page_size))


@bp.route("/<kind>", methods=["POST"])
@role_required("admin")
def create_organization_node(kind):
    from app.modules.students.services.organization_service import (
        OrganizationError,
        create_node,
    )
    try:
        node = create_node(kind, request.get_json() or {})
        return api_ok(node.to_dict())
    except OrganizationError as exc:
        return api_error(str(exc), exc.status_code)


@bp.route("/<kind>/<int:node_id>", methods=["PUT"])
@role_required("admin")
def update_organization_node(kind, node_id):
    from app.modules.students.services.organization_service import (
        OrganizationError,
        update_node,
    )
    try:
        node = update_node(kind, node_id, request.get_json() or {})
        return api_ok(node.to_dict())
    except OrganizationError as exc:
        return api_error(str(exc), exc.status_code)


@bp.route("/<kind>/<int:node_id>/archive", methods=["POST"])
@role_required("admin")
def archive_organization_node(kind, node_id):
    return _set_node_archived(kind, node_id, True)


@bp.route("/<kind>/<int:node_id>/restore", methods=["POST"])
@role_required("admin")
def restore_organization_node(kind, node_id):
    return _set_node_archived(kind, node_id, False)


def _set_node_archived(kind, node_id, archived):
    from app.modules.students.services.organization_service import (
        OrganizationError,
        set_archived,
    )
    try:
        node = set_archived(kind, node_id, archived)
        return api_ok(node.to_dict())
    except OrganizationError as exc:
        return api_error(str(exc), exc.status_code)


@bp.route("/promotions/preview", methods=["POST"])
@role_required("admin")
def preview_promotion():
    from app.modules.students.services.promotion_service import (
        PromotionError,
        preview_promotion as build_preview,
    )
    try:
        return api_ok(build_preview(request.get_json() or {}))
    except PromotionError as exc:
        return api_error(str(exc), exc.status_code)


@bp.route("/promotions/apply", methods=["POST"])
@role_required("admin")
def apply_promotion():
    from app.modules.students.services.promotion_service import PromotionError, apply_promotion as apply
    data = request.get_json() or {}
    try:
        result = apply(data, str(data.get("preview_token") or ""))
        return api_ok(result)
    except PromotionError as exc:
        return api_error(str(exc), exc.status_code)
