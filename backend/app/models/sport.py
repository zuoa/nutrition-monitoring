"""External sport results, linked to the roster by student number (person_id)."""

from datetime import datetime, timezone

from app import db


class SportRecord(db.Model):
    __tablename__ = "sport_records"

    id = db.Column(db.Integer, primary_key=True)
    event_key = db.Column(db.String(64), nullable=False, unique=True)
    version = db.Column(db.String(16), nullable=False)
    school_id = db.Column(db.String(128), nullable=False)
    product_type = db.Column(db.Integer, nullable=False)
    sport_type = db.Column(db.Integer, nullable=False)
    mode = db.Column(db.Integer, nullable=False)
    person_id = db.Column(db.String(64), nullable=False)
    start_time = db.Column(db.BigInteger, nullable=False)  # Original epoch milliseconds
    score = db.Column(db.Float, nullable=False)
    score_unit = db.Column(db.String(16))
    all_time = db.Column(db.Float)  # Seconds, only present for some sports
    video_file_id = db.Column(db.String(256))
    face_file_id = db.Column(db.String(256))
    raw_result = db.Column(db.JSON, nullable=False)  # Includes optional/future metrics
    received_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    # No FK: callbacks may arrive before roster sync, and unidentified persons
    # use "". A later roster import resolves the relationship without backfilling.
    student = db.relationship(
        "Student", primaryjoin="and_(foreign(SportRecord.person_id) == Student.student_no, SportRecord.person_id != '')",
        viewonly=True, uselist=False,
    )

    __table_args__ = (
        db.Index("ix_sport_records_person_start", "person_id", "start_time"),
        db.Index("ix_sport_records_school_sport_start", "school_id", "sport_type", "start_time"),
    )

    def to_dict(self, include_raw=True):
        # Postgres returns timestamptz values in the session timezone (aware);
        # SQLite tests return the naive UTC wall clock. Normalize both to UTC
        # without relabelling an already-aware wall clock.
        received = self.received_at
        if received is not None:
            received = received.astimezone(timezone.utc) if received.tzinfo else received.replace(tzinfo=timezone.utc)
        item = {
            "id": self.id, "school_id": self.school_id, "product_type": self.product_type,
            "sport_type": self.sport_type, "mode": self.mode, "person_id": self.person_id,
            "start_time": self.start_time, "score": self.score, "score_unit": self.score_unit,
            "all_time": self.all_time, "video_file_id": self.video_file_id,
            "face_file_id": self.face_file_id, "received_at": received.isoformat() if received else None,
        }
        if include_raw:
            item["raw_result"] = self.raw_result
        return item


class SportFile(db.Model):
    __tablename__ = "sport_files"

    file_id = db.Column(db.String(256), primary_key=True)
    version = db.Column(db.String(16), nullable=False)
    file_type = db.Column(db.Integer, nullable=False)
    storage_path = db.Column(db.Text, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    size_bytes = db.Column(db.BigInteger, nullable=False)
    received_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
