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
from app.services.hikvision_camera import HikvisionCameraService
from app.services.nvr import NVRService
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
        config.pop("meal_windows", None)
        credentials = self.decrypt_credentials(source)
        if source.source_type == VideoSourceType.nvr.value:
            config["username"] = str(credentials.get("username") or "")
            config["password_configured"] = bool(credentials.get("password"))
            channel_ids = _coerce_channel_ids(config.get("channel_ids"))
            config["channel_ids"] = channel_ids
            config["selected_channel_ids"] = channel_ids
            config["channels"] = [
                {
                    "channel_id": item["channel_id"],
                    "name": item.get("name") or f"通道 {item['channel_id']}",
                    "selected": item["channel_id"] in set(channel_ids),
                    "roi_region": _normalize_roi_region(item.get("roi_region")),
                    "location_alias": _normalize_location_alias(item.get("location_alias")),
                }
                for item in _nvr_channels_from_config(config)
            ]
        else:
            shared_credentials = _resolve_hikvision_shared_credentials(credentials)
            config["username"] = shared_credentials["username"]
            config["password_configured"] = bool(shared_credentials["password"])
            config["host"] = str(config.get("host") or _first_camera_value(config, "host") or "")
            config["port"] = int(config.get("port") or _first_camera_value(config, "port") or 80)
            config["selected_channel_ids"] = _normalize_selected_channel_ids(config)
            config["channels"] = [
                {
                    "channel_id": str(camera.get("channel_id") or "").strip(),
                    "name": camera.get("name") or f"摄像头 {camera.get('channel_id')}",
                    "selected": str(camera.get("channel_id") or "").strip() in set(config["selected_channel_ids"]),
                    "roi_region": _normalize_roi_region(camera.get("roi_region")),
                    "location_alias": _normalize_location_alias(camera.get("location_alias")),
                }
                for camera in config.get("cameras", [])
                if str(camera.get("channel_id") or "").strip()
            ]

        return {
            **self.serialize_summary(source),
            "config": config,
        }

    def create_source(self, data: Mapping[str, Any]) -> dict[str, Any]:
        payload = normalize_video_source_payload(self._prepare_video_source_payload(data))
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
            self._prepare_video_source_payload(data, existing_source=source),
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
        adapter = build_video_source_adapter(runtime_source, app_config=self.config)
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
        supports_snapshot = source_type in {VideoSourceType.hikvision_camera.value, VideoSourceType.nvr.value}

        if source_type == VideoSourceType.hikvision_camera.value:
            cameras = [
                {
                    "channel_id": camera["channel_id"],
                    "name": camera.get("name") or f"摄像头 {camera['channel_id']}",
                    "host": camera.get("host", ""),
                    "port": int(camera.get("port", 80)),
                    "supports_snapshot": True,
                    "location_alias": _normalize_location_alias(camera.get("location_alias")),
                }
                for camera in config.get("cameras", [])
            ]
        else:
            channel_aliases = config.get("channel_location_aliases") if isinstance(config.get("channel_location_aliases"), Mapping) else {}
            cameras = [
                {
                    "channel_id": channel_id,
                    "name": f"通道 {channel_id}",
                    "host": config.get("host", ""),
                    "port": int(config.get("port", 8080)),
                    "supports_snapshot": True,
                    "location_alias": _normalize_location_alias(channel_aliases.get(str(channel_id))),
                }
                for channel_id in config.get("channel_ids", [])
            ]

        return {
            "active_video_source": self.serialize_summary(runtime_source),
            "supports_snapshot": supports_snapshot,
            "cameras": cameras,
        }

    def list_source_channels(self, source: VideoSource) -> dict[str, Any]:
        channels = _channels_from_source_config(source.source_type, source.config_json or {})
        return {
            "source": self.serialize_summary(source),
            "supports_snapshot": source.source_type in {VideoSourceType.hikvision_camera.value, VideoSourceType.nvr.value},
            "channels": channels,
        }

    def capture_source_snapshot(self, source: VideoSource, channel_id: str) -> dict[str, Any]:
        normalized_channel_id = str(channel_id or "").strip()
        if not normalized_channel_id:
            raise VideoSourceConfigError("channel_id 不能为空")
        if normalized_channel_id not in _channel_ids_from_source_config(source.source_type, source.config_json or {}):
            raise VideoSourceConfigError(f"视频源未配置 channel_id={normalized_channel_id} 的通道")
        runtime_source = self.build_runtime_source(source)
        adapter = build_video_source_adapter(runtime_source, app_config=self.config)
        if not hasattr(adapter, "capture_snapshot"):
            raise VideoSourceConfigError("当前视频源不支持抓拍预览")
        return adapter.capture_snapshot(normalized_channel_id)

    def get_plugin_preview_config(self, source: VideoSource, channel_id: str) -> dict[str, Any]:
        normalized_channel_id = str(channel_id or "").strip()
        if not normalized_channel_id:
            raise VideoSourceConfigError("channel_id 不能为空")
        if normalized_channel_id not in _channel_ids_from_source_config(source.source_type, source.config_json or {}):
            raise VideoSourceConfigError(f"视频源未配置 channel_id={normalized_channel_id} 的通道")

        config = deepcopy(source.config_json or {})
        credentials = self.decrypt_credentials(source)
        if source.source_type == VideoSourceType.hikvision_camera.value:
            camera = next(
                (
                    item
                    for item in config.get("cameras", [])
                    if isinstance(item, Mapping) and str(item.get("channel_id") or "").strip() == normalized_channel_id
                ),
                None,
            )
            if not isinstance(camera, Mapping):
                raise VideoSourceConfigError(f"视频源未配置 channel_id={normalized_channel_id} 的通道")
            shared_credentials = _resolve_hikvision_shared_credentials(credentials)
            host = str(camera.get("host") or config.get("host") or "").strip()
            port = int(camera.get("port") or config.get("port") or 80)
            rtsp_port = int(camera.get("rtsp_port") or config.get("rtsp_port") or 554)
            username = shared_credentials["username"]
            password = shared_credentials["password"]
        else:
            host = str(config.get("host") or "").strip()
            port = int(config.get("port") or 8080)
            rtsp_port = int(config.get("rtsp_port") or 554)
            username = str(credentials.get("username") or "").strip()
            password = str(credentials.get("password") or "").strip()

        if not host:
            raise VideoSourceConfigError("实时预览缺少设备地址")
        if not username or not password:
            raise VideoSourceConfigError("实时预览缺少设备用户名或密码")

        return {
            "host": host,
            "port": port,
            "rtsp_port": rtsp_port,
            "username": username,
            "password": password,
            "channel_id": normalized_channel_id,
            "source_type": source.source_type,
            "protocol": 1,
            "stream_type": 1,
        }

    def update_channel_roi(
        self,
        source: VideoSource,
        channel_id: str,
        roi_region: Mapping[str, Any] | None,
        *,
        image_width: int | None = None,
        image_height: int | None = None,
    ) -> dict[str, Any]:
        normalized_channel_id = str(channel_id or "").strip()
        if not normalized_channel_id:
            raise VideoSourceConfigError("channel_id 不能为空")
        if normalized_channel_id not in _channel_ids_from_source_config(source.source_type, source.config_json or {}):
            raise VideoSourceConfigError(f"视频源未配置 channel_id={normalized_channel_id} 的通道")

        normalized_roi = _validate_roi_region(
            roi_region,
            image_width=image_width,
            image_height=image_height,
        )
        config = deepcopy(source.config_json or {})

        if source.source_type == VideoSourceType.hikvision_camera.value:
            updated = False
            cameras = config.get("cameras")
            if not isinstance(cameras, list):
                cameras = []
            next_cameras = []
            for camera in cameras:
                if not isinstance(camera, Mapping):
                    next_cameras.append(camera)
                    continue
                next_camera = dict(camera)
                if str(next_camera.get("channel_id") or "").strip() == normalized_channel_id:
                    updated = True
                    if normalized_roi:
                        next_camera["roi_region"] = normalized_roi
                    else:
                        next_camera.pop("roi_region", None)
                next_cameras.append(next_camera)
            if not updated:
                raise VideoSourceConfigError(f"视频源未配置 channel_id={normalized_channel_id} 的通道")
            config["cameras"] = next_cameras
        else:
            channel_rois = config.get("channel_rois")
            if not isinstance(channel_rois, Mapping):
                channel_rois = {}
            channel_rois = dict(channel_rois)
            if normalized_roi:
                channel_rois[normalized_channel_id] = normalized_roi
            else:
                channel_rois.pop(normalized_channel_id, None)
            config["channel_rois"] = channel_rois

        source.config_json = config
        db.session.commit()
        channel = next(
            (
                item
                for item in _channels_from_source_config(source.source_type, source.config_json or {})
                if item["channel_id"] == normalized_channel_id
            ),
            None,
        )
        return {
            "source": self.serialize_summary(source),
            "channel": channel,
        }

    def update_channel_location_alias(
        self,
        source: VideoSource,
        channel_id: str,
        location_alias: Any,
    ) -> dict[str, Any]:
        normalized_channel_id = str(channel_id or "").strip()
        if not normalized_channel_id:
            raise VideoSourceConfigError("channel_id 不能为空")
        if normalized_channel_id not in _channel_ids_from_source_config(source.source_type, source.config_json or {}):
            raise VideoSourceConfigError(f"视频源未配置 channel_id={normalized_channel_id} 的通道")

        normalized_alias = _normalize_location_alias(location_alias)
        config = deepcopy(source.config_json or {})

        if source.source_type == VideoSourceType.hikvision_camera.value:
            cameras = config.get("cameras")
            if not isinstance(cameras, list):
                cameras = []
            updated = False
            next_cameras = []
            for camera in cameras:
                if not isinstance(camera, Mapping):
                    next_cameras.append(camera)
                    continue
                next_camera = dict(camera)
                if str(next_camera.get("channel_id") or "").strip() == normalized_channel_id:
                    updated = True
                    if normalized_alias:
                        next_camera["location_alias"] = normalized_alias
                    else:
                        next_camera.pop("location_alias", None)
                next_cameras.append(next_camera)
            if not updated:
                raise VideoSourceConfigError(f"视频源未配置 channel_id={normalized_channel_id} 的通道")
            config["cameras"] = next_cameras
        else:
            channel_aliases = config.get("channel_location_aliases")
            if not isinstance(channel_aliases, Mapping):
                channel_aliases = {}
            channel_aliases = dict(channel_aliases)
            if normalized_alias:
                channel_aliases[normalized_channel_id] = normalized_alias
            else:
                channel_aliases.pop(normalized_channel_id, None)
            config["channel_location_aliases"] = channel_aliases

        source.config_json = config
        db.session.commit()
        channel = next(
            (
                item
                for item in _channels_from_source_config(source.source_type, source.config_json or {})
                if item["channel_id"] == normalized_channel_id
            ),
            None,
        )
        return {
            "source": self.serialize_summary(source),
            "channel": channel,
        }

    def capture_snapshot(
        self,
        *,
        channel_id: str = "",
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
        adapter = build_video_source_adapter(runtime_source, app_config=self.config)
        if not hasattr(adapter, "capture_snapshot"):
            raise VideoSourceConfigError("当前视频源不支持直接抓拍")
        return adapter.capture_snapshot(channel_id or None)

    def discover_hikvision_device(
        self,
        data: Mapping[str, Any],
        *,
        existing_source: VideoSource | None = None,
    ) -> dict[str, Any]:
        if not isinstance(data, Mapping):
            raise VideoSourceConfigError("请求体必须是对象")

        credentials = self.decrypt_credentials(existing_source) if existing_source is not None else {}
        base_config = deepcopy(getattr(existing_source, "config_json", {}) or {})
        config = data.get("config") if isinstance(data.get("config"), Mapping) else data
        if not isinstance(config, Mapping):
            raise VideoSourceConfigError("config 必须是对象")

        host = str(config.get("host") or base_config.get("host") or _first_camera_value(base_config, "host") or "").strip()
        if not host:
            raise VideoSourceConfigError("host 不能为空")

        port = int(config.get("port") or base_config.get("port") or _first_camera_value(base_config, "port") or 80)
        shared_credentials = _resolve_hikvision_shared_credentials(credentials)
        username = str(config.get("username") or shared_credentials["username"] or "admin").strip() or "admin"
        password = str(config.get("password") or shared_credentials["password"] or "").strip()
        if not password:
            raise VideoSourceConfigError("password 不能为空")

        selected_channel_ids = data.get("selected_channel_ids")
        if selected_channel_ids is None:
            selected_channel_ids = config.get("selected_channel_ids")
        if selected_channel_ids is None:
            selected_channel_ids = base_config.get("selected_channel_ids")

        try:
            discovered = HikvisionCameraService.discover_device(
                host=host,
                port=port,
                username=username,
                password=password,
            )
        except ValueError as exc:
            raise VideoSourceConfigError(str(exc)) from exc

        channels = discovered["channels"]
        normalized_selected_channel_ids = _pick_hikvision_channels(channels, selected_channel_ids)
        cameras = [
            {
                "channel_id": channel["channel_id"],
                "name": channel["name"],
                "host": discovered["host"],
                "port": discovered["port"],
            }
            for channel in channels
            if channel["channel_id"] in normalized_selected_channel_ids
        ]
        if not cameras:
            raise VideoSourceConfigError("未找到可用的海康通道")

        return {
            "config": {
                "host": discovered["host"],
                "port": discovered["port"],
                "device_name": discovered["device_info"].get("device_name", ""),
                "device_model": discovered["device_info"].get("model", ""),
                "device_serial_number": discovered["device_info"].get("serial_number", ""),
                "selected_channel_ids": normalized_selected_channel_ids,
                "cameras": cameras,
                "username": username,
                "password": password,
            },
            "device": discovered["device_info"],
            "channels": [
                {
                    **channel,
                    "selected": channel["channel_id"] in normalized_selected_channel_ids,
                }
                for channel in channels
            ],
            "selected_channel_ids": normalized_selected_channel_ids,
            "username": username,
            "password_configured": bool(password),
        }

    def discover_nvr_device(
        self,
        data: Mapping[str, Any],
        *,
        existing_source: VideoSource | None = None,
    ) -> dict[str, Any]:
        if not isinstance(data, Mapping):
            raise VideoSourceConfigError("请求体必须是对象")

        credentials = self.decrypt_credentials(existing_source) if existing_source is not None else {}
        base_config = deepcopy(getattr(existing_source, "config_json", {}) or {})
        config = data.get("config") if isinstance(data.get("config"), Mapping) else data
        if not isinstance(config, Mapping):
            raise VideoSourceConfigError("config 必须是对象")

        host = str(config.get("host") or base_config.get("host") or "").strip()
        if not host:
            raise VideoSourceConfigError("host 不能为空")
        port = int(config.get("port") or base_config.get("port") or 8080)
        username = str(config.get("username") or credentials.get("username") or "admin").strip() or "admin"
        password = str(config.get("password") or credentials.get("password") or "").strip()
        if not password:
            raise VideoSourceConfigError("password 不能为空")

        try:
            channels = NVRService({
                "NVR_HOST": host,
                "NVR_PORT": port,
                "NVR_USERNAME": username,
                "NVR_PASSWORD": password,
            }).list_channels()
        except ValueError as exc:
            raise VideoSourceConfigError(str(exc)) from exc

        normalized_channels = _normalize_discovered_channels(channels)
        selected_channel_ids = data.get("selected_channel_ids")
        if selected_channel_ids is None:
            selected_channel_ids = config.get("selected_channel_ids")
        if selected_channel_ids is None:
            selected_channel_ids = base_config.get("selected_channel_ids") or base_config.get("channel_ids")
        normalized_selected_channel_ids = _pick_available_channels(normalized_channels, selected_channel_ids)
        if not normalized_selected_channel_ids:
            raise VideoSourceConfigError("未找到可用的 NVR 通道")

        selected_set = set(normalized_selected_channel_ids)
        config_channels = [
            {
                "channel_id": channel["channel_id"],
                "name": channel["name"],
                **({"stream_id": channel["stream_id"]} if channel.get("stream_id") else {}),
            }
            for channel in normalized_channels
            if channel["channel_id"] in selected_set
        ]

        return {
            "config": {
                "host": host,
                "port": port,
                "channel_ids": normalized_selected_channel_ids,
                "selected_channel_ids": normalized_selected_channel_ids,
                "channels": config_channels,
                "username": username,
                "password": password,
            },
            "channels": [
                {
                    **channel,
                    "selected": channel["channel_id"] in selected_set,
                }
                for channel in normalized_channels
            ],
            "selected_channel_ids": normalized_selected_channel_ids,
            "username": username,
            "password_configured": bool(password),
        }

    def build_runtime_source(self, source: VideoSource) -> dict[str, Any]:
        config = deepcopy(source.config_json or {})
        config.pop("meal_windows", None)
        credentials = self.decrypt_credentials(source)
        if source.source_type == VideoSourceType.nvr.value:
            config["username"] = credentials.get("username", "")
            config["password"] = credentials.get("password", "")
        else:
            shared_credentials = _resolve_hikvision_shared_credentials(credentials)
            merged_cameras = []
            for camera in config.get("cameras", []):
                merged_cameras.append({
                    **camera,
                    "username": shared_credentials["username"],
                    "password": shared_credentials["password"],
                })
            config["cameras"] = merged_cameras
            config["host"] = str(config.get("host") or _first_camera_value(config, "host") or "")
            config["port"] = int(config.get("port") or _first_camera_value(config, "port") or 80)
            config["selected_channel_ids"] = _normalize_selected_channel_ids(config)
            config["username"] = shared_credentials["username"]
            config["password"] = shared_credentials["password"]

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
        channel_id: str = "",
        host: str = "",
        port: int | None = None,
        username: str = "",
        password: str = "",
    ) -> dict[str, Any]:
        if str(host or "").strip():
            discovered = self.discover_hikvision_device({
                "host": str(host).strip(),
                "port": int(port or 80),
                "username": str(username or "admin").strip() or "admin",
                "password": str(password or "").strip(),
                "selected_channel_ids": [str(channel_id).strip()] if str(channel_id or "").strip() else None,
            })
            return {
                "id": None,
                "name": "临时海康抓拍源",
                "source_type": VideoSourceType.hikvision_camera.value,
                "status": VideoSourceStatus.enabled.value,
                "is_active": False,
                "config": discovered["config"],
                "persisted": False,
            }
        return self.get_active_runtime_source()

    def _deactivate_others(self, keep_id: int | None = None):
        query = VideoSource.query.filter(VideoSource.is_active.is_(True))
        if keep_id is not None:
            query = query.filter(VideoSource.id != keep_id)
        for item in query.all():
            item.is_active = False

    def _prepare_video_source_payload(
        self,
        data: Mapping[str, Any],
        *,
        existing_source: VideoSource | None = None,
    ) -> Mapping[str, Any]:
        if not isinstance(data, Mapping):
            return data
        source_type = str(data.get("source_type") or getattr(existing_source, "source_type", "") or "").strip()
        if source_type == VideoSourceType.nvr.value:
            config = data.get("config")
            if not isinstance(config, Mapping):
                return data
            if _coerce_channel_ids(config.get("channel_ids")):
                return data
            discovered = self.discover_nvr_device(data, existing_source=existing_source)
            return {
                **data,
                "config": {
                    **config,
                    **discovered["config"],
                },
            }

        if source_type != VideoSourceType.hikvision_camera.value:
            return data

        config = data.get("config")
        if not isinstance(config, Mapping):
            return data

        if isinstance(config.get("cameras"), list) and config.get("host") in (None, "") and config.get("username") in (None, ""):
            first_camera = next((item for item in config["cameras"] if isinstance(item, Mapping)), {})
            legacy_config = {
                **data,
                "config": {
                    **config,
                    "host": config.get("host") or first_camera.get("host"),
                    "port": config.get("port") or first_camera.get("port") or 80,
                    "username": config.get("username") or first_camera.get("username"),
                    "password": config.get("password") or first_camera.get("password"),
                    "selected_channel_ids": config.get("selected_channel_ids")
                    or [
                        str(item.get("channel_id") or "").strip()
                        for item in config["cameras"]
                        if isinstance(item, Mapping) and str(item.get("channel_id") or "").strip()
                    ],
                },
            }
            return legacy_config

        discovered = self.discover_hikvision_device(data, existing_source=existing_source)
        return {
            **data,
            "config": discovered["config"],
        }


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


def _resolve_hikvision_shared_credentials(credentials: Mapping[str, Any] | None) -> dict[str, str]:
    if not isinstance(credentials, Mapping):
        return {"username": "", "password": ""}
    username = str(credentials.get("username") or "").strip()
    password = str(credentials.get("password") or "").strip()
    if username or password:
        return {"username": username, "password": password}

    by_channel = _index_camera_credentials(credentials)
    first = next(iter(by_channel.values()), {})
    return {
        "username": str(first.get("username") or "").strip(),
        "password": str(first.get("password") or "").strip(),
    }


def _first_camera_value(config: Mapping[str, Any], field: str) -> Any:
    cameras = config.get("cameras")
    if not isinstance(cameras, list):
        return None
    for item in cameras:
        if isinstance(item, Mapping) and item.get(field) not in (None, ""):
            return item.get(field)
    return None


def _coerce_channel_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _normalize_selected_channel_ids(config: Mapping[str, Any]) -> list[str]:
    selected = _coerce_channel_ids(config.get("selected_channel_ids"))
    if selected:
        return selected
    cameras = config.get("cameras")
    if not isinstance(cameras, list):
        return []
    return [
        str(item.get("channel_id") or "").strip()
        for item in cameras
        if isinstance(item, Mapping) and str(item.get("channel_id") or "").strip()
    ]


def _pick_hikvision_channels(channels: list[dict[str, str]], selected_channel_ids: Any) -> list[str]:
    return _pick_available_channels(channels, selected_channel_ids, default_first=True)


def _pick_available_channels(channels: list[dict[str, Any]], selected_channel_ids: Any, *, default_first: bool = False) -> list[str]:
    available = [str(item.get("channel_id") or "").strip() for item in channels if str(item.get("channel_id") or "").strip()]
    requested = [item for item in _coerce_channel_ids(selected_channel_ids) if item in available]
    if requested:
        return requested
    return available[:1] if default_first and available else available


def _normalize_discovered_channels(channels: Any) -> list[dict[str, str]]:
    if not isinstance(channels, list):
        return []
    normalized = []
    seen = set()
    for item in channels:
        if not isinstance(item, Mapping):
            item = {"channel_id": item}
        channel_id = str(item.get("channel_id") or item.get("id") or item.get("channel") or "").strip()
        if not channel_id or channel_id in seen:
            continue
        seen.add(channel_id)
        normalized.append({
            "channel_id": channel_id,
            "name": str(item.get("name") or item.get("label") or "").strip() or f"通道 {channel_id}",
            **({"stream_id": str(item.get("stream_id") or "").strip()} if str(item.get("stream_id") or "").strip() else {}),
        })
    return normalized


def _nvr_channels_from_config(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    configured_channels = config.get("channels")
    if isinstance(configured_channels, list) and configured_channels:
        channel_rois = config.get("channel_rois") if isinstance(config.get("channel_rois"), Mapping) else {}
        channel_aliases = config.get("channel_location_aliases") if isinstance(config.get("channel_location_aliases"), Mapping) else {}
        result = []
        for item in configured_channels:
            if not isinstance(item, Mapping):
                continue
            channel_id = str(item.get("channel_id") or "").strip()
            if not channel_id:
                continue
            result.append({
                **item,
                "channel_id": channel_id,
                "name": item.get("name") or f"通道 {channel_id}",
                "roi_region": item.get("roi_region") or channel_rois.get(channel_id),
                "location_alias": item.get("location_alias") if "location_alias" in item else channel_aliases.get(channel_id),
            })
        if result:
            return result
    return [
        {
            "channel_id": channel_id,
            "name": f"通道 {channel_id}",
        }
        for channel_id in _coerce_channel_ids(config.get("channel_ids"))
    ]


def _normalize_roi_region(value: Any) -> dict[str, int] | None:
    if value in (None, "") or not isinstance(value, Mapping):
        return None
    try:
        x = int(round(float(value.get("x"))))
        y = int(round(float(value.get("y"))))
        w = int(round(float(value.get("w"))))
        h = int(round(float(value.get("h"))))
    except (TypeError, ValueError):
        return None
    if x < 0 or y < 0 or w <= 0 or h <= 0:
        return None
    return {"x": x, "y": y, "w": w, "h": h}


def _normalize_location_alias(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _validate_roi_region(
    value: Mapping[str, Any] | None,
    *,
    image_width: int | None = None,
    image_height: int | None = None,
) -> dict[str, int] | None:
    if value in (None, ""):
        return None
    roi_region = _normalize_roi_region(value)
    if not roi_region:
        raise VideoSourceConfigError("roi_region 必须包含非负 x/y 和正数 w/h")

    if image_width is not None:
        try:
            normalized_width = int(image_width)
        except (TypeError, ValueError):
            raise VideoSourceConfigError("image_width 必须是整数")
        if normalized_width <= 0:
            raise VideoSourceConfigError("image_width 必须大于 0")
        if roi_region["x"] + roi_region["w"] > normalized_width:
            raise VideoSourceConfigError("roi_region 超出图片宽度")

    if image_height is not None:
        try:
            normalized_height = int(image_height)
        except (TypeError, ValueError):
            raise VideoSourceConfigError("image_height 必须是整数")
        if normalized_height <= 0:
            raise VideoSourceConfigError("image_height 必须大于 0")
        if roi_region["y"] + roi_region["h"] > normalized_height:
            raise VideoSourceConfigError("roi_region 超出图片高度")

    return roi_region


def _channel_ids_from_source_config(source_type: str, config: Mapping[str, Any]) -> set[str]:
    return {
        str(item.get("channel_id") if isinstance(item, Mapping) else item or "").strip()
        for item in (
            config.get("cameras", [])
            if source_type == VideoSourceType.hikvision_camera.value
            else config.get("channel_ids", [])
        )
        if str(item.get("channel_id") if isinstance(item, Mapping) else item or "").strip()
    }


def _channels_from_source_config(source_type: str, config: Mapping[str, Any]) -> list[dict[str, Any]]:
    if source_type == VideoSourceType.hikvision_camera.value:
        return [
            {
                "channel_id": str(camera.get("channel_id") or "").strip(),
                "name": camera.get("name") or f"摄像头 {camera.get('channel_id')}",
                "host": camera.get("host", ""),
                "port": int(camera.get("port", 80)),
                "supports_snapshot": True,
                "roi_region": _normalize_roi_region(camera.get("roi_region")),
                "location_alias": _normalize_location_alias(camera.get("location_alias")),
            }
            for camera in config.get("cameras", [])
            if isinstance(camera, Mapping) and str(camera.get("channel_id") or "").strip()
        ]

    channel_rois = config.get("channel_rois") if isinstance(config.get("channel_rois"), Mapping) else {}
    channel_aliases = config.get("channel_location_aliases") if isinstance(config.get("channel_location_aliases"), Mapping) else {}
    return [
        {
            "channel_id": str(channel.get("channel_id") or "").strip(),
            "name": channel.get("name") or f"通道 {channel.get('channel_id')}",
            "host": config.get("host", ""),
            "port": int(config.get("port", 8080)),
            "supports_snapshot": True,
            "roi_region": _normalize_roi_region(channel.get("roi_region") or channel_rois.get(str(channel.get("channel_id") or "").strip())),
            "location_alias": _normalize_location_alias(
                channel.get("location_alias")
                if "location_alias" in channel
                else channel_aliases.get(str(channel.get("channel_id") or "").strip())
            ),
        }
        for channel in _nvr_channels_from_config(config)
        if str(channel.get("channel_id") or "").strip()
    ]


def build_channel_display_name_map() -> dict[str, str]:
    """Map every configured ``channel_id`` to a friendly display name.

    Resolution order per channel: ``location_alias`` → ``name`` → ``"通道 {channel_id}"``.
    Built across all enabled video sources; later sources override earlier ones on
    channel_id collisions (acceptable for overview-style display, where a bare id is
    used as secondary fallback text on the client).
    """
    sources = VideoSource.query.filter(
        VideoSource.status == VideoSourceStatus.enabled.value,
    ).order_by(VideoSource.is_active.desc(), VideoSource.id.asc()).all()

    display_names: dict[str, str] = {}
    for source in sources:
        for channel in _channels_from_source_config(source.source_type, source.config_json or {}):
            channel_id = str(channel.get("channel_id") or "").strip()
            if not channel_id:
                continue
            alias = str(channel.get("location_alias") or "").strip()
            name = str(channel.get("name") or "").strip()
            display_names[channel_id] = alias or name or f"通道 {channel_id}"
    return display_names
