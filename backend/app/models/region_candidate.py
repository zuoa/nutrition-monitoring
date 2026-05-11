import enum
import os
from datetime import datetime, timezone
from urllib.parse import quote

from flask import current_app, has_app_context

from app import db


class RegionRecognitionStatusEnum(str, enum.Enum):
    recognized = "recognized"
    low_confidence = "low_confidence"
    unrecognized = "unrecognized"


class RegionReviewStatusEnum(str, enum.Enum):
    pending = "pending"
    bound = "bound"
    ignored = "ignored"


class CapturedImageRegion(db.Model):
    __tablename__ = "captured_image_regions"

    id = db.Column(db.Integer, primary_key=True)
    image_id = db.Column(db.Integer, db.ForeignKey("captured_images.id"), nullable=False, index=True)
    region_index = db.Column(db.Integer, nullable=False)
    bbox = db.Column(db.JSON, nullable=False)
    bbox_source = db.Column(db.String(32), default="pixels", nullable=False)
    detector_source = db.Column(db.String(64))
    image_path = db.Column(db.String(512), nullable=False)
    recognition_status = db.Column(
        db.Enum(RegionRecognitionStatusEnum),
        default=RegionRecognitionStatusEnum.unrecognized,
        nullable=False,
        index=True,
    )
    suggested_dish_id = db.Column(db.Integer, db.ForeignKey("dishes.id"), nullable=True, index=True)
    suggested_dish_name = db.Column(db.String(64))
    suggested_confidence = db.Column(db.Numeric(4, 3))
    review_status = db.Column(
        db.Enum(RegionReviewStatusEnum),
        default=RegionReviewStatusEnum.pending,
        nullable=False,
        index=True,
    )
    dish_sample_image_id = db.Column(
        db.Integer,
        db.ForeignKey("dish_sample_images.id", ondelete="SET NULL"),
        nullable=True,
    )
    model_version = db.Column(db.String(64))
    raw_result = db.Column(db.JSON)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    image = db.relationship("CapturedImage", backref=db.backref("region_candidates", lazy="dynamic"))
    suggested_dish = db.relationship("Dish", foreign_keys=[suggested_dish_id])
    dish_sample_image = db.relationship("DishSampleImage", foreign_keys=[dish_sample_image_id])

    def _build_image_url(self):
        if not self.image_path:
            return None

        normalized_path = os.path.normpath(self.image_path).replace("\\", "/")
        if normalized_path.startswith(("http://", "https://", "/images/")):
            return normalized_path

        relative_path = None
        if has_app_context():
            image_root = current_app.config.get("IMAGE_STORAGE_PATH", "/data/images")
            normalized_root = os.path.normpath(image_root).replace("\\", "/")
            root_prefix = f"{normalized_root}/"
            if normalized_path.startswith(root_prefix):
                relative_path = normalized_path[len(root_prefix):]

        if relative_path is None and "/data/images/" in normalized_path:
            relative_path = normalized_path.split("/data/images/", 1)[1]

        if relative_path is None:
            return None

        encoded_path = "/".join(quote(part) for part in relative_path.split("/") if part)
        return f"/images/{encoded_path}" if encoded_path else "/images"

    def to_dict(self, *, include_source_image: bool = False):
        data = {
            "id": self.id,
            "image_id": self.image_id,
            "region_index": self.region_index,
            "bbox": self.bbox,
            "bbox_source": self.bbox_source,
            "detector_source": self.detector_source,
            "image_url": self._build_image_url(),
            "recognition_status": self.recognition_status.value if self.recognition_status else None,
            "suggested_dish_id": self.suggested_dish_id,
            "suggested_dish_name": self.suggested_dish_name,
            "suggested_confidence": float(self.suggested_confidence) if self.suggested_confidence is not None else None,
            "review_status": self.review_status.value if self.review_status else None,
            "dish_sample_image_id": self.dish_sample_image_id,
            "model_version": self.model_version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_source_image and self.image:
            data["image"] = self.image.to_dict()
        if self.suggested_dish:
            data["suggested_dish"] = {
                "id": self.suggested_dish.id,
                "name": self.suggested_dish.name,
                "category": self.suggested_dish.category.value if self.suggested_dish.category else None,
                "sample_image_count": self.suggested_dish.to_dict().get("sample_image_count"),
            }
        return data

    def __repr__(self):
        return f"<CapturedImageRegion image={self.image_id} region={self.region_index}>"
