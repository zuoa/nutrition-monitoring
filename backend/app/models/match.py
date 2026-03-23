import enum
from datetime import datetime, timezone
from app import db


class MatchStatusEnum(str, enum.Enum):
    matched = "matched"
    time_matched_only = "time_matched_only"
    unmatched_image = "unmatched_image"
    unmatched_record = "unmatched_record"
    confirmed = "confirmed"  # manually confirmed


class MatchResult(db.Model):
    __tablename__ = "match_results"

    id = db.Column(db.Integer, primary_key=True)
    consumption_record_id = db.Column(
        db.Integer, db.ForeignKey("consumption_records.id"), nullable=True, index=True
    )
    image_id = db.Column(
        db.Integer, db.ForeignKey("captured_images.id"), nullable=True, index=True
    )
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=True, index=True)
    status = db.Column(db.Enum(MatchStatusEnum), nullable=False, index=True)
    time_diff_seconds = db.Column(db.Float)
    price_diff = db.Column(db.Float)
    is_manual = db.Column(db.Boolean, default=False)
    confirmed_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    confirmed_at = db.Column(db.DateTime(timezone=True))
    match_date = db.Column(db.Date, index=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    consumption_record = db.relationship("ConsumptionRecord", backref="match_result", uselist=False)
    image = db.relationship("CapturedImage", backref="match_results")
    student = db.relationship("Student", backref="match_results")

    def to_dict(self):
        return {
            "id": self.id,
            "consumption_record_id": self.consumption_record_id,
            "image_id": self.image_id,
            "student_id": self.student_id,
            "status": self.status.value if self.status else None,
            "time_diff_seconds": self.time_diff_seconds,
            "price_diff": self.price_diff,
            "is_manual": self.is_manual,
            "match_date": self.match_date.isoformat() if self.match_date else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<MatchResult {self.id} ({self.status})>"
