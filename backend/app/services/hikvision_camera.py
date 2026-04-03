import json
import logging
import os
import re
import subprocess
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from requests.auth import HTTPDigestAuth

logger = logging.getLogger(__name__)

# Hikvision ISAPI uses Digest auth and XML payloads.
# Each physical camera is a separate device; channel_id maps to camera config.
# Config example (JSON mapping passed by video source manager):
#   {"1": {"host": "192.168.1.101", "port": 80, "username": "admin", "password": "xxx"},
#    "2": {"host": "192.168.1.102", "port": 80, "username": "admin", "password": "xxx"}}

# Global lock to ensure only one camera downloads at a time
_download_lock = threading.Lock()

_SEARCH_XML = """\
<CMSearchDescription>
  <searchID>{search_id}</searchID>
  <trackList><trackID>{track_id}</trackID></trackList>
  <timeSpanList>
    <timeSpan>
      <startTime>{start}</startTime>
      <endTime>{end}</endTime>
    </timeSpan>
  </timeSpanList>
  <maxResults>50</maxResults>
  <searchResultPostion>0</searchResultPostion>
  <metadataList>
    <metadataDescriptor>//recordType.meta.std-cgi.com</metadataDescriptor>
  </metadataList>
</CMSearchDescription>"""


class HikvisionCameraService:
    """Direct Hikvision IP camera adapter via ISAPI.

    Implements the same interface as NVRService so it can be swapped in without
    changing any caller code.
    """

    def __init__(self, config: dict):
        cameras_raw = config.get("HIKVISION_CAMERAS", "{}")
        self.cameras: dict = (
            json.loads(cameras_raw) if isinstance(cameras_raw, str) else cameras_raw
        )
        timezone_name = str(
            config.get("VIDEO_TIMEZONE")
            or config.get("APP_TIMEZONE")
            or "Asia/Shanghai"
        ).strip() or "Asia/Shanghai"
        try:
            self.video_timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            logger.warning("Unknown VIDEO_TIMEZONE=%s, fallback to Asia/Shanghai", timezone_name)
            self.video_timezone = ZoneInfo("Asia/Shanghai")
        # Lazy per-channel sessions (Digest auth)
        self._sessions: dict[str, requests.Session] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _session(self, channel_id: str) -> requests.Session:
        if channel_id not in self._sessions:
            cam = self.cameras.get(channel_id, {})
            s = requests.Session()
            s.auth = HTTPDigestAuth(
                cam.get("username", "admin"), cam.get("password", "")
            )
            self._sessions[channel_id] = s
        return self._sessions[channel_id]

    def _base_url(self, channel_id: str) -> str:
        cam = self.cameras.get(channel_id, {})
        return f"http://{cam.get('host', '')}:{cam.get('port', 80)}"

    def _channel_from_url(self, url: str) -> str:
        """Reverse-lookup channel_id by matching camera host in a download URL."""
        for ch_id, cam in self.cameras.items():
            host = cam.get("host", "")
            if host and host in url:
                return ch_id
        return next(iter(self.cameras), "")

    @staticmethod
    def _parse_isapi_time(t: str) -> datetime:
        return datetime.fromisoformat(t.replace("Z", "+00:00"))

    def _to_isapi_utc(self, value: datetime) -> str:
        if value.tzinfo is None:
            localized = value.replace(tzinfo=self.video_timezone)
        else:
            localized = value.astimezone(self.video_timezone)
        return localized.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _build_playback_url(self, channel_id: str, playback_uri: str) -> str:
        parsed = urlparse(str(playback_uri or "").strip())
        if parsed.scheme.lower() != "rtsp":
            raise ValueError("海康录像缺少可用的 RTSP playbackURI")

        cam = self.cameras.get(channel_id, {})
        username = quote(str(cam.get("username", "admin") or "admin"), safe="")
        password = quote(str(cam.get("password", "") or ""), safe="")
        auth = f"{username}:{password}@" if password else f"{username}@"
        host = parsed.hostname or str(cam.get("host", "") or "")
        if not host:
            raise ValueError(f"未解析到 channel_id={channel_id} 的 RTSP 主机地址")
        port = parsed.port
        netloc = f"{auth}{host}:{port}" if port else f"{auth}{host}"
        normalized_path = self._normalize_playback_path(parsed.path)
        normalized_query = self._normalize_playback_query(parsed.query)
        return urlunparse(parsed._replace(netloc=netloc, path=normalized_path, query=normalized_query))

    @staticmethod
    def _normalize_playback_path(path: str) -> str:
        normalized = str(path or "").strip() or "/"
        if re.fullmatch(r"/Streaming/tracks/\d+", normalized, flags=re.IGNORECASE):
            return f"{normalized}/"
        return normalized

    @classmethod
    def _normalize_playback_query(cls, query: str) -> str:
        if not query:
            return ""
        normalized_items: list[tuple[str, str]] = []
        for key, value in parse_qsl(query, keep_blank_values=True):
            if key.lower() in {"starttime", "endtime"}:
                normalized_items.append((key, cls._normalize_playback_timestamp(value)))
            else:
                normalized_items.append((key, value))
        return urlencode(normalized_items, doseq=True)

    @staticmethod
    def _normalize_playback_timestamp(value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return raw
        if re.fullmatch(r"\d{8}T\d{6}Z", raw):
            return raw
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return raw
        return parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    @staticmethod
    def _strip_problematic_playback_params(playback_url: str) -> str:
        parsed = urlparse(playback_url)
        if not parsed.query:
            return playback_url
        filtered_items = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in {"name", "size"}
        ]
        filtered_query = urlencode(filtered_items, doseq=True)
        return urlunparse(parsed._replace(query=filtered_query))

    @staticmethod
    def _build_ffmpeg_command(playback_url: str, save_path: str) -> list[str]:
        return [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-i",
            playback_url,
            "-an",
            "-c:v",
            "copy",
            "-y",
            save_path,
        ]

    @staticmethod
    def _find_text(element: ET.Element, tag: str) -> str:
        """Find tag text ignoring namespace prefix."""
        node = element.find(f".//{tag}")
        if node is not None and node.text:
            return node.text.strip()
        return HikvisionCameraService._extract_text_by_local_name(element, tag)

    @staticmethod
    def _extract_text_by_local_name(element: ET.Element, *local_names: str) -> str:
        wanted = {name for name in local_names if name}
        if not wanted:
            return ""
        for node in element.iter():
            local_name = node.tag.split("}")[-1]
            if local_name in wanted and node.text and node.text.strip():
                return node.text.strip()
        return ""

    @staticmethod
    def _normalize_channel_name(channel_id: str, name: str) -> str:
        normalized_name = str(name or "").strip()
        if normalized_name:
            return normalized_name
        return "主通道" if str(channel_id) == "1" else f"通道 {channel_id}"

    @staticmethod
    def _requests_session(username: str, password: str) -> requests.Session:
        session = requests.Session()
        session.auth = HTTPDigestAuth(username or "admin", password or "")
        return session

    @staticmethod
    def _read_xml(session: requests.Session, url: str, *, timeout: int = 10) -> ET.Element:
        response = session.get(url, timeout=timeout)
        response.raise_for_status()
        return ET.fromstring(response.text)

    @classmethod
    def _parse_device_info(cls, root: ET.Element) -> dict[str, str]:
        return {
            "device_name": cls._extract_text_by_local_name(root, "deviceName", "name"),
            "model": cls._extract_text_by_local_name(root, "model"),
            "serial_number": cls._extract_text_by_local_name(root, "serialNumber"),
            "firmware_version": cls._extract_text_by_local_name(root, "firmwareVersion"),
        }

    @classmethod
    def _parse_video_input_channels(cls, root: ET.Element) -> list[dict[str, str]]:
        channels: list[dict[str, str]] = []
        for item in root.iter():
            if item.tag.split("}")[-1] != "VideoInputChannel":
                continue
            channel_id = cls._extract_text_by_local_name(item, "id")
            if not channel_id:
                continue
            channels.append({
                "channel_id": str(channel_id).strip(),
                "name": cls._normalize_channel_name(
                    str(channel_id).strip(),
                    cls._extract_text_by_local_name(
                        item,
                        "name",
                        "videoInputName",
                        "videoInputChannelName",
                        "channelName",
                    ),
                ),
            })
        return channels

    @classmethod
    def _parse_streaming_channels(cls, root: ET.Element) -> list[dict[str, str]]:
        grouped: dict[str, dict[str, str]] = {}
        for item in root.iter():
            if item.tag.split("}")[-1] != "StreamingChannel":
                continue
            stream_id = cls._extract_text_by_local_name(item, "id")
            if not stream_id:
                continue
            stream_id = str(stream_id).strip()
            channel_id = stream_id[:-2] if len(stream_id) > 2 else stream_id
            if not channel_id:
                continue
            if channel_id not in grouped:
                grouped[channel_id] = {
                    "channel_id": channel_id,
                    "name": cls._normalize_channel_name(
                        channel_id,
                        cls._extract_text_by_local_name(item, "channelName", "name", "videoInputChannelName"),
                    ),
                }
        return list(grouped.values())

    @classmethod
    def _sort_channels(cls, channels: list[dict[str, str]]) -> list[dict[str, str]]:
        def _sort_key(item: dict[str, str]):
            channel_id = str(item.get("channel_id") or "").strip()
            return (not channel_id.isdigit(), int(channel_id) if channel_id.isdigit() else channel_id)

        deduped: dict[str, dict[str, str]] = {}
        for item in channels:
            channel_id = str(item.get("channel_id") or "").strip()
            if not channel_id:
                continue
            deduped[channel_id] = {
                "channel_id": channel_id,
                "name": cls._normalize_channel_name(channel_id, str(item.get("name") or "").strip()),
            }
        return sorted(deduped.values(), key=_sort_key)

    @classmethod
    def discover_device(
        cls,
        *,
        host: str,
        port: int = 80,
        username: str = "admin",
        password: str = "",
        timeout: int = 10,
    ) -> dict[str, Any]:
        normalized_host = str(host or "").strip()
        if not normalized_host:
            raise ValueError("host 不能为空")

        normalized_port = int(port or 80)
        base_url = f"http://{normalized_host}:{normalized_port}"
        session = cls._requests_session(username or "admin", password or "")

        try:
            device_root = cls._read_xml(
                session,
                f"{base_url}/ISAPI/System/deviceInfo",
                timeout=timeout,
            )
        except Exception as exc:
            raise ValueError(f"读取海康设备信息失败: {exc}") from exc

        channels: list[dict[str, str]] = []
        for path, parser in (
            ("/ISAPI/System/Video/inputs/channels", cls._parse_video_input_channels),
            ("/ISAPI/Streaming/channels", cls._parse_streaming_channels),
        ):
            try:
                root = cls._read_xml(session, f"{base_url}{path}", timeout=timeout)
                channels = parser(root)
                if channels:
                    break
            except Exception:
                continue

        device_info = cls._parse_device_info(device_root)
        channels = cls._sort_channels(channels)
        if not channels:
            channels = [{
                "channel_id": "1",
                "name": cls._normalize_channel_name(
                    "1",
                    device_info.get("device_name") or device_info.get("model") or "",
                ),
            }]

        return {
            "host": normalized_host,
            "port": normalized_port,
            "device_info": device_info,
            "channels": channels,
        }

    # ------------------------------------------------------------------
    # Public interface (matches NVRService)
    # ------------------------------------------------------------------

    def list_recordings(
        self, channel_id: str, start: datetime, end: datetime
    ) -> list[dict]:
        """Query SD card recordings via ISAPI ContentMgmt/search.

        Returns list of {filename, start_time, end_time, download_url}.
        """
        # Hikvision track ID: channel_number * 100 + 1 (main stream)
        try:
            track_id = str(int(channel_id) * 100 + 1)
        except ValueError:
            track_id = "101"

        xml_body = _SEARCH_XML.format(
            search_id=str(uuid4()),
            track_id=track_id,
            start=self._to_isapi_utc(start),
            end=self._to_isapi_utc(end),
        )

        try:
            resp = self._session(channel_id).post(
                f"{self._base_url(channel_id)}/ISAPI/ContentMgmt/search",
                data=xml_body,
                headers={"Content-Type": "application/xml"},
                timeout=30,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Hikvision list_recordings failed (channel={channel_id}): {e}")
            return []

        recordings = []
        try:
            root = ET.fromstring(resp.text)
            for item in root.iter():
                # Match both namespaced and plain <searchMatchItem>
                if not item.tag.endswith("searchMatchItem"):
                    continue

                seg_start = self._find_text(item, "startTime")
                seg_end = self._find_text(item, "endTime")
                if not seg_start or not seg_end:
                    continue

                playback_uri = self._find_text(item, "playbackURI")
                if not playback_uri:
                    logger.warning("Hikvision search result missing playbackURI (channel=%s, start=%s, end=%s)", channel_id, seg_start, seg_end)
                    continue

                seg_start_dt = self._parse_isapi_time(seg_start)
                filename = f"cam{channel_id}_{int(seg_start_dt.timestamp())}.mp4"

                recordings.append(
                    {
                        "filename": filename,
                        "start_time": seg_start_dt.isoformat(),
                        "end_time": self._parse_isapi_time(seg_end).isoformat(),
                        "download_url": playback_uri,
                        "playback_uri": playback_uri,
                        "size": 0,
                    }
                )
        except ET.ParseError as e:
            logger.error(f"Hikvision ISAPI XML parse error: {e}")

        return recordings

    def download_recording(
        self, download_url: str, save_path: str, resume_offset: int = 0
    ) -> bool:
        """Stream-download a recording segment with resume support.

        Note: Hikvision cameras typically support only 1-2 concurrent playback
        sessions. A global lock ensures sequential downloads across all cameras.
        """
        os.makedirs(os.path.dirname(save_path), exist_ok=True)

        channel_id = self._channel_from_url(download_url)

        with _download_lock:
            try:
                playback_url = self._build_playback_url(channel_id, download_url)
                if resume_offset > 0 and os.path.exists(save_path):
                    os.remove(save_path)

                attempts = [playback_url]
                sanitized_playback_url = self._strip_problematic_playback_params(playback_url)
                if sanitized_playback_url != playback_url:
                    attempts.append(sanitized_playback_url)

                for index, attempt_url in enumerate(attempts):
                    result = subprocess.run(
                        self._build_ffmpeg_command(attempt_url, save_path),
                        capture_output=True,
                        text=True,
                        timeout=300,
                        check=False,
                    )
                    if result.returncode == 0:
                        return True

                    error_text = (result.stderr or result.stdout or "").strip()[:1000]
                    if index == 0 and len(attempts) > 1:
                        logger.warning(
                            "Hikvision ffmpeg download failed on original playback URI, retry without name/size (channel=%s, code=%s): %s",
                            channel_id,
                            result.returncode,
                            error_text,
                        )
                        continue

                    logger.error(
                        "Hikvision ffmpeg download failed (channel=%s, code=%s): %s",
                        channel_id,
                        result.returncode,
                        error_text,
                    )
                    return False
            except FileNotFoundError:
                logger.error("Hikvision download failed: ffmpeg is not installed")
                return False
            except subprocess.TimeoutExpired:
                logger.error("Hikvision ffmpeg download timed out (channel=%s)", channel_id)
                return False
            except Exception as e:
                logger.error(f"Hikvision download failed ({download_url}): {e}")
                return False

    def get_file_size(self, download_url: str) -> int:
        channel_id = self._channel_from_url(download_url)
        try:
            resp = self._session(channel_id).head(download_url, timeout=10)
            return int(resp.headers.get("Content-Length", 0))
        except Exception:
            return 0

    def is_available(self) -> bool:
        """Check reachability of the first configured camera."""
        first_channel = next(iter(self.cameras), None)
        if not first_channel:
            return False
        try:
            resp = self._session(first_channel).get(
                f"{self._base_url(first_channel)}/ISAPI/System/deviceInfo",
                timeout=5,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def list_cameras(self) -> list[dict]:
        cameras = []
        for channel_id, camera in self.cameras.items():
            cameras.append({
                "channel_id": str(channel_id),
                "name": camera.get("name") or f"摄像头 {channel_id}",
                "host": camera.get("host", ""),
                "port": int(camera.get("port", 80)),
            })
        return cameras

    def capture_snapshot(self, channel_id: str | None = None) -> dict:
        resolved_channel_id = str(channel_id or "").strip() or next(iter(self.cameras), "")
        cam = self.cameras.get(resolved_channel_id, {})
        if not cam:
            raise ValueError(f"未配置 channel_id={resolved_channel_id} 的摄像头")

        snapshot_url = (
            f"{self._base_url(resolved_channel_id)}"
            f"/ISAPI/Streaming/Channels/{resolved_channel_id}01/picture"
        )
        resp = self._session(resolved_channel_id).get(snapshot_url, timeout=10)
        resp.raise_for_status()
        return {
            "content": resp.content,
            "content_type": resp.headers.get("Content-Type", "image/jpeg"),
            "channel_id": resolved_channel_id,
        }
