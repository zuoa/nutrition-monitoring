import json
import logging
import os
import threading
import xml.etree.ElementTree as ET
from datetime import datetime
from uuid import uuid4

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

    @staticmethod
    def _find_text(element: ET.Element, tag: str) -> str:
        """Find tag text ignoring namespace prefix."""
        # Try without namespace first
        node = element.find(f".//{tag}")
        if node is not None and node.text:
            return node.text.strip()
        # Try with wildcard namespace
        node = element.find(f".//{{{ET.QName(tag).namespace}}}{tag}") if "{" in tag else None
        return (node.text or "").strip() if node is not None else ""

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
            start=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            end=end.strftime("%Y-%m-%dT%H:%M:%SZ"),
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

                # Build ISAPI download URL for this segment
                base = self._base_url(channel_id)
                download_url = (
                    f"{base}/ISAPI/ContentMgmt/download"
                    f"?name={track_id}"
                    f"&startTime={seg_start}"
                    f"&endTime={seg_end}"
                )

                seg_start_dt = self._parse_isapi_time(seg_start)
                filename = f"cam{channel_id}_{int(seg_start_dt.timestamp())}.mp4"

                recordings.append(
                    {
                        "filename": filename,
                        "start_time": seg_start_dt.isoformat(),
                        "end_time": self._parse_isapi_time(seg_end).isoformat(),
                        "download_url": download_url,
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
            session = self._session(channel_id)

            headers = {}
            if resume_offset > 0:
                headers["Range"] = f"bytes={resume_offset}-"

            try:
                with session.get(
                    download_url, headers=headers, stream=True, timeout=300
                ) as resp:
                    resp.raise_for_status()
                    mode = "ab" if resume_offset > 0 else "wb"
                    with open(save_path, mode) as f:
                        for chunk in resp.iter_content(chunk_size=8192):
                            f.write(chunk)
                return True
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

    def capture_snapshot(self, channel_id: str) -> dict:
        cam = self.cameras.get(channel_id, {})
        if not cam:
            raise ValueError(f"未配置 channel_id={channel_id} 的摄像头")

        snapshot_url = (
            f"{self._base_url(channel_id)}"
            f"/ISAPI/Streaming/Channels/{channel_id}01/picture"
        )
        resp = self._session(channel_id).get(snapshot_url, timeout=10)
        resp.raise_for_status()
        return {
            "content": resp.content,
            "content_type": resp.headers.get("Content-Type", "image/jpeg"),
        }
