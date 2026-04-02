import json
import os
from typing import Any

import requests


class InferenceServiceError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502):
        super().__init__(message)
        self.status_code = int(status_code)


def _resolve_timeout(config: dict[str, Any], default_key: str, fallback: int, override: int | None = None) -> int:
    if override is not None:
        return max(int(override), 1)
    return max(int(config.get(default_key, fallback) or fallback), 1)


class InferenceServiceClient:
    def __init__(self, base_url: str, *, token: str = "", timeout: int = 180):
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        self.timeout = int(timeout)

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = self._headers()
        try:
            response = requests.get(
                f"{self.base_url}{path}",
                params=params,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.Timeout as e:
            raise InferenceServiceError("推理服务请求超时", status_code=504) from e
        except requests.RequestException as e:
            raise InferenceServiceError(f"推理服务不可用: {str(e)}", status_code=502) from e
        return self._unwrap(response)

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = self._headers()
        try:
            response = requests.post(
                f"{self.base_url}{path}",
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.Timeout as e:
            raise InferenceServiceError("推理服务请求超时", status_code=504) from e
        except requests.RequestException as e:
            raise InferenceServiceError(f"推理服务不可用: {str(e)}", status_code=502) from e
        return self._unwrap(response)

    def post_file(
        self,
        path: str,
        *,
        image_path: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not image_path or not os.path.exists(image_path):
            raise InferenceServiceError("图片文件不存在")
        payload = {}
        for key, value in (data or {}).items():
            if value is None:
                continue
            if isinstance(value, (dict, list)):
                payload[key] = json.dumps(value, ensure_ascii=False)
            else:
                payload[key] = str(value)

        headers = self._headers(include_content_type=False)
        with open(image_path, "rb") as image_file:
            try:
                response = requests.post(
                    f"{self.base_url}{path}",
                    data=payload,
                    files={"image_file": (os.path.basename(image_path), image_file)},
                    headers=headers,
                    timeout=self.timeout,
                )
            except requests.Timeout as e:
                raise InferenceServiceError("推理服务请求超时", status_code=504) from e
            except requests.RequestException as e:
                raise InferenceServiceError(f"推理服务不可用: {str(e)}", status_code=502) from e
        return self._unwrap(response)

    def post_form_files(
        self,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        file_paths: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        payload = {}
        for key, value in (data or {}).items():
            if value is None:
                continue
            if isinstance(value, (dict, list)):
                payload[key] = json.dumps(value, ensure_ascii=False)
            else:
                payload[key] = str(value)

        headers = self._headers(include_content_type=False)
        files = {}
        handles = []
        try:
            for field_name, file_path in (file_paths or {}).items():
                if not file_path or not os.path.exists(file_path):
                    raise InferenceServiceError(f"文件不存在: {field_name}")
                handle = open(file_path, "rb")
                handles.append(handle)
                files[field_name] = (os.path.basename(file_path), handle)

            try:
                response = requests.post(
                    f"{self.base_url}{path}",
                    data=payload,
                    files=files,
                    headers=headers,
                    timeout=self.timeout,
                )
            except requests.Timeout as e:
                raise InferenceServiceError("推理服务请求超时", status_code=504) from e
            except requests.RequestException as e:
                raise InferenceServiceError(f"推理服务不可用: {str(e)}", status_code=502) from e
            return self._unwrap(response)
        finally:
            for handle in handles:
                try:
                    handle.close()
                except OSError:
                    pass

    def _headers(self, *, include_content_type: bool = True) -> dict[str, str]:
        headers: dict[str, str] = {}
        if include_content_type:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _unwrap(self, response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as e:
            raise InferenceServiceError(
                f"推理服务返回无效响应: {response.text[:200]}",
                status_code=response.status_code if response.status_code >= 400 else 502,
            ) from e

        if response.status_code >= 400 or payload.get("code", 0) != 0:
            message = payload.get("message") or f"推理服务错误: {response.status_code}"
            raise InferenceServiceError(
                str(message),
                status_code=response.status_code if response.status_code >= 400 else 502,
            )
        return payload.get("data") or {}


def make_detector_client(config: dict[str, Any], *, timeout: int | None = None) -> InferenceServiceClient:
    return InferenceServiceClient(
        str(config.get("DETECTOR_API_BASE_URL", "http://detector-api:5000") or "http://detector-api:5000"),
        token=str(config.get("INFERENCE_API_TOKEN", "") or ""),
        timeout=_resolve_timeout(config, "INFERENCE_API_TIMEOUT", 180, override=timeout),
    )


def make_retrieval_client(config: dict[str, Any], *, timeout: int | None = None) -> InferenceServiceClient:
    return InferenceServiceClient(
        str(config.get("RETRIEVAL_API_BASE_URL", "http://retrieval-api:5000") or "http://retrieval-api:5000"),
        token=str(config.get("INFERENCE_API_TOKEN", "") or ""),
        timeout=_resolve_timeout(config, "INFERENCE_API_TIMEOUT", 180, override=timeout),
    )


def make_retrieval_control_client(config: dict[str, Any], *, timeout: int | None = None) -> InferenceServiceClient:
    return InferenceServiceClient(
        str(config.get("RETRIEVAL_API_BASE_URL", "http://retrieval-api:5000") or "http://retrieval-api:5000"),
        token=str(config.get("INFERENCE_API_TOKEN", "") or ""),
        timeout=_resolve_timeout(config, "INFERENCE_CONTROL_TIMEOUT", 3, override=timeout),
    )
