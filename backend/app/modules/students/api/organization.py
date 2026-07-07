"""组织架构 API：学校列表、整树（含各级学生数）、班级学生。"""
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
    counts = dict(
        db.session.query(Student.class_id, func.count(Student.id))
        .filter(Student.class_id.isnot(None), Student.is_active.is_(True))
        .group_by(Student.class_id)
        .all()
    )

    schools = School.query.filter_by(is_active=True).order_by(School.sort_order, School.id).all()
    tree = []
    for school in schools:
        campuses = (
            Campus.query.filter_by(school_id=school.id, is_active=True)
            .order_by(Campus.sort_order, Campus.id).all()
        )
        campus_nodes = []
        for campus in campuses:
            campus_nodes.append({
                **campus.to_dict(),
                "stages": _stages(campus.id, counts),
            })
        tree.append({**school.to_dict(), "campuses": campus_nodes})
    return api_ok(tree)


def _stages(campus_id, counts):
    stages = (
        Stage.query.filter_by(campus_id=campus_id, is_active=True)
        .order_by(Stage.sort_order, Stage.id).all()
    )
    out = []
    for stage in stages:
        grades = (
            Grade.query.filter_by(stage_id=stage.id, is_active=True)
            .order_by(Grade.sort_order, Grade.id).all()
        )
        grade_nodes = []
        for grade in grades:
            classes = (
                Class.query.filter_by(grade_id=grade.id, is_active=True)
                .order_by(Class.sort_order, Class.id).all()
            )
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
