import os
import sys
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET
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

from app.services.nvr import NVRService  # noqa: E402


class _FakeResponse:
    def __init__(self, *, content: bytes = b"", text: str = "", content_type: str = "image/jpeg"):
        self.content = content
        self.text = text
        self.headers = {"Content-Type": content_type}
        self.status_code = 200

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size: int):
        yield self.content

    def close(self):
        return None


class NVRServiceTests(unittest.TestCase):
    def _service(self) -> NVRService:
        return NVRService({
            "NVR_HOST": "10.0.4.100",
            "NVR_PORT": 80,
            "NVR_USERNAME": "admin",
            "NVR_PASSWORD": "secret",
        })

    def test_list_channels_prefers_hikvision_input_proxy_channels(self):
        service = self._service()
        root = ET.fromstring("""
        <InputProxyChannelList xmlns="http://www.hikvision.com/ver20/XMLSchema">
          <InputProxyChannel>
            <id>2</id>
            <name>结算台</name>
          </InputProxyChannel>
          <InputProxyChannel>
            <id>1</id>
            <name>入口</name>
          </InputProxyChannel>
        </InputProxyChannelList>
        """)

        with mock.patch.object(service, "_get_isapi_xml", return_value=root) as get_xml:
            channels = service.list_channels()

        self.assertEqual(
            channels,
            [
                {"channel_id": "1", "name": "入口"},
                {"channel_id": "2", "name": "结算台"},
            ],
        )
        get_xml.assert_called_once_with("/ISAPI/ContentMgmt/InputProxy/channels", timeout=15)

    def test_list_channels_falls_back_to_hikvision_streaming_channels(self):
        service = self._service()
        root = ET.fromstring("""
        <StreamingChannelList xmlns="http://www.hikvision.com/ver20/XMLSchema">
          <StreamingChannel>
            <id>101</id>
            <channelName>一楼</channelName>
          </StreamingChannel>
          <StreamingChannel>
            <id>201</id>
            <channelName>二楼</channelName>
          </StreamingChannel>
        </StreamingChannelList>
        """)

        with mock.patch.object(
            service,
            "_get_isapi_xml",
            side_effect=[ValueError("no input proxy"), ValueError("no video inputs"), root],
        ):
            channels = service.list_channels()

        self.assertEqual(
            channels,
            [
                {"channel_id": "1", "name": "一楼"},
                {"channel_id": "2", "name": "二楼"},
            ],
        )

    def test_capture_snapshot_prefers_hikvision_streaming_picture_endpoint(self):
        service = self._service()
        response = _FakeResponse(content=b"jpeg-bytes")

        with mock.patch.object(service._isapi_session, "get", return_value=response) as get_mock:
            payload = service.capture_snapshot("2")

        self.assertEqual(payload["content"], b"jpeg-bytes")
        self.assertEqual(payload["content_type"], "image/jpeg")
        self.assertEqual(payload["channel_id"], "2")
        get_mock.assert_called_once_with(
            "http://10.0.4.100:80/ISAPI/Streaming/Channels/201/picture",
            params=None,
            timeout=15,
        )

    def test_capture_snapshot_tries_isapi_variants_only(self):
        service = self._service()

        with mock.patch.object(service._isapi_session, "get", side_effect=ValueError("missing")) as get_mock:
            with self.assertRaisesRegex(ValueError, "ISAPI 抓拍失败"):
                service.capture_snapshot("1")

        attempted_urls = [call.args[0] for call in get_mock.call_args_list]
        self.assertEqual(
            attempted_urls,
            [
                "http://10.0.4.100:80/ISAPI/Streaming/Channels/101/picture",
                "http://10.0.4.100:80/ISAPI/Streaming/channels/101/picture",
                "http://10.0.4.100:80/ISAPI/Streaming/Channels/1/picture",
                "http://10.0.4.100:80/ISAPI/Streaming/channels/1/picture",
            ],
        )
        self.assertTrue(all("/api/" not in url for url in attempted_urls))

    def test_list_recordings_uses_hikvision_isapi_search(self):
        service = self._service()
        response = _FakeResponse(text="""
        <CMSearchResult xmlns="http://www.hikvision.com/ver20/XMLSchema">
          <matchList>
            <searchMatchItem>
              <timeSpan>
                <startTime>2026-05-14T03:30:00Z</startTime>
                <endTime>2026-05-14T03:35:00Z</endTime>
              </timeSpan>
              <mediaSegmentDescriptor>
                <playbackURI>rtsp://10.0.4.100/Streaming/tracks/101?starttime=20260514T033000Z</playbackURI>
              </mediaSegmentDescriptor>
            </searchMatchItem>
          </matchList>
        </CMSearchResult>
        """, content_type="application/xml")

        with mock.patch.object(service._isapi_session, "post", return_value=response) as post_mock:
            recordings = service.list_recordings("1", datetime(2026, 5, 14, 11, 30), datetime(2026, 5, 14, 11, 35))

        self.assertEqual(len(recordings), 1)
        self.assertTrue(recordings[0]["download_url"].startswith("rtsp://10.0.4.100/Streaming/tracks/101"))
        post_mock.assert_called_once()
        self.assertEqual(post_mock.call_args.args[0], "http://10.0.4.100:80/ISAPI/ContentMgmt/search")

    def test_download_recording_uses_hikvision_isapi_download(self):
        service = self._service()
        response = _FakeResponse(content=b"video-bytes", content_type="video/mp4")

        with mock.patch.object(service._isapi_session, "request", return_value=response) as request_mock:
            with tempfile.TemporaryDirectory() as tmpdir:
                save_path = os.path.join(tmpdir, "test-nvr.mp4")
                ok = service.download_recording("rtsp://10.0.4.100/Streaming/tracks/101", save_path)
                with open(save_path, "rb") as output_file:
                    self.assertEqual(output_file.read(), b"video-bytes")

        self.assertTrue(ok)
        request_mock.assert_called_once()
        self.assertEqual(request_mock.call_args.args[1], "http://10.0.4.100:80/ISAPI/ContentMgmt/download")


if __name__ == "__main__":
    unittest.main()
