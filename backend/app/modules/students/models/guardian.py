"""学生监护人（家长）模型。

监护人在钉钉家校通讯录中通过「班级内学生关系列表」获取。每条记录关联一个学生，
并尽量按 ``dingtalk_user_id`` 关联到本地 ``User``(parent 角色) 账号，以便 M6
报告推送到家长钉钉。
"""
from datetime import datetime, timezone

from app import db


def _utcnow():
    return datetime.now(timezone.utc)


class Guardian(db.Model):
    __tablename__ = "guardians"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False, index=True)
    name = db.Column(db.String(64), nullable=False)
    relation = db.Column(db.String(32), nullable=True)  # 父 / 母 / 其他
    dingtalk_user_id = db.Column(db.String(64), nullable=True, index=True)
    phone = db.Column(db.String(32), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    sync_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    student = db.relationship("Student", backref="guardians")
    user = db.relationship("User", backref="guardians")

    def to_dict(self):
        return {
            "id": self.id,
            "student_id": self.student_id,
            "name": self.name,
            "relation": self.relation,
            "dingtalk_user_id": self.dingtalk_user_id,
            "phone": self.phone,
            "user_id": self.user_id,
            "sync_at": self.sync_at.isoformat() if self.sync_at else None,
        }

    def __repr__(self):
        return f"<Guardian {self.name} (student {self.student_id})>"
