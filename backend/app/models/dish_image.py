import enum
import os
from datetime import datetime, timezone
from urllib.parse import quote

from flask import current_app, has_app_context

from app import db


class EmbeddingStatusEnum(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    ready = "ready"
    failed = "failed"


class DishSampleImage(db.Model):
    __tablename__ = "dish_sample_images"

    id = db.Column(db.Integer, primary_key=True)
    dish_id = db.Column(db.Integer, db.ForeignKey("dishes.id"), nullable=False, index=True)
    image_path = db.Column(db.String(512), nullable=False)
    original_filename = db.Column(db.String(255))
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_cover = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    embedding_status = db.Column(
        db.Enum(EmbeddingStatusEnum),
        default=EmbeddingStatusEnum.pending,
        nullable=False,
        index=True,
    )
    embedding_model = db.Column(db.String(128))
    embedding_version = db.Column(db.String(64))
    embedding_updated_at = db.Column(db.DateTime(timezone=True))
    error_message = db.Column(db.String(255))
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    dish = db.relationship("Dish", back_populates="sample_images")

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

    def to_dict(self, *, include_internal_path: bool = False):
        data = {
            "id": self.id,
            "dish_id": self.dish_id,
            "image_url": self._build_image_url(),
            "original_filename": self.original_filename,
            "sort_order": self.sort_order,
            "is_cover": self.is_cover,
            "is_active": self.is_active,
            "embedding_status": self.embedding_status.value if self.embedding_status else None,
            "embedding_model": self.embedding_model,
            "embedding_version": self.embedding_version,
            "embedding_updated_at": self.embedding_updated_at.isoformat() if self.embedding_updated_at else None,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_internal_path:
            data["image_path"] = self.image_path
        return data

    def __repr__(self):
        return f"<DishSampleImage {self.id} dish={self.dish_id}>"
