from datetime import datetime, timezone
from app import db


class Student(db.Model):
    __tablename__ = "students"

    id = db.Column(db.Integer, primary_key=True)
    student_no = db.Column(db.String(64), unique=True, nullable=False, index=True)
    name = db.Column(db.String(64), nullable=False)
    class_id = db.Column(db.String(64), nullable=False, index=True)
    class_name = db.Column(db.String(64))
    grade_id = db.Column(db.String(64), index=True)
    grade_name = db.Column(db.String(64))
    # Card number for consumption matching
    card_no = db.Column(db.String(64), index=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "student_no": self.student_no,
            "name": self.name,
            "class_id": self.class_id,
            "class_name": self.class_name,
            "grade_id": self.grade_id,
            "grade_name": self.grade_name,
            "card_no": self.card_no,
            "is_active": self.is_active,
        }

    def __repr__(self):
        return f"<Student {self.name} ({self.student_no})>"
