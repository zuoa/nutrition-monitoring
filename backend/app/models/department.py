from datetime import datetime, timezone
from app import db


class Department(db.Model):
    __tablename__ = "departments"

    id = db.Column(db.Integer, primary_key=True)
    dingtalk_dept_id = db.Column(db.String(64), unique=True, nullable=False, index=True)
    name = db.Column(db.String(128), nullable=False)
    parent_dingtalk_dept_id = db.Column(db.String(64), nullable=True, index=True)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    sync_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "dingtalk_dept_id": self.dingtalk_dept_id,
            "name": self.name,
            "parent_dingtalk_dept_id": self.parent_dingtalk_dept_id,
            "sort_order": self.sort_order,
            "is_active": self.is_active,
            "sync_at": self.sync_at.isoformat() if self.sync_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<Department {self.name} ({self.dingtalk_dept_id})>"
