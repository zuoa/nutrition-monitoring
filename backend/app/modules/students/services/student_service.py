"""学生本地服务：列表（组织筛选 + 角色作用域）、详情、本地字段编辑、监护人查询。

角色作用域（与权限一致）：
- admin：全部
- teacher：仅其 ``managed_class_ids`` 内的班级
- grade_leader：仅其 ``managed_grade_ids`` 内年级下属班级
- parent：仅其 ``student_ids`` 内学生
"""
import logging

from app import db
from app.models import Report, ReportTypeEnum, User
from app.modules.students.models.student import Student
from app.modules.students.models.guardian import Guardian
from app.modules.students.models.organization import Class, Grade, Stage, Campus
from app.utils.pagination import paginate

logger = logging.getLogger(__name__)

# 仅允许本地编辑的字段。
# 注：name 对钉钉托管学生会在下次同步被覆盖，但对 source=local/csv 的学生是必填可编辑项，
# 故纳入此处；student_no 对钉钉托管学生也会随下次同步按钉钉数据更新。
LOCAL_EDITABLE_FIELDS = {
    "student_no", "card_no", "gender", "is_locally_disabled", "class_id", "name",
}


def _build_student_query(user: User, args: dict):
    """构造 Student 列表 query：角色作用域 + 组织筛选，组织表 join 仅做一次。

    join 链按需向上延展到能覆盖所有筛选项的最深层级（Class→Grade→Stage→Campus），
    避免同一张表被多次 join。
    """
    q = Student.query.filter_by(is_active=True)
    role = user.role.value if user.role else None

    teacher_class_ids = [c for c in (user.managed_class_ids or []) if isinstance(c, int)]
    grade_leader_grade_ids = [g for g in (user.managed_grade_ids or []) if isinstance(g, int)]
    parent_sids = [s for s in (user.student_ids or []) if isinstance(s, int)]

    # 决定需要 join 到哪一层
    need_class = bool(
        grade_leader_grade_ids or args.get("grade_id") or args.get("stage_id")
        or args.get("campus_id") or args.get("school_id")
    )
    need_grade = bool(args.get("stage_id") or args.get("campus_id") or args.get("school_id"))
    need_stage = bool(args.get("campus_id") or args.get("school_id"))
    need_campus = bool(args.get("school_id"))

    if need_class:
        q = q.join(Class, Student.class_id == Class.id, isouter=True)
    if need_grade:
        q = q.join(Grade, Class.grade_id == Grade.id, isouter=True)
    if need_stage:
        q = q.join(Stage, Grade.stage_id == Stage.id, isouter=True)
    if need_campus:
        q = q.join(Campus, Stage.campus_id == Campus.id, isouter=True)

    # 角色作用域
    if role == "teacher":
        q = q.filter(Student.class_id.in_(teacher_class_ids)) if teacher_class_ids else q.filter(False)
    elif role == "grade_leader":
        q = q.filter(Class.grade_id.in_(grade_leader_grade_ids)) if grade_leader_grade_ids else q.filter(False)
    elif role == "parent":
        q = q.filter(Student.id.in_(parent_sids)) if parent_sids else q.filter(False)
    # admin / canteen_manager：不限

    # 组织筛选
    if args.get("class_id"):
        q = q.filter(Student.class_id == int(args["class_id"]))
    if args.get("grade_id"):
        q = q.filter(Class.grade_id == int(args["grade_id"]))
    if args.get("stage_id"):
        q = q.filter(Grade.stage_id == int(args["stage_id"]))
    if args.get("campus_id"):
        q = q.filter(Stage.campus_id == int(args["campus_id"]))
    if args.get("school_id"):
        q = q.filter(Campus.school_id == int(args["school_id"]))
    return q


def list_students(user: User, args: dict, include_latest_report: bool = False):
    q = _build_student_query(user, args)
    if search := args.get("search"):
        q = q.filter(db.or_(
            Student.name.ilike(f"%{search}%"),
            Student.student_no.ilike(f"%{search}%"),
        ))
    q = q.order_by(Student.class_id, Student.name)
    items, total, page, page_size = paginate(q)
    reports_by_target = _load_latest_personal_reports(items) if include_latest_report else {}
    payload = []
    for student in items:
        data = student.to_dict()
        if include_latest_report:
            data["latest_report"] = _build_latest_report_summary(reports_by_target.get(str(student.id)))
        payload.append(data)
    return payload, total, page, page_size


def get_student(student_id: int) -> Student | None:
    return Student.query.get(student_id)


def update_student(student_id: int, data: dict) -> Student | None:
    student = Student.query.get(student_id)
    if not student:
        return None
    # is_active 作为 is_locally_disabled 的兼容入口：旧前端直接传 is_active 时，
    # 在此转成 is_locally_disabled，避免被静默忽略（is_locally_disabled 优先）。
    if "is_active" in data and "is_locally_disabled" not in data:
        data = {**data, "is_locally_disabled": not bool(data["is_active"])}
    for field in LOCAL_EDITABLE_FIELDS:
        if field in data:
            setattr(student, field, data[field])
    # 本地禁用与 is_active 保持一致
    if "is_locally_disabled" in data:
        student.is_active = not bool(student.is_locally_disabled)
    db.session.commit()
    return student


def list_guardians(student_id: int) -> list[Guardian]:
    return Guardian.query.filter_by(student_id=student_id).order_by(Guardian.id).all()


def _build_latest_report_summary(report: Report | None) -> dict | None:
    if report is None:
        return None
    content = report.content or {}
    alerts = content.get("alerts") or []
    return {
        "report_id": report.id,
        "overall_score": content.get("overall_score"),
        "alert_count": len(alerts),
        "period_start": report.period_start.isoformat() if report.period_start else None,
        "period_end": report.period_end.isoformat() if report.period_end else None,
        "summary": report.summary,
        "created_at": report.created_at.isoformat() if report.created_at else None,
    }


def _load_latest_personal_reports(students: list[Student]) -> dict[str, Report]:
    target_ids = [str(s.id) for s in students]
    if not target_ids:
        return {}
    reports = Report.query.filter(
        Report.report_type == ReportTypeEnum.personal_weekly,
        Report.target_id.in_(target_ids),
    ).order_by(Report.target_id.asc(), Report.period_start.desc(), Report.created_at.desc()).all()
    latest: dict[str, Report] = {}
    for r in reports:
        latest.setdefault(r.target_id, r)
    return latest
