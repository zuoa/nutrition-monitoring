from datetime import datetime, timezone
from app import db
from app.utils.recognition_geometry import bbox_to_pixels, derive_position_from_bbox, load_image_size


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
        payload = self.raw_response if isinstance(self.raw_response, dict) else {}
        bbox_source = str(payload.get("bbox_source") or "auto").strip().lower() or "auto"
        bbox = payload.get("bbox")
        position = payload.get("position", "")

        if self.image and self.image.image_path:
            try:
                image_size = load_image_size(self.image.image_path)
            except OSError:
                image_size = None
            if image_size:
                image_width, image_height = image_size
                normalized_bbox = bbox_to_pixels(
                    bbox,
                    image_width=image_width,
                    image_height=image_height,
                    bbox_source=bbox_source,
                )
                if normalized_bbox:
                    bbox = normalized_bbox
                    position = derive_position_from_bbox(
                        normalized_bbox,
                        image_width=image_width,
                        image_height=image_height,
                        bbox_source="pixels",
                    )

        return {
            "id": self.id,
            "image_id": self.image_id,
            "dish_id": self.dish_id,
            "dish_name_raw": self.dish_name_raw,
            "dish_price": float(self.dish.price) if self.dish and self.dish.price is not None else None,
            "confidence": float(self.confidence) if self.confidence is not None else None,
            "is_low_confidence": self.is_low_confidence,
            "is_manual": self.is_manual,
            "position": position,
            "bbox": bbox,
            "notes": payload.get("notes", ""),
            "model_version": self.model_version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<DishRecognition {self.dish_name_raw} ({self.confidence})>"
