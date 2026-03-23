import enum
from datetime import datetime, timezone
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

    def to_dict(self):
        return {
            "id": self.id,
            "capture_date": self.capture_date.isoformat() if self.capture_date else None,
            "channel_id": self.channel_id,
            "captured_at": self.captured_at.isoformat() if self.captured_at else None,
            "image_path": self.image_path,
            "status": self.status.value if self.status else None,
            "source_video": self.source_video,
            "diff_score": self.diff_score,
            "is_candidate": self.is_candidate,
        }

    def __repr__(self):
        return f"<CapturedImage {self.id} at {self.captured_at}>"
