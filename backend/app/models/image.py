import enum
import os
from datetime import datetime, timezone
from urllib.parse import quote

from flask import current_app, has_app_context

from app import db


class ImageStatusEnum(str, enum.Enum):
    pending = "pending"
    identified = "identified"
    matched = "matched"
    error = "error"


class CapturedImage(db.Model):
    __tablename__ = "captured_images"

    id = db.Column(db.Integer, primary_key=True)
    capture_date = db.Column(db.Date, nullable=False, index=True)
    channel_id = db.Column(db.String(16), nullable=False)
    captured_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    image_path = db.Column(db.String(512), nullable=False)
    status = db.Column(
        db.Enum(ImageStatusEnum),
        default=ImageStatusEnum.pending,
        nullable=False,
        index=True,
    )
    source_video = db.Column(db.String(512))
    diff_score = db.Column(db.Float)  # frame diff score for ranking
    is_candidate = db.Column(db.Boolean, default=False)  # secondary candidate
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    recognitions = db.relationship("DishRecognition", backref="image", lazy="dynamic")

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

    def to_dict(self):
        return {
            "id": self.id,
            "capture_date": self.capture_date.isoformat() if self.capture_date else None,
            "channel_id": self.channel_id,
            "captured_at": self.captured_at.isoformat() if self.captured_at else None,
            "image_path": self.image_path,
            "image_url": self._build_image_url(),
            "status": self.status.value if self.status else None,
            "source_video": self.source_video,
            "diff_score": self.diff_score,
            "is_candidate": self.is_candidate,
        }

    def __repr__(self):
        return f"<CapturedImage {self.id} at {self.captured_at}>"
