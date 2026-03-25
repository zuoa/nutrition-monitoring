import enum
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from app import db


class RoleEnum(str, enum.Enum):
    admin = "admin"
    teacher = "teacher"
    grade_leader = "grade_leader"
    parent = "parent"
    canteen_manager = "canteen_manager"


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    dingtalk_user_id = db.Column(db.String(64), unique=True, nullable=True, index=True)
    # For password-based login
    username = db.Column(db.String(64), unique=True, nullable=True, index=True)
    password_hash = db.Column(db.String(256), nullable=True)
    name = db.Column(db.String(64), nullable=False)
    role = db.Column(db.Enum(RoleEnum), nullable=False)
    dept_id = db.Column(db.String(64))
    dept_name = db.Column(db.String(128))
    # For teachers: class ids they manage
    managed_class_ids = db.Column(db.JSON, default=list)
    # For grade leaders: grade ids
    managed_grade_ids = db.Column(db.JSON, default=list)
    # For parents: student ids
    student_ids = db.Column(db.JSON, default=list)
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

    def set_password(self, password: str):
        """Hash and set password."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        """Check password against hash."""
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {
            "id": self.id,
            "dingtalk_user_id": self.dingtalk_user_id,
            "username": self.username,
            "name": self.name,
            "role": self.role.value if self.role else None,
            "dept_id": self.dept_id,
            "dept_name": self.dept_name,
            "managed_class_ids": self.managed_class_ids or [],
            "managed_grade_ids": self.managed_grade_ids or [],
            "student_ids": self.student_ids or [],
            "is_active": self.is_active,
            "sync_at": self.sync_at.isoformat() if self.sync_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<User {self.name} ({self.role})>"
