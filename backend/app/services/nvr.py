import logging
import os
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from requests.auth import HTTPDigestAuth
from uuid import uuid4
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

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


class NVRService:
    """Hikvision NVR adapter via ISAPI."""

    def __init__(self, config: dict):
        self.host = config.get("NVR_HOST", "")
        self.port = int(config.get("NVR_PORT", 8080))
        self.username = config.get("NVR_USERNAME", "")
        self.password = config.get("NVR_PASSWORD", "")
        self.channel_stream_ids = self._build_channel_stream_id_map(config.get("NVR_CHANNELS"))
        self.base_url = f"http://{self.host}:{self.port}"
        self._isapi_session = requests.Session()
        self._isapi_session.auth = HTTPDigestAuth(self.username or "admin", self.password or "")
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

    @staticmethod
    def _build_channel_stream_id_map(channels: object) -> dict[str, str]:
        if not isinstance(channels, list):
            return {}
        result = {}
        for item in channels:
            if not isinstance(item, dict):
                continue
            channel_id = str(item.get("channel_id") or "").strip()
            stream_id = str(item.get("stream_id") or "").strip()
            if channel_id and stream_id:
                result[channel_id] = stream_id
        return result

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
                **item,
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

    def _channel_id_to_stream_id(self, channel_id: str) -> str:
        normalized = str(channel_id or "").strip()
        configured_stream_id = self.channel_stream_ids.get(normalized)
        if configured_stream_id:
            return configured_stream_id
        if normalized.endswith("01") and len(normalized) > 2:
            return normalized
        try:
            return str(int(normalized) * 100 + 1)
        except (TypeError, ValueError):
            return "101"

    def _snapshot_stream_id_candidates(self, channel_id: str) -> list[str]:
        normalized = str(channel_id or "").strip()
        if not normalized:
            return []
        candidates = []
        configured_stream_id = self.channel_stream_ids.get(normalized)
        if configured_stream_id:
            candidates.append(configured_stream_id)
        if normalized.endswith("01") and len(normalized) > 2:
            candidates.extend([normalized, normalized[:-2]])
        else:
            candidates.extend([f"{normalized}01", normalized])

        result = []
        for item in candidates:
            if item and item not in result:
                result.append(item)
        return result

    def _get_isapi_xml(self, path: str, *, timeout: int = 10) -> ET.Element:
        resp = self._isapi_session.get(f"{self.base_url}{path}", timeout=timeout)
        resp.raise_for_status()
        return ET.fromstring(resp.text)

    @staticmethod
    def _parse_isapi_time(value: str) -> datetime:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))

    def _to_isapi_utc(self, value: datetime) -> str:
        if value.tzinfo is None:
            localized = value.replace(tzinfo=self.video_timezone)
        else:
            localized = value.astimezone(self.video_timezone)
        return localized.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")

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
        return self._parse_streaming_channel_list(root)

    def _list_hikvision_streaming_proxy_channels(self) -> list[dict]:
        root = self._get_isapi_xml("/ISAPI/ContentMgmt/StreamingProxy/channels", timeout=15)
        return self._parse_streaming_channel_list(root)

    def _parse_streaming_channel_list(self, root: ET.Element) -> list[dict]:
        channels = []
        for item in root.iter():
            if item.tag.split("}")[-1] not in {"StreamingChannel", "StreamingProxyChannel"}:
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
                "stream_id": stream_id,
                "name": self._normalize_channel_name(channel_id, name),
            })
        return self._sort_channels(channels)

    def _list_hikvision_recordings(self, channel_id: str, start: datetime, end: datetime) -> list[dict]:
        stream_id = self._channel_id_to_stream_id(channel_id)
        xml_body = _SEARCH_XML.format(
            search_id=str(uuid4()),
            track_id=stream_id,
            start=self._to_isapi_utc(start),
            end=self._to_isapi_utc(end),
        )
        resp = self._isapi_session.post(
            f"{self.base_url}/ISAPI/ContentMgmt/search",
            data=xml_body,
            headers={"Content-Type": "application/xml"},
            timeout=30,
        )
        resp.raise_for_status()

        root = ET.fromstring(resp.text)
        recordings = []
        for item in root.iter():
            if item.tag.split("}")[-1] != "searchMatchItem":
                continue
            seg_start = self._extract_text_by_local_name(item, "startTime")
            seg_end = self._extract_text_by_local_name(item, "endTime")
            playback_uri = self._extract_text_by_local_name(item, "playbackURI")
            if not seg_start or not seg_end or not playback_uri:
                continue
            seg_start_dt = self._parse_isapi_time(seg_start)
            recordings.append({
                "filename": f"nvr{channel_id}_{int(seg_start_dt.timestamp())}.mp4",
                "start_time": seg_start_dt.isoformat(),
                "end_time": self._parse_isapi_time(seg_end).isoformat(),
                "download_url": playback_uri,
                "playback_uri": playback_uri,
                "size": 0,
            })
        return recordings

    @staticmethod
    def _looks_like_error_payload(content_type: str, chunk: bytes) -> bool:
        normalized_type = str(content_type or "").lower()
        stripped = chunk.lstrip()
        if any(item in normalized_type for item in ("xml", "json", "text/", "html")):
            return True
        return stripped.startswith((b"<", b"{"))

    def _download_hikvision_recording(self, playback_uri: str, save_path: str) -> bool:
        request_body = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
            "<downloadRequest>\n"
            f"  <playbackURI>{escape(str(playback_uri or '').strip())}</playbackURI>\n"
            "</downloadRequest>"
        )
        temp_path = f"{save_path}.part"
        response = None
        try:
            response = self._isapi_session.request(
                "GET",
                f"{self.base_url}/ISAPI/ContentMgmt/download",
                data=request_body,
                headers={"Content-Type": "application/xml"},
                stream=True,
                timeout=(10, 300),
            )
            response.raise_for_status()

            content_type = str(response.headers.get("Content-Type") or "")
            wrote_any = False
            with open(temp_path, "wb") as output_file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    if not wrote_any and self._looks_like_error_payload(content_type, chunk):
                        snippet = chunk[:200].decode("utf-8", errors="replace")
                        logger.warning("Hikvision NVR download returned non-video payload: %s", snippet)
                        break
                    output_file.write(chunk)
                    wrote_any = True

            if wrote_any:
                os.replace(temp_path, save_path)
                return True
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return False
        except Exception as exc:
            logger.error("Hikvision NVR download failed: %s", exc)
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return False
        finally:
            if response is not None:
                response.close()

    def list_recordings(self, channel_id: str, start: datetime, end: datetime) -> list[dict]:
        """List recording segments for a channel in time range.
        Returns list of {filename, start_time, end_time, size, download_url}
        """
        try:
            return self._list_hikvision_recordings(channel_id, start, end)
        except Exception as exc:
            logger.error("Hikvision NVR list_recordings failed: %s", exc)
            return []

    def list_channels(self) -> list[dict]:
        """Fetch Hikvision NVR channel metadata via ISAPI."""
        errors = []
        merged: dict[str, dict] = {}
        for loader in (
            self._list_hikvision_input_proxy_channels,
            self._list_hikvision_video_input_channels,
            self._list_hikvision_streaming_proxy_channels,
            self._list_hikvision_streaming_channels,
        ):
            try:
                channels = loader()
                if channels:
                    for channel in channels:
                        channel_id = str(channel.get("channel_id") or "").strip()
                        if not channel_id:
                            continue
                        merged[channel_id] = {
                            **merged.get(channel_id, {}),
                            **channel,
                            "channel_id": channel_id,
                            "name": channel.get("name") or merged.get(channel_id, {}).get("name") or f"通道 {channel_id}",
                        }
            except Exception as exc:
                errors.append(str(exc))

        if merged:
            return self._sort_channels(list(merged.values()))

        detail = "; ".join(errors[:3]) or "未返回通道"
        raise ValueError(f"NVR 通道查询失败: ISAPI 接口不可用: {detail}")

    def download_recording(
        self, download_url: str, save_path: str, resume_offset: int = 0
    ) -> bool:
        """Download a recording file with resume support."""
        os.makedirs(os.path.dirname(save_path), exist_ok=True)

        normalized_url = str(download_url or "").strip()
        if normalized_url.lower().startswith("rtsp://"):
            if resume_offset > 0 and os.path.exists(save_path):
                os.remove(save_path)
            return self._download_hikvision_recording(normalized_url, save_path)
        logger.error("Hikvision NVR download requires RTSP playbackURI, got: %s", normalized_url)
        return False

    def get_file_size(self, download_url: str) -> int:
        return 0

    def capture_snapshot(self, channel_id: str | None = None) -> dict:
        """Capture a channel snapshot via Hikvision ISAPI."""
        resolved_channel_id = str(channel_id or "").strip()
        if not resolved_channel_id:
            raise ValueError("channel_id 不能为空")

        isapi_attempts = [
            (self._isapi_session, f"{self.base_url}/ISAPI/ContentMgmt/StreamingProxy/channels/{stream_id}/picture", None)
            for stream_id in self._snapshot_stream_id_candidates(resolved_channel_id)
        ]
        isapi_attempts.extend(
            (self._isapi_session, f"{self.base_url}/ISAPI/Streaming/{segment}/{stream_id}/picture", None)
            for stream_id in self._snapshot_stream_id_candidates(resolved_channel_id)
            for segment in ("Channels", "channels")
        )
        isapi_errors = []

        for session, url, params in isapi_attempts:
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
                isapi_errors.append(f"{url}: {exc}")

        detail = "; ".join(isapi_errors[:4]) or "未执行任何抓拍请求"
        if any("403" in error or "Forbidden" in error for error in isapi_errors):
            detail = (
                f"{detail}。海康设备返回 403 Forbidden，通常表示当前 NVR 用户没有远程预览/抓图权限，"
                "或该通道不允许通过 ISAPI 抓拍。请检查用户权限、通道预览权限和设备的 ISAPI/HTTP 访问配置。"
            )
        raise ValueError(f"NVR 抓拍失败: ISAPI 抓拍失败: {detail}")

    def is_available(self) -> bool:
        try:
            resp = self._isapi_session.get(f"{self.base_url}/ISAPI/System/deviceInfo", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False
