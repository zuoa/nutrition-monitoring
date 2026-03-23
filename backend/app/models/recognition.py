from datetime import datetime, timezone
from app import db


class DishRecognition(db.Model):
    __tablename__ = "dish_recognitions"

    id = db.Column(db.Integer, primary_key=True)
    image_id = db.Column(
        db.Integer, db.ForeignKey("captured_images.id"), nullable=False, index=True
    )
    dish_id = db.Column(db.Integer, db.ForeignKey("dishes.id"), nullable=True, index=True)
    dish_name_raw = db.Column(db.String(64), nullable=False)
    confidence = db.Column(db.Numeric(4, 3), nullable=False)
    is_low_confidence = db.Column(db.Boolean, default=False, nullable=False)
    is_manual = db.Column(db.Boolean, default=False)
    model_version = db.Column(db.String(32))
    raw_response = db.Column(db.JSON)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    dish = db.relationship("Dish", backref="recognitions")

    def to_dict(self):
        return {
            "id": self.id,
            "image_id": self.image_id,
            "dish_id": self.dish_id,
            "dish_name_raw": self.dish_name_raw,
            "confidence": float(self.confidence) if self.confidence is not None else None,
            "is_low_confidence": self.is_low_confidence,
            "is_manual": self.is_manual,
            "model_version": self.model_version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<DishRecognition {self.dish_name_raw} ({self.confidence})>"
