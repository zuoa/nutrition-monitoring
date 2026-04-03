import enum
from datetime import datetime, timezone

from app import db


class VideoSourceType(str, enum.Enum):
    nvr = "nvr"
    hikvision_camera = "hikvision_camera"


class VideoSourceStatus(str, enum.Enum):
    enabled = "enabled"
    disabled = "disabled"


class VideoSourceValidationStatus(str, enum.Enum):
    unknown = "unknown"
    success = "success"
    failed = "failed"


class VideoSource(db.Model):
    __tablename__ = "video_sources"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    source_type = db.Column(db.String(64), nullable=False, index=True)
    is_active = db.Column(db.Boolean, default=False, nullable=False, index=True)
    status = db.Column(db.String(32), default=VideoSourceStatus.enabled.value, nullable=False, index=True)
    config_json = db.Column(db.JSON, default=dict, nullable=False)
    credentials_json_encrypted = db.Column(db.Text, nullable=False, default="")
    last_validation_status = db.Column(
        db.String(32),
        default=VideoSourceValidationStatus.unknown.value,
        nullable=False,
    )
    last_validation_error = db.Column(db.Text)
    last_validated_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def to_summary_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "source_type": self.source_type,
            "is_active": self.is_active,
            "status": self.status,
            "last_validation_status": self.last_validation_status,
            "last_validation_error": self.last_validation_error,
            "last_validated_at": self.last_validated_at.isoformat() if self.last_validated_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f"<VideoSource {self.id} {self.source_type} active={self.is_active}>"
