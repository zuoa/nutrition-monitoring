"""学生本地服务：列表（组织筛选 + 角色作用域）、详情、本地字段编辑、监护人查询。

角色作用域（与权限一致）：
- admin：全部
- teacher：仅其 ``managed_class_ids`` 内的班级
- grade_leader：仅其 ``managed_grade_ids`` 内年级下属班级
- parent：仅其 ``student_ids`` 内学生
"""
import logging

from app import db
from app.models import Report, ReportTypeEnum, User, RoleEnum
from app.modules.students.models.student import (
    Student,
    StudentSourceEnum,
    EnrollmentStatusEnum,
)
from app.modules.students.models.guardian import Guardian
from app.modules.students.models.organization import Class, Grade, Stage, Campus
from app.utils.pagination import paginate

logger = logging.getLogger(__name__)


class StudentManagementError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _build_student_query(user: User, args: dict):
    """构造 Student 列表 query：角色作用域 + 组织筛选，组织表 join 仅做一次。

    join 链按需向上延展到能覆盖所有筛选项的最深层级（Class→Grade→Stage→Campus），
    避免同一张表被多次 join。
    """
    q = Student.query
    role = user.role.value if user.role else None

    teacher_class_ids = [c for c in (user.managed_class_ids or []) if isinstance(c, int)]
    grade_leader_grade_ids = [g for g in (user.managed_grade_ids or []) if isinstance(g, int)]
    parent_sids = [s for s in (user.student_ids or []) if isinstance(s, int)]

    status = args.get("status", "enrolled")
    if status == "enrolled":
        q = q.filter(
            Student.enrollment_status == EnrollmentStatusEnum.enrolled,
            Student.is_active.is_(True),
        )
    elif status == "disabled":
        q = q.filter(
            Student.enrollment_status == EnrollmentStatusEnum.enrolled,
            Student.is_locally_disabled.is_(True),
        )
    elif status == "graduated":
        q = q.filter(Student.enrollment_status == EnrollmentStatusEnum.graduated)
    elif status != "all":
        raise StudentManagementError("学生状态筛选无效")

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
    return db.session.get(Student, student_id)


def create_student(data: dict) -> Student:
    student_no = str(data.get("student_no") or "").strip()
    name = str(data.get("name") or "").strip()
    if not student_no or not name:
        raise StudentManagementError("学号和姓名不能为空")
    if Student.query.filter_by(student_no=student_no).first():
        raise StudentManagementError("学号已存在", 409)
    class_ = _active_class(data.get("class_id"))
    student = Student(
        student_no=student_no,
        name=name,
        class_id=class_.id,
        card_no=_optional_text(data.get("card_no")),
        gender=_optional_text(data.get("gender")),
        source=StudentSourceEnum.local,
        enrollment_status=EnrollmentStatusEnum.enrolled,
        is_locally_disabled=bool(data.get("is_locally_disabled", False)),
        is_active=not bool(data.get("is_locally_disabled", False)),
    )
    db.session.add(student)
    db.session.commit()
    return student


def update_student(student_id: int, data: dict) -> Student | None:
    student = db.session.get(Student, student_id)
    if not student:
        return None
    # is_active 作为 is_locally_disabled 的兼容入口：旧前端直接传 is_active 时，
    # 在此转成 is_locally_disabled，避免被静默忽略（is_locally_disabled 优先）。
    if "is_active" in data and "is_locally_disabled" not in data:
        data = {**data, "is_locally_disabled": not bool(data["is_active"])}
    if "student_no" in data:
        student_no = str(data.get("student_no") or "").strip()
        if not student_no:
            raise StudentManagementError("学号不能为空")
        duplicate = Student.query.filter(Student.student_no == student_no, Student.id != student.id).first()
        if duplicate:
            raise StudentManagementError("学号已存在", 409)
        student.student_no = student_no
    if "name" in data:
        name = str(data.get("name") or "").strip()
        if not name:
            raise StudentManagementError("姓名不能为空")
        student.name = name
    if "class_id" in data:
        student.class_id = _active_class(data.get("class_id")).id
    for field in ("card_no", "gender"):
        if field in data:
            setattr(student, field, _optional_text(data.get(field)))
    if "is_locally_disabled" in data:
        student.is_locally_disabled = bool(data["is_locally_disabled"])
    if "enrollment_status" in data:
        try:
            status = EnrollmentStatusEnum(str(data["enrollment_status"]))
        except ValueError as exc:
            raise StudentManagementError("学生在校状态无效") from exc
        if status == EnrollmentStatusEnum.enrolled and "class_id" not in data and not student.class_id:
            raise StudentManagementError("恢复为在校生时必须选择班级")
        student.enrollment_status = status
    student.is_active = (
        student.enrollment_status == EnrollmentStatusEnum.enrolled
        and not student.is_locally_disabled
    )
    db.session.commit()
    return student


def list_guardians(student_id: int) -> list[Guardian]:
    return Guardian.query.filter_by(student_id=student_id).order_by(Guardian.id).all()


def create_guardian(student_id: int, data: dict) -> Guardian:
    student = db.session.get(Student, student_id)
    if not student:
        raise StudentManagementError("学生不存在", 404)
    guardian = Guardian(student_id=student.id, name="")
    _apply_guardian_data(guardian, data, creating=True)
    db.session.add(guardian)
    db.session.commit()
    return guardian


def update_guardian(student_id: int, guardian_id: int, data: dict) -> Guardian:
    guardian = Guardian.query.filter_by(id=guardian_id, student_id=student_id).first()
    if not guardian:
        raise StudentManagementError("监护人不存在", 404)
    old_user_id = guardian.user_id
    _apply_guardian_data(guardian, data, creating=False)
    db.session.commit()
    if old_user_id and old_user_id != guardian.user_id:
        _unlink_parent_if_unused(old_user_id, student_id)
        db.session.commit()
    return guardian


def delete_guardian(student_id: int, guardian_id: int):
    guardian = Guardian.query.filter_by(id=guardian_id, student_id=student_id).first()
    if not guardian:
        raise StudentManagementError("监护人不存在", 404)
    user_id = guardian.user_id
    db.session.delete(guardian)
    db.session.flush()
    if user_id:
        _unlink_parent_if_unused(user_id, student_id)
    db.session.commit()


def _apply_guardian_data(guardian: Guardian, data: dict, creating: bool):
    if creating or "name" in data:
        name = str(data.get("name") or "").strip()
        if not name:
            raise StudentManagementError("监护人姓名不能为空")
        guardian.name = name
    for field in ("relation", "phone"):
        if field in data:
            setattr(guardian, field, _optional_text(data.get(field)))
    if "user_id" in data:
        raw_user_id = data.get("user_id")
        if raw_user_id in (None, ""):
            guardian.user_id = None
        else:
            try:
                user_id = int(raw_user_id)
            except (TypeError, ValueError) as exc:
                raise StudentManagementError("家长账号 ID 无效") from exc
            user = db.session.get(User, user_id)
            if not user or user.role != RoleEnum.parent or not user.is_active:
                raise StudentManagementError("家长账号不存在或不可用")
            guardian.user_id = user.id
            ids = list(user.student_ids or [])
            if guardian.student_id not in ids:
                user.student_ids = [*ids, guardian.student_id]


def _unlink_parent_if_unused(user_id: int, student_id: int):
    still_linked = Guardian.query.filter_by(user_id=user_id, student_id=student_id).first()
    if still_linked:
        return
    user = db.session.get(User, user_id)
    if user:
        user.student_ids = [sid for sid in (user.student_ids or []) if sid != student_id]


def _active_class(class_id) -> Class:
    try:
        value = int(class_id)
    except (TypeError, ValueError) as exc:
        raise StudentManagementError("请选择班级") from exc
    class_ = db.session.get(Class, value)
    if not class_ or not class_.is_active:
        raise StudentManagementError("班级不存在或已归档")
    return class_


def _optional_text(value):
    return str(value).strip() or None if value is not None else None


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
