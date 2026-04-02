import contextlib
import fcntl
import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any


logger = logging.getLogger(__name__)

REMOTE_MODEL_DOWNLOAD_STATE_DIRNAME = ".remote_model_downloads"
REMOTE_MODEL_DOWNLOAD_ACTIVE_STATUSES = {"pending", "running"}
REMOTE_MODEL_DOWNLOAD_SPAWN_GRACE_SECONDS = 15.0


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso_timestamp(value: Any) -> datetime | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _spawn_requested_recently(state: dict[str, Any]) -> bool:
    launched_at = _parse_iso_timestamp(state.get("last_worker_spawned_at"))
    if not launched_at:
        return False
    now = datetime.now(launched_at.tzinfo or timezone.utc)
    return (now - launched_at).total_seconds() < REMOTE_MODEL_DOWNLOAD_SPAWN_GRACE_SECONDS


def get_remote_download_state_dir(config: dict[str, Any]) -> str:
    storage_root = str(config.get("LOCAL_MODEL_STORAGE_PATH", "/data/models") or "/data/models")
    return os.path.join(storage_root, REMOTE_MODEL_DOWNLOAD_STATE_DIRNAME)


def ensure_remote_download_state_dir(config: dict[str, Any]) -> str:
    state_dir = get_remote_download_state_dir(config)
    os.makedirs(state_dir, exist_ok=True)
    return state_dir


def get_remote_download_state_path(config: dict[str, Any], task_id: str) -> str:
    return os.path.join(ensure_remote_download_state_dir(config), f"{task_id}.json")


def get_remote_download_lock_path_for_state_path(state_path: str) -> str:
    base, _ = os.path.splitext(state_path)
    return f"{base}.lock"


def get_remote_download_launch_lock_path_for_state_path(state_path: str) -> str:
    base, _ = os.path.splitext(state_path)
    return f"{base}.launch.lock"


def read_remote_download_state_file(state_path: str) -> dict[str, Any] | None:
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        logger.warning("Ignoring corrupt remote model download state file: %s", state_path)
        return None
    if not isinstance(payload, dict):
        logger.warning("Ignoring invalid remote model download state payload: %s", state_path)
        return None
    return payload


def read_remote_download_state(config: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    return read_remote_download_state_file(get_remote_download_state_path(config, task_id))


def write_remote_download_state_file(state_path: str, state: dict[str, Any]) -> dict[str, Any]:
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    payload = dict(state)
    payload["updated_at"] = utcnow_iso()
    fd, tmp_path = tempfile.mkstemp(prefix=".remote-model-download-", suffix=".json", dir=os.path.dirname(state_path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, state_path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    return payload


def write_remote_download_state(config: dict[str, Any], task_id: str, state: dict[str, Any]) -> dict[str, Any]:
    return write_remote_download_state_file(get_remote_download_state_path(config, task_id), state)


def list_remote_download_states(config: dict[str, Any]) -> list[dict[str, Any]]:
    state_dir = get_remote_download_state_dir(config)
    if not os.path.isdir(state_dir):
        return []

    states: list[dict[str, Any]] = []
    for entry in sorted(os.listdir(state_dir)):
        if not entry.endswith(".json"):
            continue
        state = read_remote_download_state_file(os.path.join(state_dir, entry))
        if state:
            states.append(state)
    return states


def find_remote_download_state(
    config: dict[str, Any],
    *,
    model_type: str,
    variant: str | None,
) -> dict[str, Any] | None:
    normalized_variant = str(variant or "").strip().upper()
    matches = [
        state
        for state in list_remote_download_states(config)
        if state.get("status") in REMOTE_MODEL_DOWNLOAD_ACTIVE_STATUSES
        and state.get("model_type") == model_type
        and str(state.get("variant") or "").strip().upper() == normalized_variant
    ]
    if not matches:
        return None
    matches.sort(key=lambda state: str(state.get("updated_at") or state.get("created_at") or ""))
    return dict(matches[-1])


@contextlib.contextmanager
def hold_remote_download_lock(state_path: str):
    lock_path = get_remote_download_lock_path_for_state_path(state_path)
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def is_remote_download_worker_active(config: dict[str, Any], task_id: str) -> bool:
    state_path = get_remote_download_state_path(config, task_id)
    lock_path = get_remote_download_lock_path_for_state_path(state_path)
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        else:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            return False


def spawn_remote_download_worker(config: dict[str, Any], task_id: str) -> None:
    state_path = get_remote_download_state_path(config, task_id)
    subprocess.Popen(
        [sys.executable, "-m", "app.inference_api.model_download_worker", state_path],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
        cwd=os.getcwd(),
    )


def ensure_remote_download_worker(config: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    state_path = get_remote_download_state_path(config, task_id)
    launch_lock_path = get_remote_download_launch_lock_path_for_state_path(state_path)
    os.makedirs(os.path.dirname(launch_lock_path), exist_ok=True)

    with open(launch_lock_path, "a+", encoding="utf-8") as launch_lock:
        fcntl.flock(launch_lock.fileno(), fcntl.LOCK_EX)
        state = read_remote_download_state_file(state_path)
        if not state or state.get("status") not in REMOTE_MODEL_DOWNLOAD_ACTIVE_STATUSES:
            return state
        if is_remote_download_worker_active(config, task_id):
            return state
        if _spawn_requested_recently(state):
            return state

        logger.info("Respawning remote model download worker for task %s", task_id)
        state = write_remote_download_state_file(
            state_path,
            {
                **state,
                "last_worker_spawned_at": utcnow_iso(),
            },
        )
        spawn_remote_download_worker(config, task_id)
        return read_remote_download_state_file(state_path) or state
