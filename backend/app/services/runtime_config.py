import json
import os
import tempfile
import threading
from typing import Any, Mapping

from config import DEFAULT_MEAL_SLOTS

try:
    import fcntl
except ImportError:  # Non-POSIX platforms fall back to the in-process lock only.
    fcntl = None

# Serialise read-modify-write cycles inside one process and cache the last
# loaded overrides so hot paths (e.g. sport push callbacks) do not re-read the
# file on every request. The cache key is (mtime_ns, size): the atomic rename
# in persist_runtime_overrides always changes it, including writes from other
# workers sharing the volume.
_persist_lock = threading.Lock()
_overrides_cache: dict[str, Any] = {"path": None, "stamp": None, "value": None}


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
    try:
        stat = os.stat(path)
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        _overrides_cache.update(path=None, stamp=None, value=None)
        return {}

    if _overrides_cache["path"] == path and _overrides_cache["stamp"] == stamp:
        return dict(_overrides_cache["value"])

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

    data = data if isinstance(data, dict) else {}
    _overrides_cache.update(path=path, stamp=stamp, value=dict(data))
    return dict(data)


def get_effective_config(config: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(config)
    overrides = _migrate_meal_slots(load_runtime_overrides(config))
    merged.update(overrides)
    merged["LOCAL_RUNTIME_CONFIG_PATH"] = _runtime_config_path(config)
    return merged


def persist_runtime_overrides(config: Mapping[str, Any], updates: Mapping[str, Any]) -> str:
    """Merge ``updates`` into the shared overrides file.

    A ``None`` value deletes the key, restoring the base (env/app) config for
    that setting. The write is serialised by a file lock (shared with other
    workers on the same volume) and published atomically via rename, so
    concurrent readers never observe a torn file and concurrent saves cannot
    lose each other's keys.
    """
    deletions = [key for key, value in updates.items() if value is None]
    writes = {key: value for key, value in updates.items() if value is not None}
    path = _runtime_config_path(config)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)

    with _persist_lock:
        lock_file = None
        if fcntl is not None:
            try:
                lock_file = open(path + ".lock", "a+")
                fcntl.flock(lock_file, fcntl.LOCK_EX)
            except OSError:
                if lock_file is not None:
                    lock_file.close()
                lock_file = None
        try:
            merged = load_runtime_overrides(config)
            merged.update(writes)
            for key in deletions:
                merged.pop(key, None)

            fd, temporary = tempfile.mkstemp(dir=directory, prefix=".runtime-config-", suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as target:
                    json.dump(merged, target, ensure_ascii=False, indent=2)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        finally:
            _overrides_cache.update(path=None, stamp=None, value=None)
            if lock_file is not None:
                try:
                    fcntl.flock(lock_file, fcntl.LOCK_UN)
                finally:
                    lock_file.close()
    return path
