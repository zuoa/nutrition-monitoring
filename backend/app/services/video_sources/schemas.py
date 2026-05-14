from copy import deepcopy
from typing import Any, Mapping

from app.models import VideoSourceStatus, VideoSourceType


class VideoSourceConfigError(ValueError):
    pass


def _as_non_empty_string(value: Any, field_name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise VideoSourceConfigError(f"{field_name} 不能为空")
    return result


def _as_optional_string(value: Any) -> str:
    return str(value or "").strip()


def _as_int(value: Any, field_name: str, default: int | None = None) -> int:
    if value in (None, "") and default is not None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise VideoSourceConfigError(f"{field_name} 必须是整数")


def _normalize_channel_ids(value: Any) -> list[str]:
    if value is None or value == "":
        raise VideoSourceConfigError("channel_ids 不能为空")
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        items = [str(part).strip() for part in value]
    else:
        raise VideoSourceConfigError("channel_ids 格式无效")
    normalized = [item for item in items if item]
    if not normalized:
        raise VideoSourceConfigError("channel_ids 不能为空")
    return normalized


def _credentials_by_channel(existing_credentials: Mapping[str, Any] | None) -> dict[str, dict[str, str]]:
    cameras = existing_credentials.get("cameras") if isinstance(existing_credentials, Mapping) else []
    if not isinstance(cameras, list):
        return {}
    result: dict[str, dict[str, str]] = {}
    for item in cameras:
        if not isinstance(item, Mapping):
            continue
        channel_id = str(item.get("channel_id") or "").strip()
        if not channel_id:
            continue
        result[channel_id] = {
            "channel_id": channel_id,
            "username": _as_optional_string(item.get("username")),
            "password": _as_optional_string(item.get("password")),
        }
    return result


def _shared_hikvision_credentials(existing_credentials: Mapping[str, Any] | None) -> dict[str, str]:
    if not isinstance(existing_credentials, Mapping):
        return {"username": "", "password": ""}
    username = _as_optional_string(existing_credentials.get("username"))
    password = _as_optional_string(existing_credentials.get("password"))
    if username or password:
        return {"username": username, "password": password}

    by_channel = _credentials_by_channel(existing_credentials)
    first = next(iter(by_channel.values()), {})
    return {
        "username": _as_optional_string(first.get("username")),
        "password": _as_optional_string(first.get("password")),
    }


def _normalize_nvr_config(
    config: Mapping[str, Any],
    existing_credentials: Mapping[str, Any] | None,
    existing_config: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    host = _as_non_empty_string(config.get("host"), "host")
    port = _as_int(config.get("port"), "port", 8080)
    channel_ids = _normalize_channel_ids(config.get("channel_ids"))
    username = _as_optional_string(config.get("username")) or _as_optional_string((existing_credentials or {}).get("username"))
    password = _as_optional_string(config.get("password")) or _as_optional_string((existing_credentials or {}).get("password"))
    if not username:
        raise VideoSourceConfigError("username 不能为空")
    if not password:
        raise VideoSourceConfigError("password 不能为空")
    return (
        {
            "host": host,
            "port": port,
            "channel_ids": channel_ids,
            "selected_channel_ids": channel_ids,
            "channels": _normalize_nvr_channels(config.get("channels"), channel_ids),
            "channel_rois": _merge_channel_rois(channel_ids, existing_config),
            "channel_location_aliases": _merge_channel_location_aliases(channel_ids, existing_config),
            "download_trigger_time": _as_optional_string(config.get("download_trigger_time")) or "21:30",
            "local_storage_path": _as_optional_string(config.get("local_storage_path")) or "/data/nvr_cache",
            "retention_days": _as_int(config.get("retention_days"), "retention_days", 3),
        },
        {
            "username": username,
            "password": password,
        },
    )


def _normalize_nvr_channels(value: Any, channel_ids: list[str]) -> list[dict[str, str]]:
    by_id: dict[str, dict[str, str]] = {}
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, Mapping):
                continue
            channel_id = _as_optional_string(item.get("channel_id"))
            if not channel_id:
                continue
            by_id[channel_id] = {
                "channel_id": channel_id,
                "name": _as_optional_string(item.get("name")) or f"通道 {channel_id}",
                **({"stream_id": _as_optional_string(item.get("stream_id"))} if _as_optional_string(item.get("stream_id")) else {}),
            }
    return [
        by_id.get(channel_id, {"channel_id": channel_id, "name": f"通道 {channel_id}"})
        for channel_id in channel_ids
    ]


def _normalize_hikvision_config(
    config: Mapping[str, Any],
    existing_credentials: Mapping[str, Any] | None,
    existing_config: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    cameras = config.get("cameras")
    shared_existing_credentials = _shared_hikvision_credentials(existing_credentials)
    legacy_first_camera = cameras[0] if isinstance(cameras, list) and cameras else {}
    username = (
        _as_optional_string(config.get("username"))
        or _as_optional_string(getattr(legacy_first_camera, "get", lambda *_: "")("username"))
        or shared_existing_credentials.get("username", "")
    )
    password = (
        _as_optional_string(config.get("password"))
        or _as_optional_string(getattr(legacy_first_camera, "get", lambda *_: "")("password"))
        or shared_existing_credentials.get("password", "")
    )

    if not username:
        raise VideoSourceConfigError("username 不能为空")
    if not password:
        raise VideoSourceConfigError("password 不能为空")
    if not isinstance(cameras, list) or not cameras:
        raise VideoSourceConfigError("探测后未生成可用通道，请先检查海康设备连接")

    existing_rois = _roi_by_channel(existing_config)
    existing_aliases = _location_alias_by_channel(existing_config)
    normalized_cameras: list[dict[str, Any]] = []
    seen_channel_ids: set[str] = set()

    for item in cameras:
        if not isinstance(item, Mapping):
            raise VideoSourceConfigError("cameras 项格式无效")
        channel_id = _as_non_empty_string(item.get("channel_id"), "camera.channel_id")
        if channel_id in seen_channel_ids:
            raise VideoSourceConfigError(f"channel_id 重复: {channel_id}")
        seen_channel_ids.add(channel_id)

        normalized_camera = {
            "channel_id": channel_id,
            "name": _as_optional_string(item.get("name")) or f"摄像头 {channel_id}",
            "host": _as_non_empty_string(item.get("host"), f"摄像头 {channel_id} host"),
            "port": _as_int(item.get("port"), f"摄像头 {channel_id} port", 80),
        }
        roi_region = _normalize_optional_roi_region(item.get("roi_region") or existing_rois.get(channel_id))
        if roi_region:
            normalized_camera["roi_region"] = roi_region
        location_alias = _normalize_location_alias(
            item.get("location_alias") if "location_alias" in item else existing_aliases.get(channel_id)
        )
        if location_alias:
            normalized_camera["location_alias"] = location_alias
        normalized_cameras.append(normalized_camera)

    selected_channel_ids = config.get("selected_channel_ids")
    if selected_channel_ids in (None, ""):
        selected_channel_ids = [camera["channel_id"] for camera in normalized_cameras]
    normalized_selected_channel_ids = _normalize_channel_ids(selected_channel_ids)

    return (
        {
            "host": _as_non_empty_string(
                config.get("host") or normalized_cameras[0].get("host"),
                "host",
            ),
            "port": _as_int(
                config.get("port") if config.get("port") not in (None, "") else normalized_cameras[0].get("port"),
                "port",
                80,
            ),
            "device_name": _as_optional_string(config.get("device_name")),
            "device_model": _as_optional_string(config.get("device_model")),
            "device_serial_number": _as_optional_string(config.get("device_serial_number")),
            "selected_channel_ids": normalized_selected_channel_ids,
            "cameras": normalized_cameras,
        },
        {
            "username": username,
            "password": password,
        },
    )


def normalize_video_source_payload(
    data: Mapping[str, Any],
    *,
    existing_source: Any = None,
    existing_credentials: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise VideoSourceConfigError("请求体必须是对象")

    source_type = _as_optional_string(data.get("source_type")) or (
        getattr(existing_source, "source_type", "") if existing_source else ""
    )
    if source_type not in {item.value for item in VideoSourceType}:
        raise VideoSourceConfigError("source_type 仅支持 nvr 或 hikvision_camera")

    name = _as_optional_string(data.get("name")) or (getattr(existing_source, "name", "") if existing_source else "")
    if not name:
        raise VideoSourceConfigError("name 不能为空")

    status = _as_optional_string(data.get("status")) or (
        getattr(existing_source, "status", VideoSourceStatus.enabled.value) if existing_source else VideoSourceStatus.enabled.value
    )
    if status not in {item.value for item in VideoSourceStatus}:
        raise VideoSourceConfigError("status 仅支持 enabled 或 disabled")

    base_config = deepcopy(getattr(existing_source, "config_json", {}) or {})
    incoming_config = data.get("config")
    if incoming_config is None:
        incoming_config = base_config
    if not isinstance(incoming_config, Mapping):
        raise VideoSourceConfigError("config 必须是对象")

    if source_type == VideoSourceType.nvr.value:
        normalized_config, normalized_credentials = _normalize_nvr_config(
            incoming_config,
            existing_credentials,
            base_config,
        )
    else:
        normalized_config, normalized_credentials = _normalize_hikvision_config(
            incoming_config,
            existing_credentials,
            base_config,
        )

    return {
        "name": name,
        "source_type": source_type,
        "status": status,
        "is_active": bool(data.get("is_active", getattr(existing_source, "is_active", False))),
        "config_json": normalized_config,
        "credentials": normalized_credentials,
    }


def _normalize_optional_roi_region(value: Any) -> dict[str, int] | None:
    if value in (None, ""):
        return None
    if not isinstance(value, Mapping):
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


def _roi_by_channel(config: Mapping[str, Any] | None) -> dict[str, dict[str, int]]:
    if not isinstance(config, Mapping):
        return {}
    result: dict[str, dict[str, int]] = {}
    cameras = config.get("cameras")
    if isinstance(cameras, list):
        for item in cameras:
            if not isinstance(item, Mapping):
                continue
            channel_id = str(item.get("channel_id") or "").strip()
            roi_region = _normalize_optional_roi_region(item.get("roi_region"))
            if channel_id and roi_region:
                result[channel_id] = roi_region
    channel_rois = config.get("channel_rois")
    if isinstance(channel_rois, Mapping):
        for channel_id, roi_value in channel_rois.items():
            normalized_channel_id = str(channel_id or "").strip()
            roi_region = _normalize_optional_roi_region(roi_value)
            if normalized_channel_id and roi_region:
                result[normalized_channel_id] = roi_region
    return result


def _merge_channel_rois(channel_ids: list[str], existing_config: Mapping[str, Any] | None) -> dict[str, dict[str, int]]:
    existing_rois = _roi_by_channel(existing_config)
    return {
        channel_id: existing_rois[channel_id]
        for channel_id in channel_ids
        if channel_id in existing_rois
    }


def _normalize_location_alias(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _location_alias_by_channel(config: Mapping[str, Any] | None) -> dict[str, str]:
    if not isinstance(config, Mapping):
        return {}
    result: dict[str, str] = {}
    cameras = config.get("cameras")
    if isinstance(cameras, list):
        for item in cameras:
            if not isinstance(item, Mapping):
                continue
            channel_id = str(item.get("channel_id") or "").strip()
            location_alias = _normalize_location_alias(item.get("location_alias"))
            if channel_id and location_alias:
                result[channel_id] = location_alias
    channel_aliases = config.get("channel_location_aliases")
    if isinstance(channel_aliases, Mapping):
        for channel_id, alias_value in channel_aliases.items():
            normalized_channel_id = str(channel_id or "").strip()
            location_alias = _normalize_location_alias(alias_value)
            if normalized_channel_id and location_alias:
                result[normalized_channel_id] = location_alias
    return result


def _merge_channel_location_aliases(channel_ids: list[str], existing_config: Mapping[str, Any] | None) -> dict[str, str]:
    existing_aliases = _location_alias_by_channel(existing_config)
    return {
        channel_id: existing_aliases[channel_id]
        for channel_id in channel_ids
        if channel_id in existing_aliases
    }
