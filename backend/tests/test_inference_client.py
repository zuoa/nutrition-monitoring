import importlib.util
import os
import unittest
from unittest import mock

import requests


MODULE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "app",
    "services",
    "inference_client.py",
)

SPEC = importlib.util.spec_from_file_location("inference_client", MODULE_PATH)
INFERENCE_CLIENT = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(INFERENCE_CLIENT)


class FakeResponse:
    def __init__(self, *, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class InferenceClientTests(unittest.TestCase):
    def test_control_client_uses_control_timeout(self):
        client = INFERENCE_CLIENT.make_retrieval_control_client({
            "RETRIEVAL_API_BASE_URL": "http://retrieval-api:5000",
            "INFERENCE_API_TOKEN": "token",
            "INFERENCE_CONTROL_TIMEOUT": 7,
        })
        self.assertEqual(client.base_url, "http://retrieval-api:5000")
        self.assertEqual(client.token, "token")
        self.assertEqual(client.timeout, 7)

    def test_get_wraps_transport_failures(self):
        client = INFERENCE_CLIENT.InferenceServiceClient("http://retrieval-api:5000", timeout=1)
        with mock.patch("requests.get", side_effect=requests.ConnectionError("connection refused")):
            with self.assertRaises(INFERENCE_CLIENT.InferenceServiceError) as ctx:
                client.get_json("/health/models")
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn("推理服务不可用", str(ctx.exception))

    def test_wraps_transport_failures(self):
        client = INFERENCE_CLIENT.InferenceServiceClient("http://detector-api:5000", timeout=1)
        with mock.patch("requests.post", side_effect=requests.ConnectionError("connection refused")):
            with self.assertRaises(INFERENCE_CLIENT.InferenceServiceError) as ctx:
                client.post_json("/v1/detect", {"mode": "detect"})
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn("推理服务不可用", str(ctx.exception))

    def test_preserves_upstream_status_code(self):
        client = INFERENCE_CLIENT.InferenceServiceClient("http://retrieval-api:5000", timeout=1)
        response = FakeResponse(
            status_code=503,
            payload={"code": 503, "message": "模型未就绪", "data": None},
        )
        with self.assertRaises(INFERENCE_CLIENT.InferenceServiceError) as ctx:
            client._unwrap(response)
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(str(ctx.exception), "模型未就绪")

    def test_invalid_json_uses_response_status(self):
        client = INFERENCE_CLIENT.InferenceServiceClient("http://retrieval-api:5000", timeout=1)
        response = FakeResponse(status_code=502, payload=ValueError("bad json"), text="<html>502</html>")
        with self.assertRaises(INFERENCE_CLIENT.InferenceServiceError) as ctx:
            client._unwrap(response)
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn("推理服务返回无效响应", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
