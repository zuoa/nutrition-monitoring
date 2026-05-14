import os
import sys
import types
import unittest
import xml.etree.ElementTree as ET
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
    def __init__(self, *, content: bytes = b"", content_type: str = "image/jpeg"):
        self.content = content
        self.headers = {"Content-Type": content_type}
        self.status_code = 200

    def raise_for_status(self):
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
            "http://10.0.4.100:80/ISAPI/Streaming/channels/201/picture",
            params=None,
            timeout=15,
        )


if __name__ == "__main__":
    unittest.main()
