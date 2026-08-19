from datetime import datetime, timezone

from app import db


class VideoRecordingJob(db.Model):
    """Durable state for one recording in a multi-video synchronization run."""

    __tablename__ = "video_recording_jobs"
    __table_args__ = (
        db.UniqueConstraint("task_log_id", "filename", name="uq_video_recording_job_task_filename"),
    )

    id = db.Column(db.Integer, primary_key=True)
    task_log_id = db.Column(db.Integer, db.ForeignKey("task_logs.id", ondelete="CASCADE"), nullable=False, index=True)
    video_source_id = db.Column(db.Integer, db.ForeignKey("video_sources.id", ondelete="SET NULL"), index=True)
    channel_id = db.Column(db.String(32), nullable=False, index=True)
    filename = db.Column(db.String(512), nullable=False)
    video_path = db.Column(db.String(1024), nullable=False)
    output_dir = db.Column(db.String(1024), nullable=False)
    download_url = db.Column(db.Text, nullable=False, default="")
    status = db.Column(db.String(32), nullable=False, default="pending", index=True)
    stage = db.Column(db.String(32), nullable=False, default="queued", index=True)
    recording_start = db.Column(db.DateTime(timezone=True))
    recording_end = db.Column(db.DateTime(timezone=True))
    source_start = db.Column(db.DateTime(timezone=True))
    source_end = db.Column(db.DateTime(timezone=True))
    progress_percent = db.Column(db.Float, nullable=False, default=0.0)
    current_frame = db.Column(db.Integer)
    total_frames = db.Column(db.Integer)
    extracted_count = db.Column(db.Integer, nullable=False, default=0)
    frame_count = db.Column(db.Integer, nullable=False, default=0)
    download_attempt_count = db.Column(db.Integer, nullable=False, default=0)
    extract_attempt_count = db.Column(db.Integer, nullable=False, default=0)
    download_task_id = db.Column(db.String(64), index=True)
    extract_task_id = db.Column(db.String(64), index=True)
    dispatch_attempt_count = db.Column(db.Integer, nullable=False, default=0)
    recovery_count = db.Column(db.Integer, nullable=False, default=0)
    published_at = db.Column(db.DateTime(timezone=True))
    next_dispatch_at = db.Column(db.DateTime(timezone=True), index=True)
    lease_expires_at = db.Column(db.DateTime(timezone=True), index=True)
    extraction_strategy = db.Column(db.String(64))
    fallback_used = db.Column(db.Boolean, nullable=False, default=False)
    error_code = db.Column(db.String(64))
    error_message = db.Column(db.Text)
    details = db.Column(db.JSON, nullable=False, default=dict)
    queued_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    download_started_at = db.Column(db.DateTime(timezone=True))
    download_finished_at = db.Column(db.DateTime(timezone=True))
    extract_started_at = db.Column(db.DateTime(timezone=True))
    extract_finished_at = db.Column(db.DateTime(timezone=True))
    last_progress_at = db.Column(db.DateTime(timezone=True), index=True)
    finished_at = db.Column(db.DateTime(timezone=True))

    task_log = db.relationship("TaskLog", backref=db.backref("video_recording_jobs", lazy="dynamic", cascade="all, delete-orphan"))

    def to_recording_meta(self) -> dict:
        details = dict(self.details or {})
        return {
            **details,
            "recording_job_id": self.id,
            "channel_id": self.channel_id,
            "filename": self.filename,
            "relative_path": details.get("relative_path"),
            "recording_start": self.recording_start.isoformat() if self.recording_start else None,
            "recording_end": self.recording_end.isoformat() if self.recording_end else None,
            "source_start": self.source_start.isoformat() if self.source_start else None,
            "source_end": self.source_end.isoformat() if self.source_end else None,
            "download_status": self.status,
            "stage": self.stage,
            "progress_percent": round(float(self.progress_percent or 0.0), 1),
            "current_frame": self.current_frame,
            "total_frames": self.total_frames,
            "extracted_count": self.extracted_count or 0,
            "frame_count": self.frame_count or 0,
            "dispatch_attempt_count": self.dispatch_attempt_count or 0,
            "recovery_count": self.recovery_count or 0,
            "fallback_used": bool(self.fallback_used),
            "extract_strategy": self.extraction_strategy,
            "error": self.error_message,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "next_dispatch_at": self.next_dispatch_at.isoformat() if self.next_dispatch_at else None,
            "lease_expires_at": self.lease_expires_at.isoformat() if self.lease_expires_at else None,
            "last_progress_at": self.last_progress_at.isoformat() if self.last_progress_at else None,
            "extract_finished_at": self.extract_finished_at.isoformat() if self.extract_finished_at else None,
        }
