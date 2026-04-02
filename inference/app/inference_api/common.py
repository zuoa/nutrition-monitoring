import json
import os
import tempfile
import time
from functools import wraps
from typing import Any

from flask import current_app, request

from app.utils.http import api_error, api_ok


def internal_token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        configured = str(current_app.config.get("INFERENCE_API_TOKEN", "") or "").strip()
        if configured:
            header = request.headers.get("Authorization", "")
            token = header[7:] if header.startswith("Bearer ") else request.headers.get("X-Internal-Token", "")
            if token != configured:
                return api_error("未授权", 401)
        return f(*args, **kwargs)

    return decorated


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def load_request_payload() -> tuple[dict[str, Any], str, bool]:
    if not (request.content_type and request.content_type.startswith("multipart/form-data")):
        raise ValueError("推理服务仅支持 multipart/form-data image_file 上传")

    payload = request.form.to_dict(flat=True)
    file_storage = request.files.get("image_file")
    if not file_storage or not file_storage.filename:
        raise ValueError("请提供 image_file")

    suffix = os.path.splitext(file_storage.filename or "")[1] or ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        file_storage.save(tmp.name)
        image_path = tmp.name
    return payload, image_path, True


def parse_bboxes(value: Any) -> list[dict[str, int]]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        raise ValueError("bboxes 必须是数组")
    parsed = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("bbox 项必须是对象")
        parsed.append({
            "x1": int(round(float(item["x1"]))),
            "y1": int(round(float(item["y1"]))),
            "x2": int(round(float(item["x2"]))),
            "y2": int(round(float(item["y2"]))),
        })
    return parsed


def parse_candidate_dishes(value: Any) -> list[dict[str, Any]]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        raise ValueError("candidate_dishes 必须是数组")
    normalized = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("candidate_dishes 项必须是对象")
        normalized.append({
            "id": item.get("id"),
            "name": str(item.get("name") or "").strip(),
            "description": str(item.get("description") or "").strip(),
            "structured_description": item.get("structured_description"),
        })
    return normalized


def timed_call(fn, *args, **kwargs):
    started = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed_ms = int(round((time.perf_counter() - started) * 1000))
    return result, elapsed_ms


__all__ = [
    "api_error",
    "api_ok",
    "internal_token_required",
    "load_request_payload",
    "parse_bboxes",
    "parse_bool",
    "parse_candidate_dishes",
    "timed_call",
]
