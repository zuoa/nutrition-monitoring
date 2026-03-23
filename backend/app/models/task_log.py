from datetime import datetime, timezone
from app import db


class TaskLog(db.Model):
    __tablename__ = "task_logs"

    id = db.Column(db.Integer, primary_key=True)
    task_type = db.Column(db.String(64), nullable=False, index=True)  # nvr_download / ai_recognition / report_gen
    task_date = db.Column(db.Date, index=True)
    status = db.Column(db.String(32), default="running")  # running / success / failed / partial
    total_count = db.Column(db.Integer, default=0)
    success_count = db.Column(db.Integer, default=0)
    low_confidence_count = db.Column(db.Integer, default=0)
    error_count = db.Column(db.Integer, default=0)
    error_message = db.Column(db.Text)
    meta = db.Column(db.JSON, default=dict)
    started_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    finished_at = db.Column(db.DateTime(timezone=True))

    def to_dict(self):
        return {
            "id": self.id,
            "task_type": self.task_type,
            "task_date": self.task_date.isoformat() if self.task_date else None,
            "status": self.status,
            "total_count": self.total_count,
            "success_count": self.success_count,
            "low_confidence_count": self.low_confidence_count,
            "error_count": self.error_count,
            "error_message": self.error_message,
            "meta": self.meta or {},
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }

    def __repr__(self):
        return f"<TaskLog {self.task_type} {self.task_date} ({self.status})>"
