import json
import os
from typing import Any, Mapping


def _runtime_config_path(config: Mapping[str, Any]) -> str:
    path = (config.get("LOCAL_RUNTIME_CONFIG_PATH") or "").strip()
    if path:
        return path
    model_root = (config.get("LOCAL_MODEL_STORAGE_PATH") or "/data/models").strip() or "/data/models"
    return os.path.join(model_root, "runtime_config.json")


def load_runtime_overrides(config: Mapping[str, Any]) -> dict[str, Any]:
    path = _runtime_config_path(config)
    if not os.path.exists(path):
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

    return data if isinstance(data, dict) else {}


def get_effective_config(config: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(config)
    merged.update(load_runtime_overrides(config))
    merged["LOCAL_RUNTIME_CONFIG_PATH"] = _runtime_config_path(config)
    return merged


def persist_runtime_overrides(config: Mapping[str, Any], updates: Mapping[str, Any]) -> str:
    path = _runtime_config_path(config)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    merged = load_runtime_overrides(config)
    merged.update(dict(updates))

    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    return path
