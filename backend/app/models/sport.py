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


class SportFile(db.Model):
    __tablename__ = "sport_files"

    file_id = db.Column(db.String(256), primary_key=True)
    version = db.Column(db.String(16), nullable=False)
    file_type = db.Column(db.Integer, nullable=False)
    storage_path = db.Column(db.Text, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    size_bytes = db.Column(db.BigInteger, nullable=False)
    received_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
