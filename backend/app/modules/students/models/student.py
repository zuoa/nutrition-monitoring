"""学生模型（学生管理独立模块）。

重构要点：
- ``id`` 保持不变（消费记录 / 匹配结果按主键引用，迁移不影响）。
- 旧的松散字符串 ``class_id``/``grade_id`` 在迁移中被改名为
  ``legacy_class_code``/``legacy_grade_code``，并新增整型外键 ``class_id``。
- 字段分两类：
  * 钉钉托管（每次同步覆盖）：name / dingtalk_user_id / student_no / class_id / source
  * 本地可编辑（同步不动）：card_no / gender / is_locally_disabled
- ``is_active`` 为可查询的有效标志；``is_locally_disabled`` 为本地禁用覆盖位，
  同步只会在「未本地禁用」时把学生置为 active，绝不会因本地禁用而复活。
"""
import enum
from datetime import datetime, timezone

from app import db


def _utcnow():
    return datetime.now(timezone.utc)


class StudentSourceEnum(str, enum.Enum):
    dingtalk = "dingtalk"  # 来自钉钉家校通讯录同步
    local = "local"        # 本地手工创建
    csv = "csv"            # CSV 导入


class Student(db.Model):
    __tablename__ = "students"

    id = db.Column(db.Integer, primary_key=True)
    student_no = db.Column(db.String(64), unique=True, nullable=False, index=True)
    name = db.Column(db.String(64), nullable=False)

    # 组织架构外键（替代旧的松散字符串）
    class_id = db.Column(db.Integer, db.ForeignKey("classes.id"), nullable=True, index=True)
    class_ = db.relationship("Class", backref="students", foreign_keys=[class_id])

    # 钉钉身份
    dingtalk_user_id = db.Column(db.String(64), nullable=True, index=True)
    gender = db.Column(db.String(16), nullable=True)
    source = db.Column(
        db.Enum(StudentSourceEnum),
        default=StudentSourceEnum.local,
        nullable=False,
    )

    # 本地可编辑字段（同步不覆盖）
    card_no = db.Column(db.String(64), index=True)
    is_locally_disabled = db.Column(db.Boolean, default=False, nullable=False)

    sync_at = db.Column(db.DateTime(timezone=True))

    # 迁移期保留的旧字符串（已 deprecated，仅用于回填与兼容展示）
    legacy_class_code = db.Column(db.String(64), nullable=True)
    legacy_grade_code = db.Column(db.String(64), nullable=True)
    class_name = db.Column(db.String(64), nullable=True)  # 兼容缓存
    grade_name = db.Column(db.String(64), nullable=True)  # 兼容缓存

    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    def to_dict(self):
        cls = self.class_
        grade = cls.grade if cls else None
        if cls:
            class_name = cls.name
            grade_name = grade.name if grade else None
            grade_id = grade.id if grade else None
        else:
            # 回退到迁移期缓存，保证旧数据仍可展示
            class_name = self.class_name
            grade_name = self.grade_name
            grade_id = None
        return {
            "id": self.id,
            "student_no": self.student_no,
            "name": self.name,
            "class_id": self.class_id,
            "class_name": class_name,
            "grade_id": grade_id,
            "grade_name": grade_name,
            "dingtalk_user_id": self.dingtalk_user_id,
            "gender": self.gender,
            "source": self.source.value if self.source else None,
            "card_no": self.card_no,
            "is_active": self.is_active,
            "is_locally_disabled": self.is_locally_disabled,
            "sync_at": self.sync_at.isoformat() if self.sync_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<Student {self.name} ({self.student_no})>"
