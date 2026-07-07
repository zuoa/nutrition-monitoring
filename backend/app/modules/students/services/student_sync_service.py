"""同步班级内学生与监护人。

字段策略：
- 钉钉托管（每次覆盖）：name / dingtalk_user_id / class_id / source
- 本地可编辑（同步不动）：card_no / student_no / gender / is_locally_disabled
- ``is_active`` 仅在「未本地禁用」时由同步置 True，本地禁用的学生不会被复活。

监护人按 (student_id, dingtalk_user_id) upsert，并尽量按 dingtalk_user_id 关联到
本地 ``User``(parent)，回填 ``User.student_ids`` 以支持报告推送。
"""
import logging
from datetime import datetime, timezone

from app import db
from app.models import User, RoleEnum
from app.modules.students.models.student import Student, StudentSourceEnum
from app.modules.students.models.guardian import Guardian
from app.modules.students.models.organization import Class
from app.modules.students.services.dingtalk_edu import DingTalkEduService

logger = logging.getLogger(__name__)


def _utcnow():
    return datetime.now(timezone.utc)


class StudentSyncService:
    def __init__(self, edu_service: DingTalkEduService):
        self.edu = edu_service

    def sync(self) -> dict:
        now = _utcnow()
        stats = {"classes": 0, "students": 0, "students_created": 0, "guardians": 0}

        classes = Class.query.filter(
            Class.dingtalk_node_id.isnot(None),
            Class.is_active.is_(True),
        ).all()
        stats["classes"] = len(classes)

        for cls in classes:
            try:
                members = self.edu.get_node_members(cls.dingtalk_node_id) or []
                relations = self.edu.get_student_relations(cls.dingtalk_node_id) or []
            except Exception as exc:
                logger.warning("拉取班级 %s(%s) 成员失败：%s", cls.id, cls.dingtalk_node_id, exc)
                continue

            # 学生成员 upsert
            student_by_dtalk = {}
            for m in members:
                if m.get("identity") != "student":
                    continue
                student, was_created = self._upsert_student(m, cls, now)
                if student:
                    student_by_dtalk[m["dingtalk_user_id"]] = student
                    stats["students"] += 1
                    if was_created:
                        stats["students_created"] += 1

            # 监护人 upsert（优先用 relations；缺失时跳过）
            for rel in relations:
                student = student_by_dtalk.get(rel.get("student_user_id"))
                if not student:
                    # 按钉钉学生 id 兜底查
                    student = Student.query.filter_by(
                        dingtalk_user_id=rel.get("student_user_id")
                    ).first()
                if not student:
                    continue
                if self._upsert_guardian(student, rel, now):
                    stats["guardians"] += 1

        db.session.commit()
        logger.info("学生/监护人同步完成：%s", stats)
        return stats

    def _upsert_student(self, member: dict, cls: Class, now) -> tuple[Student | None, bool]:
        dingtalk_id = (member.get("dingtalk_user_id") or "").strip()
        if not dingtalk_id:
            return None, False
        created = False
        student = Student.query.filter_by(dingtalk_user_id=dingtalk_id).first()
        if not student:
            student = Student(
                student_no=dingtalk_id,  # 占位学号，管理员可改；满足唯一约束
                name=member.get("name") or dingtalk_id,
                dingtalk_user_id=dingtalk_id,
                class_id=cls.id,
                source=StudentSourceEnum.dingtalk,
                is_active=True,
            )
            db.session.add(student)
            created = True
        else:
            # 钉钉托管字段覆盖；本地字段（card_no/student_no/gender/is_locally_disabled）不动
            student.name = member.get("name") or student.name
            student.dingtalk_user_id = dingtalk_id
            student.class_id = cls.id
            student.source = StudentSourceEnum.dingtalk
            if not student.is_locally_disabled:
                student.is_active = True
        student.sync_at = now
        db.session.flush()
        return student, created

    def _upsert_guardian(self, student: Student, rel: dict, now) -> bool:
        guid = (rel.get("guardian_user_id") or "").strip() or None
        guardian = None
        if guid:
            guardian = Guardian.query.filter_by(
                student_id=student.id, dingtalk_user_id=guid
            ).first()
        if not guardian:
            guardian = Guardian(student_id=student.id, dingtalk_user_id=guid)
            db.session.add(guardian)
            added = True
        else:
            added = False
        guardian.name = rel.get("guardian_name") or guardian.name or "家长"
        if rel.get("relation"):
            guardian.relation = rel["relation"]
        guardian.sync_at = now

        # 关联到本地 parent 用户，回填 student_ids
        if guid:
            user = User.query.filter_by(dingtalk_user_id=guid).first()
            if user and user.role == RoleEnum.parent:
                guardian.user_id = user.id
                sids = set(user.student_ids or [])
                sids.add(student.id)
                user.student_ids = sorted(sids)
        db.session.flush()
        return added
