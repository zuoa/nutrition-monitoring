"""把 Provider 返回的标准学生快照同步到本地组织与学生表。"""

import logging
from datetime import datetime, timezone

from app import db
from app.modules.students.models.organization import (
    Campus,
    Class,
    Grade,
    School,
    Stage,
    StageTypeEnum,
)
from app.modules.students.models.student import (
    EnrollmentStatusEnum,
    Student,
    StudentSourceEnum,
)
from app.modules.students.services.sync_provider import StudentRosterProvider

logger = logging.getLogger(__name__)


def _utcnow():
    return datetime.now(timezone.utc)


def _infer_stage_type(name: str) -> StageTypeEnum:
    for token, stage_type in (
        ("幼儿", StageTypeEnum.kindergarten),
        ("小学", StageTypeEnum.primary),
        ("初中", StageTypeEnum.junior),
        ("高中", StageTypeEnum.senior),
    ):
        if token in name:
            return stage_type
    return StageTypeEnum.other


class ExternalRosterSyncService:
    """通用的扁平学生名单入库服务。"""

    def __init__(
        self,
        provider: StudentRosterProvider,
        *,
        school_name: str,
        campus_name: str,
        stage_name: str,
        deactivate_missing: bool = False,
    ):
        self.provider = provider
        self.school_name = school_name.strip() or "默认学校"
        self.campus_name = campus_name.strip() or "默认校区"
        self.stage_name = stage_name.strip() or "默认学段"
        self.deactivate_missing = deactivate_missing

    def sync(self) -> dict:
        entries = self.provider.fetch_students()
        now = _utcnow()
        stats = {
            "total": len(entries),
            "students": 0,
            "students_created": 0,
            "students_updated": 0,
            "skipped": 0,
            "deactivated": 0,
            "deactivation_suppressed": False,
            "errors": [],
        }
        seen_external_ids: set[str] = set()
        school, campus, stage = self._ensure_root_org(now)

        for row_number, entry in enumerate(entries, start=1):
            error = self._validate_entry(entry)
            if error:
                stats["skipped"] += 1
                self._append_error(stats, row_number, error)
                continue
            if entry.external_id in seen_external_ids:
                stats["skipped"] += 1
                self._append_error(stats, row_number, "外部主键重复")
                continue

            seen_external_ids.add(entry.external_id)
            grade = self._get_or_create_grade(stage, entry.grade_name, now)
            class_ = self._get_or_create_class(grade, entry.class_name, now)
            student, created, error = self._upsert_student(entry, class_, now)
            if error:
                stats["skipped"] += 1
                self._append_error(stats, row_number, error)
                continue
            stats["students"] += 1
            stats["students_created" if created else "students_updated"] += 1

        # 空快照或包含坏数据的快照通常是上游故障，即使显式开启
        # 也不做全量停用。
        can_deactivate = bool(seen_external_ids) and stats["skipped"] == 0
        if self.deactivate_missing and can_deactivate:
            missing = Student.query.filter(
                Student.sync_provider == self.provider.key,
                Student.external_id.isnot(None),
                ~Student.external_id.in_(seen_external_ids),
            ).all()
            for student in missing:
                student.is_active = False
                student.sync_at = now
            stats["deactivated"] = len(missing)
        elif self.deactivate_missing:
            stats["deactivation_suppressed"] = True

        # 让统计不包含潜在的 PII；错误也只记行号和类型。
        stats["error_count"] = stats["skipped"]
        stats["org"] = {
            "school_id": school.id,
            "campus_id": campus.id,
            "stage_id": stage.id,
        }
        db.session.commit()
        logger.info("外部学生名单同步完成：provider=%s stats=%s", self.provider.key, stats)
        return stats

    @staticmethod
    def _validate_entry(entry) -> str | None:
        if not entry.external_id:
            return "缺少外部主键 user_code"
        if not entry.student_no:
            return "缺少学号"
        if not entry.name:
            return "缺少姓名"
        if not entry.grade_name:
            return "缺少年级"
        if not entry.class_name:
            return "缺少班级"
        return None

    @staticmethod
    def _append_error(stats: dict, row_number: int, message: str) -> None:
        # TaskLog.meta 只保留有限错误样本，防止大批坏数据撑大记录。
        if len(stats["errors"]) < 20:
            stats["errors"].append({"row": row_number, "message": message})

    def _ensure_root_org(self, now):
        school = School.query.filter_by(name=self.school_name).first()
        if not school:
            school = School(name=self.school_name)
            db.session.add(school)
            db.session.flush()
        school.is_active = True
        school.sync_at = now

        campus = Campus.query.filter_by(school_id=school.id, name=self.campus_name).first()
        if not campus:
            campus = Campus(school_id=school.id, name=self.campus_name)
            db.session.add(campus)
            db.session.flush()
        campus.is_active = True
        campus.sync_at = now

        stage = Stage.query.filter_by(campus_id=campus.id, name=self.stage_name).first()
        if not stage:
            stage = Stage(
                campus_id=campus.id,
                name=self.stage_name,
                stage_type=_infer_stage_type(self.stage_name),
            )
            db.session.add(stage)
            db.session.flush()
        stage.is_active = True
        stage.sync_at = now
        return school, campus, stage

    @staticmethod
    def _get_or_create_grade(stage: Stage, name: str, now) -> Grade:
        grade = Grade.query.filter_by(stage_id=stage.id, name=name).first()
        if not grade:
            grade = Grade(stage_id=stage.id, name=name)
            db.session.add(grade)
            db.session.flush()
        grade.is_active = True
        grade.sync_at = now
        return grade

    @staticmethod
    def _get_or_create_class(grade: Grade, name: str, now) -> Class:
        class_ = Class.query.filter_by(grade_id=grade.id, name=name).first()
        if not class_:
            class_ = Class(grade_id=grade.id, name=name)
            db.session.add(class_)
            db.session.flush()
        class_.is_active = True
        class_.sync_at = now
        return class_

    def _upsert_student(self, entry, class_: Class, now):
        student = Student.query.filter_by(
            sync_provider=self.provider.key,
            external_id=entry.external_id,
        ).first()
        created = False
        if not student:
            student_no_match = Student.query.filter_by(student_no=entry.student_no).first()
            if student_no_match:
                return None, False, "学号已由本地或其他数据源管理"
            student = Student(
                student_no=entry.student_no,
                registration_no=entry.registration_no or None,
                name=entry.name,
                class_id=class_.id,
                gender=entry.gender or None,
                source=StudentSourceEnum.api,
                sync_provider=self.provider.key,
                external_id=entry.external_id,
                enrollment_status=EnrollmentStatusEnum.enrolled,
                is_active=True,
            )
            db.session.add(student)
            created = True
        else:
            number_owner = Student.query.filter(
                Student.student_no == entry.student_no,
                Student.id != student.id,
            ).first()
            if number_owner:
                return None, False, "更新后学号与其他学生冲突"
            student.student_no = entry.student_no
            student.registration_no = entry.registration_no or student.registration_no
            student.name = entry.name
            student.gender = entry.gender or student.gender
            student.source = StudentSourceEnum.api
            if student.enrollment_status == EnrollmentStatusEnum.enrolled:
                student.class_id = class_.id
                if not student.is_locally_disabled:
                    student.is_active = True

        student.sync_provider = self.provider.key
        student.external_id = entry.external_id
        student.sync_at = now
        db.session.flush()
        return student, created, None
