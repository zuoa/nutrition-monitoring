import os
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import datetime
from unittest import mock


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

    def close(self):
        return None


class _FakeDownloadResponse:
    def __init__(self, chunks=None, headers=None, exc=None):
        self._chunks = list(chunks or [])
        self.headers = headers or {}
        self._exc = exc

    def raise_for_status(self):
        if self._exc:
            raise self._exc
        return None

    def iter_content(self, chunk_size=8192):
        for chunk in self._chunks:
            yield chunk

    def close(self):
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


class _FakeDownloadSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def get(self, url, stream=None, timeout=None):
        self.calls.append({
            "url": url,
            "stream": stream,
            "timeout": timeout,
        })
        return self.responses.pop(0)


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
            "rtsp://192.168.1.10/Streaming/tracks/101?starttime=2026-04-03T03:35:00Z&endtime=2026-04-03T03:40:00Z&name=ch01_07010000064000100&size=89992380",
        )

    def test_download_recording_uses_ffmpeg_with_authenticated_playback_uri(self):
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

        with mock.patch.object(service, "_download_recording_via_isapi", return_value=False), mock.patch("app.services.hikvision_camera.subprocess.run") as run_mock:
            run_mock.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

            ok = service.download_recording(
                "rtsp://192.168.1.10/Streaming/tracks/101?starttime=2026-04-03T03:35:00Z&endtime=2026-04-03T03:40:00Z&name=ch01_07010000064000100&size=89992380",
                "/tmp/hikvision-test.mp4",
            )

        self.assertTrue(ok)
        cmd = run_mock.call_args.args[0]
        self.assertEqual(cmd[0], "ffmpeg")
        self.assertIn("-rtsp_transport", cmd)
        self.assertIn("rtsp://admin:secret@192.168.1.10/Streaming/tracks/101/?starttime=20260403T033500Z&endtime=20260403T034000Z&name=ch01_07010000064000100&size=89992380", cmd)

    def test_build_playback_url_keeps_compact_hikvision_timestamp(self):
        service = HikvisionCameraService({
            "HIKVISION_CAMERAS": {
                "1": {
                    "host": "192.168.1.10",
                    "port": 80,
                    "username": "admin",
                    "password": "secret",
                },
            },
        })

        playback_url = service._build_playback_url(
            "1",
            "rtsp://192.168.1.10/Streaming/tracks/101/?starttime=20260403T033500Z&endtime=20260403T034000Z&name=abc",
        )

        self.assertEqual(
            playback_url,
            "rtsp://admin:secret@192.168.1.10/Streaming/tracks/101/?starttime=20260403T033500Z&endtime=20260403T034000Z&name=abc",
        )

    def test_download_recording_retries_without_name_and_size(self):
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

        with mock.patch.object(service, "_download_recording_via_isapi", return_value=False), mock.patch("app.services.hikvision_camera.subprocess.run") as run_mock:
            run_mock.side_effect = [
                subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="Invalid data found when processing input"),
                subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            ]

            ok = service.download_recording(
                "rtsp://192.168.1.10/Streaming/tracks/101?starttime=2026-04-03T03:35:00Z&endtime=2026-04-03T03:40:00Z&name=ch01_07010000064000100&size=89992380",
                "/tmp/hikvision-test.mp4",
            )

        self.assertTrue(ok)
        self.assertEqual(run_mock.call_count, 2)
        first_cmd = run_mock.call_args_list[0].args[0]
        second_cmd = run_mock.call_args_list[1].args[0]
        self.assertIn("rtsp://admin:secret@192.168.1.10/Streaming/tracks/101/?starttime=20260403T033500Z&endtime=20260403T034000Z&name=ch01_07010000064000100&size=89992380", first_cmd)
        self.assertIn("rtsp://admin:secret@192.168.1.10/Streaming/tracks/101/?starttime=20260403T033500Z&endtime=20260403T034000Z", second_cmd)

    def test_download_recording_prefers_isapi_http_download(self):
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
        service._sessions["1"] = _FakeDownloadSession([
            _FakeDownloadResponse(
                chunks=[b"ftyp", b"video-data"],
                headers={"Content-Type": "video/mp4"},
            ),
        ])

        with tempfile.TemporaryDirectory() as temp_dir, mock.patch("app.services.hikvision_camera.subprocess.run") as run_mock:
            output_path = os.path.join(temp_dir, "hikvision-http.mp4")
            ok = service.download_recording(
                "rtsp://192.168.1.10/Streaming/tracks/101?starttime=20260403T033500Z&endtime=20260403T034000Z&name=abc&size=123",
                output_path,
            )

            self.assertTrue(ok)
            self.assertFalse(run_mock.called)
            with open(output_path, "rb") as saved_file:
                self.assertEqual(saved_file.read(), b"ftypvideo-data")

        self.assertEqual(len(service._sessions["1"].calls), 1)
        self.assertEqual(
            service._sessions["1"].calls[0]["url"],
            "http://192.168.1.10:80/ISAPI/ContentMgmt/download?startTime=2026-04-03T11%3A35%3A00&endTime=2026-04-03T11%3A40%3A00&channelID=1",
        )

    def test_download_recording_falls_back_to_ffmpeg_after_isapi_failure(self):
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
        service._sessions["1"] = _FakeDownloadSession([
            _FakeDownloadResponse(
                chunks=[b"<?xml version=\"1.0\"?><status>Error</status>"],
                headers={"Content-Type": "application/xml"},
            ),
            _FakeDownloadResponse(
                chunks=[b"<?xml version=\"1.0\"?><status>Error</status>"],
                headers={"Content-Type": "application/xml"},
            ),
        ])

        with tempfile.TemporaryDirectory() as temp_dir, mock.patch("app.services.hikvision_camera.subprocess.run") as run_mock:
            run_mock.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            output_path = os.path.join(temp_dir, "hikvision-fallback.mp4")
            ok = service.download_recording(
                "rtsp://192.168.1.10/Streaming/tracks/101?starttime=20260403T033500Z&endtime=20260403T034000Z&name=abc&size=123",
                output_path,
            )

        self.assertTrue(ok)
        self.assertEqual(run_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
