import logging
import os
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from requests.auth import HTTPDigestAuth

logger = logging.getLogger(__name__)


class NVRService:
    """Abstract NVR adapter. Supports ONVIF-compatible and generic HTTP NVRs."""

    def __init__(self, config: dict):
        self.host = config.get("NVR_HOST", "")
        self.port = int(config.get("NVR_PORT", 8080))
        self.username = config.get("NVR_USERNAME", "")
        self.password = config.get("NVR_PASSWORD", "")
        self.base_url = f"http://{self.host}:{self.port}"
        self._session = requests.Session()
        self._session.auth = (self.username, self.password)
        self._isapi_session = requests.Session()
        self._isapi_session.auth = HTTPDigestAuth(self.username or "admin", self.password or "")

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
        return normalized_name or f"通道 {channel_id}"

    @staticmethod
    def _sort_channels(channels: list[dict]) -> list[dict]:
        def _sort_key(item: dict):
            channel_id = str(item.get("channel_id") or "").strip()
            return (not channel_id.isdigit(), int(channel_id) if channel_id.isdigit() else channel_id)

        deduped: dict[str, dict] = {}
        for item in channels:
            channel_id = str(item.get("channel_id") or "").strip()
            if not channel_id:
                continue
            deduped[channel_id] = {
                "channel_id": channel_id,
                "name": NVRService._normalize_channel_name(channel_id, str(item.get("name") or "").strip()),
            }
        return sorted(deduped.values(), key=_sort_key)

    @staticmethod
    def _stream_id_to_channel_id(stream_id: str) -> str:
        normalized = str(stream_id or "").strip()
        if normalized.endswith("01") and len(normalized) > 2:
            return normalized[:-2]
        return normalized

    def _get_isapi_xml(self, path: str, *, timeout: int = 10) -> ET.Element:
        resp = self._isapi_session.get(f"{self.base_url}{path}", timeout=timeout)
        resp.raise_for_status()
        return ET.fromstring(resp.text)

    def _list_hikvision_input_proxy_channels(self) -> list[dict]:
        root = self._get_isapi_xml("/ISAPI/ContentMgmt/InputProxy/channels", timeout=15)
        channels = []
        for item in root.iter():
            if item.tag.split("}")[-1] != "InputProxyChannel":
                continue
            channel_id = self._extract_text_by_local_name(item, "id")
            if not channel_id:
                continue
            name = self._extract_text_by_local_name(
                item,
                "name",
                "channelName",
                "videoInputName",
                "videoInputChannelName",
                "sourceInputPortDescriptor",
                "ipAddress",
            )
            channels.append({
                "channel_id": str(channel_id).strip(),
                "name": self._normalize_channel_name(str(channel_id).strip(), name),
            })
        return self._sort_channels(channels)

    def _list_hikvision_video_input_channels(self) -> list[dict]:
        root = self._get_isapi_xml("/ISAPI/System/Video/inputs/channels", timeout=15)
        channels = []
        for item in root.iter():
            if item.tag.split("}")[-1] != "VideoInputChannel":
                continue
            channel_id = self._extract_text_by_local_name(item, "id")
            if not channel_id:
                continue
            name = self._extract_text_by_local_name(
                item,
                "name",
                "channelName",
                "videoInputName",
                "videoInputChannelName",
            )
            channels.append({
                "channel_id": str(channel_id).strip(),
                "name": self._normalize_channel_name(str(channel_id).strip(), name),
            })
        return self._sort_channels(channels)

    def _list_hikvision_streaming_channels(self) -> list[dict]:
        root = self._get_isapi_xml("/ISAPI/Streaming/channels", timeout=15)
        channels = []
        for item in root.iter():
            if item.tag.split("}")[-1] != "StreamingChannel":
                continue
            stream_id = self._extract_text_by_local_name(item, "id")
            channel_id = self._stream_id_to_channel_id(stream_id)
            if not channel_id:
                continue
            name = self._extract_text_by_local_name(
                item,
                "channelName",
                "name",
                "videoInputChannelName",
            )
            channels.append({
                "channel_id": channel_id,
                "name": self._normalize_channel_name(channel_id, name),
            })
        return self._sort_channels(channels)

    def list_recordings(self, channel_id: str, start: datetime, end: datetime) -> list[dict]:
        """List recording segments for a channel in time range.
        Returns list of {filename, start_time, end_time, size, download_url}
        """
        try:
            resp = self._session.get(
                f"{self.base_url}/api/recordings",
                params={
                    "channel": channel_id,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                },
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json().get("recordings", [])
        except Exception as e:
            logger.error(f"NVR list_recordings failed: {e}")
            return []

    def list_channels(self) -> list[dict]:
        """Fetch channel metadata, preferring Hikvision ISAPI for NVRs."""
        last_error = None
        for loader in (
            self._list_hikvision_input_proxy_channels,
            self._list_hikvision_video_input_channels,
            self._list_hikvision_streaming_channels,
        ):
            try:
                channels = loader()
                if channels:
                    return channels
            except Exception as exc:
                last_error = exc

        for path in ("/api/channels", "/api/cameras"):
            try:
                resp = self._session.get(f"{self.base_url}{path}", timeout=15)
                resp.raise_for_status()
                payload = resp.json()
                if isinstance(payload, dict):
                    raw_channels = payload.get("channels") or payload.get("cameras") or payload.get("items")
                else:
                    raw_channels = payload
                if not isinstance(raw_channels, list):
                    continue

                channels = []
                for item in raw_channels:
                    if isinstance(item, dict):
                        channel_id = str(
                            item.get("channel_id")
                            or item.get("channel")
                            or item.get("id")
                            or ""
                        ).strip()
                        name = str(item.get("name") or item.get("label") or "").strip()
                    else:
                        channel_id = str(item or "").strip()
                        name = ""
                    if channel_id:
                        channels.append({
                            "channel_id": channel_id,
                            "name": name or f"通道 {channel_id}",
                        })

                if channels:
                    return channels
            except Exception as exc:
                last_error = exc

        raise ValueError(f"NVR 通道查询失败: {last_error}")

    def download_recording(
        self, download_url: str, save_path: str, resume_offset: int = 0
    ) -> bool:
        """Download a recording file with resume support."""
        os.makedirs(os.path.dirname(save_path), exist_ok=True)

        headers = {}
        if resume_offset > 0:
            headers["Range"] = f"bytes={resume_offset}-"

        try:
            with self._session.get(
                download_url, headers=headers, stream=True, timeout=300
            ) as resp:
                resp.raise_for_status()
                mode = "ab" if resume_offset > 0 else "wb"
                with open(save_path, mode) as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)
            return True
        except Exception as e:
            logger.error(f"NVR download failed for {download_url}: {e}")
            return False

    def get_file_size(self, download_url: str) -> int:
        try:
            resp = self._session.head(download_url, timeout=10)
            return int(resp.headers.get("Content-Length", 0))
        except Exception:
            return 0

    def capture_snapshot(self, channel_id: str | None = None) -> dict:
        """Capture a channel snapshot, preferring Hikvision ISAPI for NVRs."""
        resolved_channel_id = str(channel_id or "").strip()
        if not resolved_channel_id:
            raise ValueError("channel_id 不能为空")

        stream_channel_id = f"{resolved_channel_id}01" if not resolved_channel_id.endswith("01") else resolved_channel_id
        attempts = [
            (self._isapi_session, f"{self.base_url}/ISAPI/Streaming/channels/{stream_channel_id}/picture", None),
            (self._session, f"{self.base_url}/api/snapshot", {"channel": resolved_channel_id}),
            (self._session, f"{self.base_url}/api/channels/{resolved_channel_id}/snapshot", None),
            (self._session, f"{self.base_url}/api/cameras/{resolved_channel_id}/snapshot", None),
        ]
        last_error = None

        for session, url, params in attempts:
            try:
                resp = session.get(url, params=params, timeout=15)
                resp.raise_for_status()
                content_type = resp.headers.get("Content-Type", "image/jpeg")
                if any(item in content_type.lower() for item in ("json", "text", "xml", "html")):
                    raise ValueError("NVR 抓拍接口返回了非图片内容")
                return {
                    "content": resp.content,
                    "content_type": content_type,
                    "channel_id": resolved_channel_id,
                }
            except Exception as exc:
                last_error = exc

        raise ValueError(f"NVR 抓拍失败: {last_error}")

    def is_available(self) -> bool:
        try:
            resp = self._isapi_session.get(f"{self.base_url}/ISAPI/System/deviceInfo", timeout=5)
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        try:
            resp = self._session.get(f"{self.base_url}/api/health", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False
