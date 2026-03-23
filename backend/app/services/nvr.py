import logging
import os
import time
import requests
from datetime import datetime, date

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

    def is_available(self) -> bool:
        try:
            resp = self._session.get(f"{self.base_url}/api/health", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False
