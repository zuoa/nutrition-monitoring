import os
import sys
import types
import unittest
from datetime import datetime


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

if "flask_migrate" not in sys.modules:
    flask_migrate = types.ModuleType("flask_migrate")

    class _Migrate:
        def init_app(self, *args, **kwargs):
            return None

    flask_migrate.Migrate = _Migrate
    sys.modules["flask_migrate"] = flask_migrate

if "pythonjsonlogger" not in sys.modules:
    pythonjsonlogger = types.ModuleType("pythonjsonlogger")
    jsonlogger = types.ModuleType("jsonlogger")

    class _JsonFormatter:
        def __init__(self, *args, **kwargs):
            pass

    jsonlogger.JsonFormatter = _JsonFormatter
    pythonjsonlogger.jsonlogger = jsonlogger
    sys.modules["pythonjsonlogger"] = pythonjsonlogger

if "redis" not in sys.modules:
    redis = types.ModuleType("redis")
    redis.from_url = lambda *args, **kwargs: object()
    sys.modules["redis"] = redis

from app.services.hikvision_camera import HikvisionCameraService  # noqa: E402


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        return None


class _FakeSession:
    def __init__(self, response_text: str):
        self.response_text = response_text
        self.calls: list[dict] = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append({
            "url": url,
            "data": data,
            "headers": headers,
            "timeout": timeout,
        })
        return _FakeResponse(self.response_text)


class HikvisionCameraServiceTests(unittest.TestCase):
    def test_list_recordings_converts_local_window_to_utc_using_video_timezone(self):
        service = HikvisionCameraService({
            "HIKVISION_CAMERAS": {
                "1": {
                    "host": "192.168.1.10",
                    "port": 80,
                    "username": "admin",
                    "password": "secret",
                },
            },
            "VIDEO_TIMEZONE": "Asia/Shanghai",
        })
        fake_session = _FakeSession("""
            <CMSearchResult version="1.0" xmlns="urn:psialliance-org">
              <responseStatusStrg>OK</responseStatusStrg>
              <numOfMatches>1</numOfMatches>
              <matchList>
                <searchMatchItem>
                  <timeSpan>
                    <startTime>2026-04-03T03:35:00Z</startTime>
                    <endTime>2026-04-03T03:40:00Z</endTime>
                  </timeSpan>
                  <mediaSegmentDescriptor>
                    <playbackURI>rtsp://192.168.1.10/Streaming/tracks/101?starttime=2026-04-03T03:35:00Z&amp;endtime=2026-04-03T03:40:00Z&amp;name=ch01_07010000064000100&amp;size=89992380</playbackURI>
                  </mediaSegmentDescriptor>
                </searchMatchItem>
              </matchList>
            </CMSearchResult>
        """)
        service._sessions["1"] = fake_session

        recordings = service.list_recordings(
            "1",
            datetime(2026, 4, 3, 11, 30),
            datetime(2026, 4, 3, 13, 0),
        )

        self.assertEqual(len(recordings), 1)
        self.assertIn("<startTime>2026-04-03T03:30:00Z</startTime>", fake_session.calls[0]["data"])
        self.assertIn("<endTime>2026-04-03T05:00:00Z</endTime>", fake_session.calls[0]["data"])
        self.assertEqual(recordings[0]["start_time"], "2026-04-03T03:35:00+00:00")
        self.assertEqual(
            recordings[0]["download_url"],
            "http://192.168.1.10:80/ISAPI/ContentMgmt/download"
            "?starttime=2026-04-03T03%3A35%3A00Z"
            "&endtime=2026-04-03T03%3A40%3A00Z"
            "&name=ch01_07010000064000100"
            "&size=89992380",
        )


if __name__ == "__main__":
    unittest.main()
