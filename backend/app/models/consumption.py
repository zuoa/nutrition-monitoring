from datetime import datetime, timezone
from app import db


class ConsumptionRecord(db.Model):
    __tablename__ = "consumption_records"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=True, index=True)
    student_no = db.Column(db.String(64), index=True)  # raw from import
    student_name = db.Column(db.String(64))
    transaction_time = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    amount = db.Column(db.Numeric(8, 2), nullable=False)
    transaction_id = db.Column(db.String(128), unique=True, nullable=False, index=True)
    import_batch = db.Column(db.String(64))  # batch id for tracking
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    student = db.relationship("Student", backref="consumption_records")

    def to_dict(self):
        return {
            "id": self.id,
            "student_id": self.student_id,
            "student_no": self.student_no,
            "student_name": self.student_name,
            "transaction_time": self.transaction_time.isoformat() if self.transaction_time else None,
            "amount": float(self.amount) if self.amount is not None else None,
            "transaction_id": self.transaction_id,
            "import_batch": self.import_batch,
        }

    def __repr__(self):
        return f"<ConsumptionRecord {self.transaction_id}>"
