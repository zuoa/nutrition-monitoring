from datetime import datetime, timezone

from app import db


class MatchingRun(db.Model):
    """One durable, coalescing work queue entry per consumption date."""

    __tablename__ = "matching_runs"

    match_date = db.Column(db.Date, primary_key=True)
    requested = db.Column(db.Integer, nullable=False, default=0)
    completed = db.Column(db.Integer, nullable=False, default=0)
    generation = db.Column(db.Integer, nullable=False, default=0)
    phase = db.Column(db.String(20), nullable=False, default="idle")
    stage = db.Column(db.Integer, nullable=False, default=0)
    cursor = db.Column(db.Integer, nullable=False, default=0)
    snapshot = db.Column(db.JSON, nullable=True)
    assignments = db.Column(db.JSON, nullable=False, default=dict)
    notification_student_ids = db.Column(db.JSON, nullable=False, default=list)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class MatchingCandidate(db.Model):
    """Candidates for the current round; consumed only after full collection."""

    __tablename__ = "matching_candidates"
    __table_args__ = (
        db.Index("ix_matching_candidates_order", "match_date", "time_diff_seconds", "record_id", "image_id"),
    )

    match_date = db.Column(db.Date, db.ForeignKey("matching_runs.match_date", ondelete="CASCADE"), primary_key=True)
    record_id = db.Column(db.Integer, primary_key=True)
    image_id = db.Column(db.Integer, primary_key=True)
    time_diff_seconds = db.Column(db.Float, nullable=False)
