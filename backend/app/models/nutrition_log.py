from datetime import datetime, timezone
from app import db


class NutritionLog(db.Model):
    """Pre-computed daily nutrition totals per student."""
    __tablename__ = "nutrition_logs"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False, index=True)
    log_date = db.Column(db.Date, nullable=False, index=True)
    # Nutrient totals as JSON: {calories, protein, fat, carbohydrate, sodium, fiber}
    nutrient_totals = db.Column(db.JSON, default=dict)
    meal_count = db.Column(db.Integer, default=0)  # number of meals that day
    dish_ids = db.Column(db.JSON, default=list)  # list of dish ids consumed
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        db.UniqueConstraint("student_id", "log_date", name="uq_nutrition_log_student_date"),
    )

    student = db.relationship("Student", backref="nutrition_logs")

    def to_dict(self):
        return {
            "id": self.id,
            "student_id": self.student_id,
            "log_date": self.log_date.isoformat() if self.log_date else None,
            "nutrient_totals": self.nutrient_totals or {},
            "meal_count": self.meal_count,
            "dish_ids": self.dish_ids or [],
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f"<NutritionLog student={self.student_id} date={self.log_date}>"
