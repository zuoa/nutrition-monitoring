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

    def test_list_channels_merges_streaming_stream_id(self):
        service = self._service()
        input_proxy_root = ET.fromstring("""
        <InputProxyChannelList xmlns="http://www.hikvision.com/ver20/XMLSchema">
          <InputProxyChannel>
            <id>33</id>
            <name>结算台</name>
          </InputProxyChannel>
        </InputProxyChannelList>
        """)
        video_input_root = ET.fromstring("<VideoInputChannelList />")
        streaming_root = ET.fromstring("""
        <StreamingChannelList xmlns="http://www.hikvision.com/ver20/XMLSchema">
          <StreamingChannel>
            <id>3301</id>
            <channelName>结算台主码流</channelName>
          </StreamingChannel>
        </StreamingChannelList>
        """)

        with mock.patch.object(
            service,
            "_get_isapi_xml",
            side_effect=[input_proxy_root, video_input_root, streaming_root],
        ):
            channels = service.list_channels()

        self.assertEqual(channels[0]["channel_id"], "33")
        self.assertEqual(channels[0]["name"], "结算台")
        self.assertEqual(channels[0]["stream_id"], "3301")

    def test_list_channels_keeps_name_when_streaming_uses_fallback(self):
        service = self._service()
        input_proxy_root = ET.fromstring("""
        <InputProxyChannelList xmlns="http://www.hikvision.com/ver20/XMLSchema">
          <InputProxyChannel>
            <id>33</id>
            <name>结算台</name>
          </InputProxyChannel>
        </InputProxyChannelList>
        """)
        video_input_root = ET.fromstring("<VideoInputChannelList />")
        streaming_proxy_root = ET.fromstring("<StreamingProxyChannelList />")
        streaming_root = ET.fromstring("""
        <StreamingChannelList xmlns="http://www.hikvision.com/ver20/XMLSchema">
          <StreamingChannel>
            <id>3301</id>
          </StreamingChannel>
        </StreamingChannelList>
        """)

        with mock.patch.object(
            service,
            "_get_isapi_xml",
            side_effect=[input_proxy_root, video_input_root, streaming_proxy_root, streaming_root],
        ):
            channels = service.list_channels()

        self.assertEqual(channels, [{"channel_id": "33", "name": "结算台", "stream_id": "3301"}])

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
        self.assertEqual(
            [call.args[0] for call in get_xml.call_args_list],
            [
                "/ISAPI/ContentMgmt/InputProxy/channels",
                "/ISAPI/System/Video/inputs/channels",
                "/ISAPI/ContentMgmt/StreamingProxy/channels",
                "/ISAPI/Streaming/channels",
            ],
        )

    def test_list_channels_reads_hikvision_streaming_proxy_channels(self):
        service = self._service()
        root = ET.fromstring("""
        <StreamingProxyChannelList xmlns="http://www.hikvision.com/ver20/XMLSchema">
          <StreamingProxyChannel>
            <id>3301</id>
            <channelName>结算台</channelName>
          </StreamingProxyChannel>
        </StreamingProxyChannelList>
        """)

        with mock.patch.object(
            service,
            "_get_isapi_xml",
            side_effect=[ValueError("no input proxy"), ValueError("no video inputs"), root],
        ):
            channels = service.list_channels()

        self.assertEqual(channels, [{"channel_id": "33", "stream_id": "3301", "name": "结算台"}])

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
            side_effect=[ValueError("no input proxy"), ValueError("no video inputs"), ValueError("no streaming proxy"), root],
        ):
            channels = service.list_channels()

        self.assertEqual(
            channels,
            [
                {"channel_id": "1", "stream_id": "101", "name": "一楼"},
                {"channel_id": "2", "stream_id": "201", "name": "二楼"},
            ],
        )

    def test_capture_snapshot_uses_ffmpeg_rtsp_main_stream(self):
        service = self._service()

        with mock.patch("app.services.nvr.capture_rtsp_snapshot", return_value=b"jpeg-bytes") as capture_mock:
            payload = service.capture_snapshot("2")

        self.assertEqual(payload["content"], b"jpeg-bytes")
        self.assertEqual(payload["content_type"], "image/jpeg")
        self.assertEqual(payload["channel_id"], "2")
        capture_mock.assert_called_once_with(
            "rtsp://admin:secret@10.0.4.100:554/Streaming/channels/201",
            ffmpeg_bin="ffmpeg",
            timeout=20,
        )

    def test_capture_snapshot_uses_configured_stream_id_and_rtsp_port(self):
        service = NVRService({
            "NVR_HOST": "10.0.4.100",
            "NVR_PORT": 80,
            "NVR_RTSP_PORT": 8554,
            "NVR_USERNAME": "admin",
            "NVR_PASSWORD": "p@ss word",
            "FFMPEG_BIN": "/usr/local/bin/ffmpeg",
            "SNAPSHOT_TIMEOUT": 12,
            "NVR_CHANNELS": [{"channel_id": "33", "stream_id": "3301"}],
        })

        with mock.patch("app.services.nvr.capture_rtsp_snapshot", return_value=b"jpeg-bytes") as capture_mock:
            payload = service.capture_snapshot("33")

        self.assertEqual(payload["content"], b"jpeg-bytes")
        capture_mock.assert_called_once_with(
            "rtsp://admin:p%40ss%20word@10.0.4.100:8554/Streaming/channels/3301",
            ffmpeg_bin="/usr/local/bin/ffmpeg",
            timeout=12,
        )

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
        self.assertEqual(recordings[0]["filename"], "nvr_ch1_2026-05-14_11-30-00.mp4")
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
