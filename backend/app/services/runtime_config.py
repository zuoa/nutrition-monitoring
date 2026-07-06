import json
import os
from typing import Any, Mapping

from config import DEFAULT_MEAL_SLOTS


def _runtime_config_path(config: Mapping[str, Any]) -> str:
    path = (config.get("LOCAL_RUNTIME_CONFIG_PATH") or "").strip()
    if path:
        return path
    model_root = (config.get("LOCAL_MODEL_STORAGE_PATH") or "/data/models").strip() or "/data/models"
    return os.path.join(model_root, "runtime_config.json")


def build_meal_slots_from_legacy(
    video_sync_windows: list[dict[str, str]] | None,
    reminder_meal_times: dict[str, str] | None,
) -> list[dict[str, str]]:
    """Build unified MEAL_SLOTS from the deprecated legacy config keys.

    Single source of truth for the legacy→MEAL_SLOTS merge, used both when
    reading runtime overrides and when persisting legacy fields via the API.
    """
    slots = [dict(slot) for slot in DEFAULT_MEAL_SLOTS]
    slot_by_key = {slot["key"]: slot for slot in slots}

    if isinstance(reminder_meal_times, dict):
        for key, time_text in reminder_meal_times.items():
            if key in slot_by_key and isinstance(time_text, str) and time_text.strip():
                slot_by_key[key]["start"] = time_text.strip()

    if isinstance(video_sync_windows, list):
        for index, window in enumerate(video_sync_windows):
            if index >= len(slots):
                break
            if not isinstance(window, dict):
                continue
            start = str(window.get("start") or "").strip()
            end = str(window.get("end") or "").strip()
            if start:
                slots[index]["start"] = start
            if end:
                slots[index]["end"] = end

    return slots


def _migrate_meal_slots(overrides: dict[str, Any]) -> dict[str, Any]:
    """Build MEAL_SLOTS from legacy runtime config keys when needed."""
    if overrides.get("MEAL_SLOTS"):
        return overrides

    legacy_windows = overrides.get("VIDEO_SYNC_MEAL_WINDOWS")
    legacy_times = overrides.get("MENU_REMINDER_MEAL_TIMES")
    if not legacy_windows and not legacy_times:
        return overrides

    merged = dict(overrides)
    merged["MEAL_SLOTS"] = build_meal_slots_from_legacy(legacy_windows, legacy_times)
    return merged


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
    overrides = _migrate_meal_slots(load_runtime_overrides(config))
    merged.update(overrides)
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
