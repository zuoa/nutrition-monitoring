"""同步班级内学生与监护人。

字段策略：
- 钉钉托管（每次覆盖）：name / dingtalk_user_id / student_no / class_id / source
- 本地可编辑（同步不动）：card_no / gender / is_locally_disabled
- ``is_active`` 仅在「未本地禁用」时由同步置 True，本地禁用的学生不会被复活。

监护人按 (student_id, dingtalk_user_id) upsert，同时按 dingtalk_user_id 创建/更新
本地 ``User``(parent)，回填 ``User.student_ids`` 以支持报告推送和家长登录。
"""
import logging
from datetime import datetime, timezone

from app import db
from app.models import User, RoleEnum
from app.modules.students.models.student import (
    Student,
    StudentSourceEnum,
    EnrollmentStatusEnum,
)
from app.modules.students.models.guardian import Guardian
from app.modules.students.models.organization import Class

logger = logging.getLogger(__name__)


def _utcnow():
    return datetime.now(timezone.utc)


class StudentSyncService:
    def __init__(self, edu_service):
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

            # 每个班级独立保存点：单个班级入库失败（如学号与本地数据撞唯一约束）
            # 只回滚该班级，不影响其余班级，避免一次碰撞作废整次同步
            class_delta = {"students": 0, "students_created": 0, "guardians": 0}
            try:
                with db.session.begin_nested():
                    member_by_dtalk = {
                        (m.get("dingtalk_user_id") or "").strip(): m
                        for m in members
                        if (m.get("dingtalk_user_id") or "").strip()
                    }
                    # 学生成员 upsert
                    student_by_dtalk = {}
                    for m in members:
                        if m.get("identity") != "student":
                            continue
                        student, was_created = self._upsert_student(m, cls, now)
                        if student:
                            student_by_dtalk[m["dingtalk_user_id"]] = student
                            class_delta["students"] += 1
                            if was_created:
                                class_delta["students_created"] += 1

                    guardian_relations = self._guardian_relations_from_api(
                        relations,
                        members,
                        student_by_dtalk,
                        member_by_dtalk,
                    )

                    # 监护人 upsert（优先用 relations；关系缺失时使用可安全推断的 guardian 成员）
                    for rel in guardian_relations:
                        rel = self._relation_with_member_names(rel, member_by_dtalk)
                        student = student_by_dtalk.get(rel.get("student_user_id"))
                        if not student:
                            # 按钉钉学生 id 兜底查
                            student = Student.query.filter_by(
                                dingtalk_user_id=rel.get("student_user_id")
                            ).first()
                        if not student:
                            continue
                        if self._upsert_guardian(student, rel, now):
                            class_delta["guardians"] += 1
            except Exception as exc:
                # 保存点已回滚该班级；外层 session 仍可用，继续其余班级
                logger.warning("班级 %s(%s) 学生/监护人入库失败，已回滚该班级：%s",
                               cls.id, cls.dingtalk_node_id, exc)
                continue
            # 保存点已提交，累加该班级统计
            stats["students"] += class_delta["students"]
            stats["students_created"] += class_delta["students_created"]
            stats["guardians"] += class_delta["guardians"]

        db.session.commit()
        logger.info("学生/监护人同步完成：%s", stats)
        return stats

    def _guardian_relations_from_api(
        self,
        relations: list[dict],
        members: list[dict],
        student_by_dtalk: dict,
        member_by_dtalk: dict,
    ) -> list[dict]:
        normalized = [
            rel
            for rel in relations
            if (rel.get("student_user_id") or "").strip()
            and (rel.get("guardian_user_id") or "").strip()
        ]
        if normalized:
            return normalized

        fallback = []
        for member in members:
            if member.get("identity") != "parent":
                continue
            guardian_id = (member.get("dingtalk_user_id") or "").strip()
            if not guardian_id:
                continue
            student_id = self._student_id_from_guardian_member(member)
            if not student_id and len(student_by_dtalk) == 1:
                student_id = next(iter(student_by_dtalk))
            if not student_id or student_id not in student_by_dtalk:
                logger.warning(
                    "无法从钉钉 guardian 成员推断学生关系，跳过监护人：guardian_user_id=%s name=%s",
                    guardian_id,
                    member.get("name", ""),
                )
                continue
            fallback.append({
                "student_user_id": student_id,
                "student_name": member_by_dtalk.get(student_id, {}).get("name", ""),
                "guardian_user_id": guardian_id,
                "guardian_name": member.get("name", ""),
                "relation": member.get("relation"),
            })
        return fallback

    def _student_id_from_guardian_member(self, member: dict) -> str:
        candidates = (
            member.get("student_user_id"),
            member.get("student_userid"),
            member.get("student_dingtalk_user_id"),
            member.get("student_dingtalk_id"),
            member.get("to_userid"),
            member.get("to_user_id"),
        )
        for value in candidates:
            normalized = str(value or "").strip()
            if normalized:
                return normalized
        feature = member.get("feature")
        if isinstance(feature, dict):
            for key in (
                "student_user_id",
                "student_userid",
                "student_dingtalk_user_id",
                "student_dingtalk_id",
                "to_userid",
                "to_user_id",
            ):
                normalized = str(feature.get(key) or "").strip()
                if normalized:
                    return normalized
        return ""

    def _relation_with_member_names(self, rel: dict, member_by_dtalk: dict) -> dict:
        enriched = dict(rel)
        student_user_id = (enriched.get("student_user_id") or "").strip()
        guardian_user_id = (enriched.get("guardian_user_id") or "").strip()
        if not enriched.get("student_name") and student_user_id in member_by_dtalk:
            enriched["student_name"] = member_by_dtalk[student_user_id].get("name") or ""
        if not enriched.get("guardian_name") and guardian_user_id in member_by_dtalk:
            enriched["guardian_name"] = member_by_dtalk[guardian_user_id].get("name") or ""
        return enriched

    def _upsert_student(self, member: dict, cls: Class, now) -> tuple[Student | None, bool]:
        dingtalk_id = (member.get("dingtalk_user_id") or "").strip()
        if not dingtalk_id:
            return None, False
        incoming_student_no = self._student_no_from_member(member)
        created = False
        student = Student.query.filter_by(dingtalk_user_id=dingtalk_id).first()
        if not student:
            local_match = Student.query.filter_by(student_no=incoming_student_no).first() if incoming_student_no else None
            if local_match and local_match.source != StudentSourceEnum.dingtalk:
                logger.warning(
                    "钉钉学生学号与本地学生冲突，保留本地记录：student_no=%s dingtalk_user_id=%s",
                    incoming_student_no,
                    dingtalk_id,
                )
                return None, False
            student = Student(
                student_no=incoming_student_no or dingtalk_id,
                name=member.get("name") or dingtalk_id,
                dingtalk_user_id=dingtalk_id,
                sync_provider="dingtalk",
                external_id=dingtalk_id,
                class_id=cls.id,
                source=StudentSourceEnum.dingtalk,
                enrollment_status=EnrollmentStatusEnum.enrolled,
                is_active=True,
            )
            db.session.add(student)
            created = True
        else:
            # dingtalk_user_id 是稳定外部主键；student_no 允许随钉钉学号变更而更新。
            if incoming_student_no:
                student.student_no = incoming_student_no
            student.name = member.get("name") or student.name
            student.dingtalk_user_id = dingtalk_id
            student.sync_provider = "dingtalk"
            student.external_id = dingtalk_id
            student.source = StudentSourceEnum.dingtalk
            if student.enrollment_status == EnrollmentStatusEnum.enrolled:
                student.class_id = cls.id
            if (
                student.enrollment_status == EnrollmentStatusEnum.enrolled
                and not student.is_locally_disabled
            ):
                student.is_active = True
        student.sync_at = now
        db.session.flush()
        return student, created

    def _student_no_from_member(self, member: dict) -> str:
        value = str(member.get("student_no") or "").strip()
        if value:
            return value
        feature = member.get("feature")
        if isinstance(feature, dict):
            for key in (
                "student_no",
                "studentNo",
                "student_number",
                "studentNumber",
                "student_code",
                "studentCode",
                "student_id",
                "studentId",
                "stu_no",
                "stuNo",
                "study_no",
                "学号",
            ):
                value = str(feature.get(key) or "").strip()
                if value:
                    return value
        return ""

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
            if not user:
                user = User(
                    dingtalk_user_id=guid,
                    name=guardian.name,
                    role=RoleEnum.parent,
                    student_ids=[],
                    is_active=True,
                    sync_at=now,
                )
                db.session.add(user)
                db.session.flush()
            if user.role == RoleEnum.parent:
                user.name = guardian.name or user.name
                user.is_active = True
                user.sync_at = now
                sids = set(user.student_ids or [])
                sids.add(student.id)
                user.student_ids = sorted(sids)
                guardian.user_id = user.id
        db.session.flush()
        return added
