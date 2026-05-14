import json
from typing import Any, Mapping

from app.services.hikvision_camera import HikvisionCameraService
from app.services.nvr import NVRService


def build_video_source_adapter(runtime_source: Mapping[str, Any], app_config: Mapping[str, Any] | None = None):
    source_type = str(runtime_source.get("source_type") or "").strip()
    config = runtime_source.get("config") or {}
    if source_type == "hikvision_camera":
        cameras = {
            str(camera.get("channel_id")): {
                "name": camera.get("name", ""),
                "host": camera.get("host", ""),
                "port": int(camera.get("port", 80)),
                "rtsp_port": int(camera.get("rtsp_port") or config.get("rtsp_port") or 554),
                "stream_id": camera.get("stream_id", ""),
                "username": camera.get("username", "admin"),
                "password": camera.get("password", ""),
            }
            for camera in config.get("cameras", [])
            if str(camera.get("channel_id") or "").strip()
        }
        return HikvisionCameraService({
            "HIKVISION_CAMERAS": json.dumps(cameras, ensure_ascii=False),
            "HIKVISION_RTSP_PORT": int(config.get("rtsp_port") or 554),
            "FFMPEG_BIN": (app_config or {}).get("FFMPEG_BIN") or config.get("FFMPEG_BIN") or "ffmpeg",
            "SNAPSHOT_TIMEOUT": (app_config or {}).get("SNAPSHOT_TIMEOUT") or config.get("SNAPSHOT_TIMEOUT") or 20,
            "VIDEO_TIMEZONE": (app_config or {}).get("VIDEO_TIMEZONE") or config.get("VIDEO_TIMEZONE"),
            "APP_TIMEZONE": (app_config or {}).get("APP_TIMEZONE") or config.get("APP_TIMEZONE"),
        })

    return NVRService({
        "NVR_HOST": config.get("host", ""),
        "NVR_PORT": int(config.get("port", 8080)),
        "NVR_USERNAME": config.get("username", ""),
        "NVR_PASSWORD": config.get("password", ""),
        "NVR_RTSP_PORT": int(config.get("rtsp_port") or 554),
        "NVR_CHANNELS": config.get("channels", []),
        "FFMPEG_BIN": (app_config or {}).get("FFMPEG_BIN") or config.get("FFMPEG_BIN") or "ffmpeg",
        "SNAPSHOT_TIMEOUT": (app_config or {}).get("SNAPSHOT_TIMEOUT") or config.get("SNAPSHOT_TIMEOUT") or 20,
        "VIDEO_TIMEZONE": (app_config or {}).get("VIDEO_TIMEZONE") or config.get("VIDEO_TIMEZONE"),
        "APP_TIMEZONE": (app_config or {}).get("APP_TIMEZONE") or config.get("APP_TIMEZONE"),
    })
