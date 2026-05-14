import logging
import os
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from requests.auth import HTTPDigestAuth
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from uuid import uuid4
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.services.rtsp_snapshot import build_rtsp_url, capture_rtsp_snapshot

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
        self.rtsp_port = int(config.get("NVR_RTSP_PORT", 554))
        self.username = config.get("NVR_USERNAME", "")
        self.password = config.get("NVR_PASSWORD", "")
        self.ffmpeg_bin = config.get("FFMPEG_BIN", "ffmpeg")
        self.snapshot_timeout = int(config.get("SNAPSHOT_TIMEOUT", 20))
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
    def _is_fallback_channel_name(channel_id: str, name: str) -> bool:
        normalized_name = str(name or "").strip()
        return not normalized_name or normalized_name == f"通道 {channel_id}"

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

    @staticmethod
    def _safe_filename_part(value: str, fallback: str = "unknown") -> str:
        normalized = str(value or "").strip()
        result = "".join(
            char if (char.isascii() and (char.isalnum() or char in {"-", "_"})) else "_"
            for char in normalized
        ).strip("_")
        return result or fallback

    def _recording_filename(self, channel_id: str, start_time: datetime) -> str:
        if start_time.tzinfo is None:
            local_start = start_time.replace(tzinfo=self.video_timezone)
        else:
            local_start = start_time.astimezone(self.video_timezone)
        channel_part = self._safe_filename_part(channel_id, "channel")
        return f"nvr_ch{channel_part}_{local_start.strftime('%Y-%m-%d_%H-%M-%S')}.mp4"

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
        else:
            for channel in self._discover_stream_channels_for_snapshot(normalized):
                stream_id = str(channel.get("stream_id") or "").strip()
                if stream_id:
                    candidates.append(stream_id)
        if normalized.endswith("01") and len(normalized) > 2:
            candidates.extend([normalized, normalized[:-2]])
        else:
            candidates.extend([f"{normalized}01", normalized])

        result = []
        for item in candidates:
            if item and item not in result:
                result.append(item)
        return result

    def _discover_stream_channels_for_snapshot(self, channel_id: str) -> list[dict]:
        discovered = []
        for loader in (
            self._list_hikvision_streaming_proxy_channels,
            self._list_hikvision_streaming_channels,
        ):
            try:
                discovered.extend(loader())
            except Exception as exc:
                logger.info("Skip runtime stream channel discovery via %s: %s", loader.__name__, exc)
        normalized_channel_id = str(channel_id or "").strip()
        return [
            channel
            for channel in discovered
            if str(channel.get("channel_id") or "").strip() == normalized_channel_id
        ]

    def _get_isapi_xml(self, path: str, *, timeout: int = 10) -> ET.Element:
        resp = self._isapi_session.get(f"{self.base_url}{path}", timeout=timeout)
        resp.raise_for_status()
        return ET.fromstring(resp.text)

    def _parse_isapi_time(self, value: str) -> datetime:
        raw = str(value or "").strip()
        if raw.endswith("Z"):
            return datetime.fromisoformat(raw[:-1]).replace(tzinfo=self.video_timezone)
        parsed = datetime.fromisoformat(raw)
        return self._to_local_time(parsed)

    def _to_isapi_time(self, value: datetime) -> str:
        if value.tzinfo is None:
            localized = value.replace(tzinfo=self.video_timezone)
        else:
            localized = value.astimezone(self.video_timezone)
        return localized.isoformat(timespec="seconds")

    def _to_local_time(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=self.video_timezone)
        return value.astimezone(self.video_timezone)

    def _clip_recording_window(
        self,
        segment_start: datetime,
        segment_end: datetime,
        requested_start: datetime,
        requested_end: datetime,
    ) -> tuple[datetime, datetime] | None:
        seg_start = self._to_local_time(segment_start)
        seg_end = self._to_local_time(segment_end)
        req_start = self._to_local_time(requested_start)
        req_end = self._to_local_time(requested_end)
        clip_start = max(seg_start, req_start)
        clip_end = min(seg_end, req_end)
        if clip_end <= clip_start:
            return None
        return clip_start, clip_end

    @staticmethod
    def _format_playback_time(value: datetime) -> str:
        local_dt = value if value.tzinfo else value.replace(tzinfo=ZoneInfo("UTC"))
        return local_dt.strftime("%Y%m%dT%H%M%SZ")

    def _clip_playback_uri(self, playback_uri: str, clip_start: datetime, clip_end: datetime) -> str:
        parsed = urlparse(str(playback_uri or "").strip())
        if parsed.scheme.lower() != "rtsp":
            return str(playback_uri or "").strip()
        params = parse_qsl(parsed.query, keep_blank_values=True)
        next_params = []
        seen = set()
        replacements = {
            "starttime": self._format_playback_time(clip_start),
            "endtime": self._format_playback_time(clip_end),
        }
        for key, value in params:
            lower_key = key.lower()
            if lower_key in replacements:
                next_params.append((key, replacements[lower_key]))
                seen.add(lower_key)
            else:
                next_params.append((key, value))
        for key, value in replacements.items():
            if key not in seen:
                next_params.append((key, value))
        return urlunparse(parsed._replace(query=urlencode(next_params)))

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
                "cameraName",
                "displayName",
                "deviceName",
                "chanName",
                "channelDescription",
                "description",
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
                "cameraName",
                "displayName",
                "deviceName",
                "chanName",
                "channelDescription",
                "description",
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
                "cameraName",
                "displayName",
                "deviceName",
                "chanName",
                "channelDescription",
                "description",
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
            start=self._to_isapi_time(start),
            end=self._to_isapi_time(end),
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
            seg_end_dt = self._parse_isapi_time(seg_end)
            clip_window = self._clip_recording_window(seg_start_dt, seg_end_dt, start, end)
            if clip_window is None:
                continue
            clip_start_dt, clip_end_dt = clip_window
            # Skip recordings that haven't ended yet (future end times)
            now = datetime.now(self.video_timezone)
            if clip_end_dt > now:
                continue
            clipped_playback_uri = self._clip_playback_uri(playback_uri, clip_start_dt, clip_end_dt)
            recordings.append({
                "filename": self._recording_filename(channel_id, clip_start_dt),
                "start_time": clip_start_dt.isoformat(),
                "end_time": clip_end_dt.isoformat(),
                "source_start_time": seg_start_dt.isoformat(),
                "source_end_time": seg_end_dt.isoformat(),
                "download_url": clipped_playback_uri,
                "playback_uri": clipped_playback_uri,
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
                        existing = merged.get(channel_id, {})
                        incoming_name = str(channel.get("name") or "").strip()
                        existing_name = str(existing.get("name") or "").strip()
                        if existing_name and not self._is_fallback_channel_name(channel_id, existing_name):
                            merged_name = existing_name
                        else:
                            merged_name = incoming_name or existing_name or f"通道 {channel_id}"
                        merged[channel_id] = {
                            **existing,
                            **channel,
                            "channel_id": channel_id,
                            "name": merged_name,
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
        """Capture a channel snapshot from the RTSP main stream via ffmpeg."""
        resolved_channel_id = str(channel_id or "").strip()
        if not resolved_channel_id:
            raise ValueError("channel_id 不能为空")

        stream_id = self._channel_id_to_stream_id(resolved_channel_id)
        rtsp_url = build_rtsp_url(
            host=self.host,
            username=self.username,
            password=self.password,
            rtsp_port=self.rtsp_port,
            stream_id=stream_id,
        )
        return {
            "content": capture_rtsp_snapshot(
                rtsp_url,
                ffmpeg_bin=self.ffmpeg_bin,
                timeout=self.snapshot_timeout,
            ),
            "content_type": "image/jpeg",
            "channel_id": resolved_channel_id,
        }

    def is_available(self) -> bool:
        try:
            resp = self._isapi_session.get(f"{self.base_url}/ISAPI/System/deviceInfo", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False
