import base64
import hashlib
import json
from typing import Any, Mapping

from cryptography.fernet import Fernet, InvalidToken


def _build_fernet(secret_key: str) -> Fernet:
    digest = hashlib.sha256((secret_key or "").encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_json_payload(payload: Mapping[str, Any], secret_key: str) -> str:
    serialized = json.dumps(dict(payload or {}), ensure_ascii=False, separators=(",", ":"))
    return _build_fernet(secret_key).encrypt(serialized.encode("utf-8")).decode("utf-8")


def decrypt_json_payload(token: str | None, secret_key: str) -> dict[str, Any]:
    if not token:
        return {}

    try:
        raw = _build_fernet(secret_key).decrypt(token.encode("utf-8"))
    except (InvalidToken, ValueError):
        return {}

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}

    return data if isinstance(data, dict) else {}
