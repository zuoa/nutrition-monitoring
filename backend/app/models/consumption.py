from datetime import datetime, timezone
from app import db


class ConsumptionRecord(db.Model):
    __tablename__ = "consumption_records"
    __table_args__ = (
        db.UniqueConstraint("source_system", "source_record_id", name="uq_consumption_source_record"),
        db.Index(
            "ix_consumption_records_import_batch_time_id",
            "import_batch",
            "transaction_time",
            "id",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=True, index=True)
    student_no = db.Column(db.String(64), index=True)  # ZTK AccNum / imported student number
    card_code = db.Column(db.String(64), index=True)  # ZTK CardCode; never a student_no fallback
    student_name = db.Column(db.String(64))
    transaction_time = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    amount = db.Column(db.Numeric(8, 2), nullable=False)
    transaction_id = db.Column(db.String(128), unique=True, nullable=False, index=True)
    channel_id = db.Column(db.String(16), index=True)
    import_batch = db.Column(db.String(64))  # batch id for tracking
    source_system = db.Column(db.String(32), index=True)
    source_record_id = db.Column(db.String(128), index=True)
    source_payload = db.Column(db.JSON, default=dict)
    source_synced_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    student = db.relationship("Student", backref="consumption_records")

    def to_dict(self, *, include_source_payload: bool = False):
        data = {
            "id": self.id,
            "student_id": self.student_id,
            "student_no": self.student_no,
            "card_code": self.card_code,
            "student_name": self.student_name,
            "transaction_time": self.transaction_time.isoformat() if self.transaction_time else None,
            "amount": float(self.amount) if self.amount is not None else None,
            "transaction_id": self.transaction_id,
            "channel_id": self.channel_id,
            "import_batch": self.import_batch,
            "source_system": self.source_system,
            "source_record_id": self.source_record_id,
            "source_synced_at": self.source_synced_at.isoformat() if self.source_synced_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_source_payload:
            data["source_payload"] = self.source_payload or {}
        return data

    def __repr__(self):
        return f"<ConsumptionRecord {self.transaction_id}>"


class ConsumptionSyncState(db.Model):
    __tablename__ = "consumption_sync_states"

    id = db.Column(db.Integer, primary_key=True)
    source_system = db.Column(db.String(32), nullable=False, unique=True, index=True)
    cursor_transaction_time = db.Column(db.DateTime(timezone=True), index=True)
    cursor_source_record_id = db.Column(db.String(128))
    last_batch_id = db.Column(db.String(64))
    last_synced_at = db.Column(db.DateTime(timezone=True))
    last_success_count = db.Column(db.Integer, default=0)
    last_skipped_count = db.Column(db.Integer, default=0)
    last_error_count = db.Column(db.Integer, default=0)
    last_error = db.Column(db.Text)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = db.Column(db.DateTime(timezone=True))

    def to_dict(self):
        return {
            "id": self.id,
            "source_system": self.source_system,
            "cursor_transaction_time": self.cursor_transaction_time.isoformat() if self.cursor_transaction_time else None,
            "cursor_source_record_id": self.cursor_source_record_id,
            "last_batch_id": self.last_batch_id,
            "last_synced_at": self.last_synced_at.isoformat() if self.last_synced_at else None,
            "last_success_count": self.last_success_count or 0,
            "last_skipped_count": self.last_skipped_count or 0,
            "last_error_count": self.last_error_count or 0,
            "last_error": self.last_error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f"<ConsumptionSyncState {self.source_system}>"
