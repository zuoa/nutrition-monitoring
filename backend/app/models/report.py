import enum
from datetime import datetime, timezone
from app import db


class ReportTypeEnum(str, enum.Enum):
    personal_weekly = "personal_weekly"
    personal_monthly = "personal_monthly"
    class_weekly = "class_weekly"
    grade_monthly = "grade_monthly"
    school_monthly = "school_monthly"


class Report(db.Model):
    __tablename__ = "reports"

    id = db.Column(db.Integer, primary_key=True)
    report_type = db.Column(db.Enum(ReportTypeEnum), nullable=False, index=True)
    target_id = db.Column(db.String(64), nullable=False, index=True)  # student_id / class_id / grade_id / "school"
    period_start = db.Column(db.Date, nullable=False)
    period_end = db.Column(db.Date, nullable=False)
    content = db.Column(db.JSON, default=dict)  # full report data
    summary = db.Column(db.Text)
    push_status = db.Column(db.String(32), default="pending")  # pending / sent / failed
    pushed_at = db.Column(db.DateTime(timezone=True))
    push_retry_count = db.Column(db.Integer, default=0)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    push_logs = db.relationship("ReportPushLog", backref="report", lazy="dynamic")

    def to_dict(self, include_content=False):
        data = {
            "id": self.id,
            "report_type": self.report_type.value if self.report_type else None,
            "target_id": self.target_id,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "summary": self.summary,
            "push_status": self.push_status,
            "pushed_at": self.pushed_at.isoformat() if self.pushed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_content:
            data["content"] = self.content or {}
        return data

    def __repr__(self):
        return f"<Report {self.report_type} {self.target_id} {self.period_start}>"


class ReportPushLog(db.Model):
    __tablename__ = "report_push_logs"

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey("reports.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    status = db.Column(db.String(32))  # sent / failed
    error_message = db.Column(db.Text)
    pushed_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
