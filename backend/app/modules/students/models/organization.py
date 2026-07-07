"""学校组织架构模型：学校 → 校区 → 学段 → 年级 → 班级（与钉钉家校通讯录 classic 一一对应）。

每一级都带有 ``dingtalk_node_id``，用于和钉钉家校通讯录 2.0 的节点做映射；
本地手工创建的节点该字段为 NULL。所有级别共享同一组通用字段（sort_order /
is_active / sync_at / created_at / updated_at），各自持有一个指向上级的
外键。
"""
import enum
from datetime import datetime, timezone

from app import db


def _utcnow():
    return datetime.now(timezone.utc)


class StageTypeEnum(str, enum.Enum):
    kindergarten = "kindergarten"  # 幼儿园
    primary = "primary"            # 小学
    junior = "junior"              # 初中
    senior = "senior"              # 高中
    other = "other"                # 其他/未区分


class School(db.Model):
    __tablename__ = "schools"

    id = db.Column(db.Integer, primary_key=True)
    dingtalk_node_id = db.Column(db.String(64), unique=True, nullable=True, index=True)
    name = db.Column(db.String(128), nullable=False)
    code = db.Column(db.String(64), nullable=True)  # 校代码（本地可选）
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    sync_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    campuses = db.relationship("Campus", backref="school", lazy="dynamic")

    def to_dict(self):
        return {
            "id": self.id,
            "dingtalk_node_id": self.dingtalk_node_id,
            "name": self.name,
            "code": self.code,
            "sort_order": self.sort_order,
            "is_active": self.is_active,
            "sync_at": self.sync_at.isoformat() if self.sync_at else None,
        }

    def __repr__(self):
        return f"<School {self.name}>"


class Campus(db.Model):
    __tablename__ = "campuses"

    id = db.Column(db.Integer, primary_key=True)
    dingtalk_node_id = db.Column(db.String(64), unique=True, nullable=True, index=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=False, index=True)
    name = db.Column(db.String(128), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    sync_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    stages = db.relationship("Stage", backref="campus", lazy="dynamic")

    def to_dict(self):
        return {
            "id": self.id,
            "dingtalk_node_id": self.dingtalk_node_id,
            "school_id": self.school_id,
            "name": self.name,
            "sort_order": self.sort_order,
            "is_active": self.is_active,
            "sync_at": self.sync_at.isoformat() if self.sync_at else None,
        }

    def __repr__(self):
        return f"<Campus {self.name}>"


class Stage(db.Model):
    __tablename__ = "stages"

    id = db.Column(db.Integer, primary_key=True)
    dingtalk_node_id = db.Column(db.String(64), unique=True, nullable=True, index=True)
    campus_id = db.Column(db.Integer, db.ForeignKey("campuses.id"), nullable=False, index=True)
    name = db.Column(db.String(128), nullable=False)
    stage_type = db.Column(db.Enum(StageTypeEnum), nullable=True)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    sync_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    grades = db.relationship("Grade", backref="stage", lazy="dynamic")

    def to_dict(self):
        return {
            "id": self.id,
            "dingtalk_node_id": self.dingtalk_node_id,
            "campus_id": self.campus_id,
            "name": self.name,
            "stage_type": self.stage_type.value if self.stage_type else None,
            "sort_order": self.sort_order,
            "is_active": self.is_active,
            "sync_at": self.sync_at.isoformat() if self.sync_at else None,
        }

    def __repr__(self):
        return f"<Stage {self.name}>"


class Grade(db.Model):
    __tablename__ = "grades"

    id = db.Column(db.Integer, primary_key=True)
    dingtalk_node_id = db.Column(db.String(64), unique=True, nullable=True, index=True)
    stage_id = db.Column(db.Integer, db.ForeignKey("stages.id"), nullable=False, index=True)
    name = db.Column(db.String(128), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    sync_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    classes = db.relationship("Class", backref="grade", lazy="dynamic")

    def to_dict(self):
        return {
            "id": self.id,
            "dingtalk_node_id": self.dingtalk_node_id,
            "stage_id": self.stage_id,
            "name": self.name,
            "sort_order": self.sort_order,
            "is_active": self.is_active,
            "sync_at": self.sync_at.isoformat() if self.sync_at else None,
        }

    def __repr__(self):
        return f"<Grade {self.name}>"


class Class(db.Model):
    """班级（组织树叶节点）。Python 类名 ``Class`` 不与关键字冲突（关键字是小写 ``class``）。"""

    __tablename__ = "classes"

    id = db.Column(db.Integer, primary_key=True)
    dingtalk_node_id = db.Column(db.String(64), unique=True, nullable=True, index=True)
    grade_id = db.Column(db.Integer, db.ForeignKey("grades.id"), nullable=False, index=True)
    name = db.Column(db.String(128), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    sync_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "dingtalk_node_id": self.dingtalk_node_id,
            "grade_id": self.grade_id,
            "name": self.name,
            "sort_order": self.sort_order,
            "is_active": self.is_active,
            "sync_at": self.sync_at.isoformat() if self.sync_at else None,
        }

    def __repr__(self):
        return f"<Class {self.name}>"
