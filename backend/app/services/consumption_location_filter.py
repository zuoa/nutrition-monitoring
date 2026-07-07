import json

from flask import current_app, has_app_context

from app.models import ConsumptionRecord
from app.services.runtime_config import get_effective_config


ENABLED_TRANSACTION_LOCATION_IDS_KEY = "CONSUMPTION_ENABLED_TRANSACTION_LOCATION_IDS"


def _normalize_location_text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_location_ids(value: object) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        items = []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                items = parsed
        if not items:
            for line in raw.replace("\r", "\n").replace("，", ",").splitlines():
                items.extend(line.split(","))
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        return []

    normalized = []
    seen = set()
    for item in items:
        text = _normalize_location_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def get_enabled_transaction_location_ids(config: dict | None = None) -> list[str]:
    if config is None:
        if not has_app_context():
            return []
        config = get_effective_config(current_app.config)
    return _normalize_location_ids(
        config.get(ENABLED_TRANSACTION_LOCATION_IDS_KEY, [])
    )


def apply_enabled_transaction_location_filter(query, config: dict | None = None):
    location_ids = get_enabled_transaction_location_ids(config)
    if not location_ids:
        return query
    return query.filter(ConsumptionRecord.channel_id.in_(location_ids))
