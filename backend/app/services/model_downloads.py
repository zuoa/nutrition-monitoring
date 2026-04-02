import logging
import os
import threading
from typing import Any, Callable

import requests
from huggingface_hub import snapshot_download

logger = logging.getLogger(__name__)

DEFAULT_HF_ENDPOINT = "https://huggingface.co"
PROGRESS_UPDATE_INTERVAL_SECONDS = 2.0


def format_size(num_bytes: int | None) -> str:
    if not num_bytes or num_bytes <= 0:
        return "0 B"

    value = float(num_bytes)
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{num_bytes} B"


def normalize_hf_endpoint(endpoint: str | None) -> str:
    normalized = (endpoint or "").strip().rstrip("/")
    return normalized or DEFAULT_HF_ENDPOINT


def fetch_repo_manifest(repo_id: str, hf_endpoint: str | None = None) -> dict[str, Any]:
    endpoint = normalize_hf_endpoint(hf_endpoint)
    try:
        response = requests.get(
            f"{endpoint}/api/models/{repo_id}",
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning("Failed to fetch Hugging Face repo manifest for %s: %s", repo_id, exc)
        return {
            "files": [],
            "total_files": 0,
            "total_bytes": 0,
        }

    files: list[dict[str, Any]] = []
    total_bytes = 0
    for sibling in payload.get("siblings") or []:
        relative_path = sibling.get("rfilename")
        if not relative_path:
            continue
        size = sibling.get("size")
        if not isinstance(size, (int, float)):
            size = (sibling.get("lfs") or {}).get("size")
        normalized_size = max(int(size or 0), 0)
        total_bytes += normalized_size
        files.append({
            "path": relative_path,
            "size": normalized_size,
        })

    return {
        "files": files,
        "total_files": len(files),
        "total_bytes": total_bytes,
    }


def collect_download_progress(target_path: str, manifest: dict[str, Any]) -> dict[str, Any]:
    files = manifest.get("files") or []
    total_files = int(manifest.get("total_files") or len(files) or 0)
    total_bytes = int(manifest.get("total_bytes") or 0)

    downloaded_files = 0
    downloaded_bytes = 0
    for item in files:
        relative_path = item.get("path")
        if not relative_path:
            continue
        local_path = os.path.join(target_path, relative_path)
        if not os.path.isfile(local_path):
            continue
        downloaded_files += 1
        try:
            downloaded_bytes += os.path.getsize(local_path)
        except OSError:
            continue

    progress_percent = 0.0
    if total_bytes > 0:
        progress_percent = (downloaded_bytes / total_bytes) * 100
    elif total_files > 0:
        progress_percent = (downloaded_files / total_files) * 100

    progress_percent = max(0.0, min(progress_percent, 100.0))

    if total_bytes > 0:
        status_text = (
            f"已下载 {format_size(downloaded_bytes)} / {format_size(total_bytes)} "
            f"({downloaded_files}/{total_files} 个文件)"
        )
    elif total_files > 0:
        status_text = f"已完成 {downloaded_files}/{total_files} 个文件"
    else:
        status_text = "正在下载模型文件"

    return {
        "total_files": total_files,
        "downloaded_files": downloaded_files,
        "total_bytes": total_bytes,
        "downloaded_bytes": downloaded_bytes,
        "progress_percent": round(progress_percent, 1),
        "status_text": status_text,
    }


def run_snapshot_download_with_progress(
    *,
    repo_id: str,
    target_path: str,
    hf_endpoint: str | None = None,
    manifest: dict[str, Any] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    resolved_manifest = manifest or fetch_repo_manifest(repo_id, hf_endpoint=hf_endpoint)
    stop_event = threading.Event()

    def emit(snapshot: dict[str, Any]) -> None:
        if progress_callback is not None:
            progress_callback(dict(snapshot))

    def monitor() -> None:
        last_snapshot: dict[str, Any] | None = None
        while not stop_event.wait(PROGRESS_UPDATE_INTERVAL_SECONDS):
            snapshot = collect_download_progress(target_path, resolved_manifest)
            if snapshot == last_snapshot:
                continue
            last_snapshot = snapshot
            emit(snapshot)

    progress_thread = threading.Thread(target=monitor, daemon=True)

    try:
        os.makedirs(target_path, exist_ok=True)
        initial_snapshot = collect_download_progress(target_path, resolved_manifest)
        emit({
            **initial_snapshot,
            "status_text": "正在连接模型源并开始下载",
            "hf_endpoint": hf_endpoint or DEFAULT_HF_ENDPOINT,
        })
        progress_thread.start()
        snapshot_download(
            repo_id=repo_id,
            local_dir=target_path,
            resume_download=True,
            endpoint=hf_endpoint or None,
        )
        stop_event.set()
        progress_thread.join(timeout=5)
        final_snapshot = collect_download_progress(target_path, resolved_manifest)
        if final_snapshot["total_files"] > 0:
            final_snapshot["downloaded_files"] = final_snapshot["total_files"]
        if final_snapshot["total_bytes"] > 0:
            final_snapshot["downloaded_bytes"] = final_snapshot["total_bytes"]
        final_snapshot["progress_percent"] = 100.0
        final_snapshot["status_text"] = "模型下载完成"
        emit({**final_snapshot, "hf_endpoint": hf_endpoint or DEFAULT_HF_ENDPOINT})
        return resolved_manifest, final_snapshot
    except Exception:
        stop_event.set()
        if progress_thread.is_alive():
            progress_thread.join(timeout=5)
        failed_snapshot = collect_download_progress(target_path, resolved_manifest)
        failed_snapshot["status_text"] = "模型下载失败"
        emit({**failed_snapshot, "hf_endpoint": hf_endpoint or DEFAULT_HF_ENDPOINT})
        raise
