import logging
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping

from app import db
from app.models import (
    VideoSource,
    VideoSourceStatus,
    VideoSourceType,
    VideoSourceValidationStatus,
)
from app.services.video_sources.crypto import decrypt_json_payload, encrypt_json_payload
from app.services.video_sources.factory import build_video_source_adapter
from app.services.video_sources.repository import (
    get_active_video_source,
    get_video_source,
    list_video_sources,
)
from app.services.video_sources.schemas import VideoSourceConfigError, normalize_video_source_payload

logger = logging.getLogger(__name__)


class VideoSourceManager:
    def __init__(self, config: Mapping[str, Any]):
        self.config = config
        self.secret_key = str(config.get("SECRET_KEY") or "").strip() or "dev-secret-key-change-in-production"

    def list_sources(self) -> list[dict[str, Any]]:
        return [self.serialize_summary(item) for item in list_video_sources()]

    def get_source_or_404(self, video_source_id: int) -> VideoSource:
        source = get_video_source(video_source_id)
        if source is None:
            raise VideoSourceConfigError("视频源不存在")
        return source

    def decrypt_credentials(self, source: VideoSource) -> dict[str, Any]:
        return decrypt_json_payload(source.credentials_json_encrypted, self.secret_key)

    def serialize_summary(self, source: VideoSource | Mapping[str, Any]) -> dict[str, Any]:
        if isinstance(source, VideoSource):
            return {
                **source.to_summary_dict(),
                "persisted": True,
            }
        return {
            "id": None,
            "name": source.get("name", "未持久化视频源"),
            "source_type": source.get("source_type"),
            "is_active": bool(source.get("is_active", True)),
            "status": source.get("status", VideoSourceStatus.enabled.value),
            "last_validation_status": source.get("last_validation_status", VideoSourceValidationStatus.unknown.value),
            "last_validation_error": source.get("last_validation_error"),
            "last_validated_at": source.get("last_validated_at"),
            "created_at": None,
            "updated_at": None,
            "persisted": False,
        }

    def serialize_detail(self, source: VideoSource) -> dict[str, Any]:
        config = deepcopy(source.config_json or {})
        credentials = self.decrypt_credentials(source)
        if source.source_type == VideoSourceType.nvr.value:
            config["username"] = str(credentials.get("username") or "")
            config["password_configured"] = bool(credentials.get("password"))
        else:
            credentials_by_channel = {
                str(item.get("channel_id")): item
                for item in (credentials.get("cameras") or [])
                if isinstance(item, Mapping) and str(item.get("channel_id") or "").strip()
            }
            cameras = []
            for camera in config.get("cameras", []):
                channel_id = str(camera.get("channel_id") or "").strip()
                credential = credentials_by_channel.get(channel_id, {})
                cameras.append({
                    **camera,
                    "username": str(credential.get("username") or ""),
                    "password_configured": bool(credential.get("password")),
                })
            config["cameras"] = cameras

        return {
            **self.serialize_summary(source),
            "config": config,
        }

    def create_source(self, data: Mapping[str, Any]) -> dict[str, Any]:
        payload = normalize_video_source_payload(data)
        is_active = bool(payload["is_active"]) and payload["status"] == VideoSourceStatus.enabled.value
        source = VideoSource(
            name=payload["name"],
            source_type=payload["source_type"],
            status=payload["status"],
            is_active=is_active,
            config_json=payload["config_json"],
            credentials_json_encrypted=encrypt_json_payload(payload["credentials"], self.secret_key),
        )
        if source.is_active:
            self._deactivate_others()
        db.session.add(source)
        db.session.commit()
        return self.serialize_detail(source)

    def update_source(self, source: VideoSource, data: Mapping[str, Any]) -> dict[str, Any]:
        payload = normalize_video_source_payload(
            data,
            existing_source=source,
            existing_credentials=self.decrypt_credentials(source),
        )
        is_active = bool(payload["is_active"]) and payload["status"] == VideoSourceStatus.enabled.value
        source.name = payload["name"]
        source.source_type = payload["source_type"]
        source.status = payload["status"]
        source.is_active = is_active
        source.config_json = payload["config_json"]
        source.credentials_json_encrypted = encrypt_json_payload(payload["credentials"], self.secret_key)
        if source.is_active:
            self._deactivate_others(source.id)
        db.session.commit()
        return self.serialize_detail(source)

    def activate_source(self, source: VideoSource) -> dict[str, Any]:
        if source.status != VideoSourceStatus.enabled.value:
            raise VideoSourceConfigError("只能激活 enabled 状态的视频源")
        self._deactivate_others(source.id)
        source.is_active = True
        db.session.commit()
        return self.serialize_detail(source)

    def delete_source(self, source: VideoSource):
        if source.is_active:
            raise VideoSourceConfigError("不能删除当前激活的视频源")
        db.session.delete(source)
        db.session.commit()

    def validate_source(self, source: VideoSource) -> dict[str, Any]:
        runtime_source = self.build_runtime_source(source)
        adapter = build_video_source_adapter(runtime_source)
        ok = False
        error = None
        try:
            ok = bool(adapter.is_available())
            if not ok:
                error = "视频源不可用"
        except Exception as exc:
            error = str(exc)
            logger.warning("Video source validation failed: %s", exc, exc_info=True)

        source.last_validation_status = (
            VideoSourceValidationStatus.success.value
            if ok
            else VideoSourceValidationStatus.failed.value
        )
        source.last_validation_error = error
        source.last_validated_at = datetime.now(timezone.utc)
        db.session.commit()
        return {
            "ok": ok,
            "message": "视频源连接正常" if ok else (error or "视频源验证失败"),
            "source": self.serialize_detail(source),
        }

    def get_active_runtime_source(self) -> dict[str, Any]:
        active_source = get_active_video_source()
        if active_source is not None:
            return self.build_runtime_source(active_source)
        raise VideoSourceConfigError("未配置可用的视频源，请先在后台创建并激活视频源")

    def get_active_source_summary(self) -> dict[str, Any] | None:
        active_source = get_active_video_source()
        return self.serialize_summary(active_source) if active_source is not None else None

    def list_cameras(self) -> dict[str, Any]:
        runtime_source = self.get_active_runtime_source()
        source_type = runtime_source["source_type"]
        config = runtime_source["config"]
        supports_snapshot = source_type == VideoSourceType.hikvision_camera.value

        if source_type == VideoSourceType.hikvision_camera.value:
            cameras = [
                {
                    "channel_id": camera["channel_id"],
                    "name": camera.get("name") or f"摄像头 {camera['channel_id']}",
                    "host": camera.get("host", ""),
                    "port": int(camera.get("port", 80)),
                    "supports_snapshot": True,
                }
                for camera in config.get("cameras", [])
            ]
        else:
            cameras = [
                {
                    "channel_id": channel_id,
                    "name": f"通道 {channel_id}",
                    "host": config.get("host", ""),
                    "port": int(config.get("port", 8080)),
                    "supports_snapshot": False,
                }
                for channel_id in config.get("channel_ids", [])
            ]

        return {
            "active_video_source": self.serialize_summary(runtime_source),
            "supports_snapshot": supports_snapshot,
            "cameras": cameras,
        }

    def capture_snapshot(
        self,
        *,
        channel_id: str,
        host: str = "",
        port: int | None = None,
        username: str = "",
        password: str = "",
    ) -> dict[str, Any]:
        runtime_source = self.get_runtime_source_for_capture(
            channel_id=channel_id,
            host=host,
            port=port,
            username=username,
            password=password,
        )
        if runtime_source["source_type"] != VideoSourceType.hikvision_camera.value:
            raise VideoSourceConfigError("当前视频源不支持直接抓拍")
        adapter = build_video_source_adapter(runtime_source)
        return adapter.capture_snapshot(channel_id)

    def build_runtime_source(self, source: VideoSource) -> dict[str, Any]:
        config = deepcopy(source.config_json or {})
        credentials = self.decrypt_credentials(source)
        if source.source_type == VideoSourceType.nvr.value:
            config["username"] = credentials.get("username", "")
            config["password"] = credentials.get("password", "")
        else:
            credentials_by_channel = _index_camera_credentials(credentials)
            merged_cameras = []
            for camera in config.get("cameras", []):
                channel_id = str(camera.get("channel_id") or "").strip()
                credential = credentials_by_channel.get(channel_id, {})
                merged_cameras.append({
                    **camera,
                    "username": credential.get("username", ""),
                    "password": credential.get("password", ""),
                })
            config["cameras"] = merged_cameras

        return {
            "id": source.id,
            "name": source.name,
            "source_type": source.source_type,
            "status": source.status,
            "is_active": source.is_active,
            "config": config,
            "persisted": True,
            "last_validation_status": source.last_validation_status,
            "last_validation_error": source.last_validation_error,
            "last_validated_at": source.last_validated_at.isoformat() if source.last_validated_at else None,
        }

    def get_runtime_source_for_capture(
        self,
        *,
        channel_id: str,
        host: str = "",
        port: int | None = None,
        username: str = "",
        password: str = "",
    ) -> dict[str, Any]:
        if str(host or "").strip():
            return {
                "id": None,
                "name": "临时海康抓拍源",
                "source_type": VideoSourceType.hikvision_camera.value,
                "status": VideoSourceStatus.enabled.value,
                "is_active": False,
                "config": {
                    "cameras": [{
                        "channel_id": str(channel_id or "1").strip() or "1",
                        "name": f"临时摄像头 {channel_id or '1'}",
                        "host": str(host).strip(),
                        "port": int(port or 80),
                        "username": str(username or "admin").strip() or "admin",
                        "password": str(password or "").strip(),
                    }],
                },
                "persisted": False,
            }
        return self.get_active_runtime_source()

    def _deactivate_others(self, keep_id: int | None = None):
        query = VideoSource.query.filter(VideoSource.is_active.is_(True))
        if keep_id is not None:
            query = query.filter(VideoSource.id != keep_id)
        for item in query.all():
            item.is_active = False


def _index_camera_credentials(credentials: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(credentials, Mapping):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in credentials.get("cameras") or []:
        if not isinstance(item, Mapping):
            continue
        channel_id = str(item.get("channel_id") or "").strip()
        if not channel_id:
            continue
        result[channel_id] = dict(item)
    return result
