import logging
import os
import threading
from datetime import date, datetime
from typing import Any

import requests
from celery_app import celery
from huggingface_hub import snapshot_download

from app import db
from app.models import TaskLog
from app.services.local_model_manager import get_local_model_spec

logger = logging.getLogger(__name__)

DEFAULT_HF_ENDPOINT = "https://huggingface.co"
PROGRESS_UPDATE_INTERVAL_SECONDS = 2.0
TASK_RUNNING_STATUS = "running"
TASK_SUCCESS_STATUS = "success"
TASK_FAILED_STATUS = "failed"
_UNSET = object()


def _format_size(num_bytes: int | None) -> str:
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


def _normalize_hf_endpoint(endpoint: str | None) -> str:
    normalized = (endpoint or "").strip().rstrip("/")
    return normalized or DEFAULT_HF_ENDPOINT


def _fetch_repo_manifest(repo_id: str, hf_endpoint: str | None = None) -> dict[str, Any]:
    endpoint = _normalize_hf_endpoint(hf_endpoint)
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


def _collect_download_progress(target_path: str, manifest: dict[str, Any]) -> dict[str, Any]:
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
            f"已下载 {_format_size(downloaded_bytes)} / {_format_size(total_bytes)} "
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


def _update_task_log(
    app,
    task_log_id: int,
    *,
    status: str | object = _UNSET,
    total_count: int | object = _UNSET,
    success_count: int | object = _UNSET,
    error_count: int | object = _UNSET,
    error_message: str | None | object = _UNSET,
    finished_at: datetime | None | object = _UNSET,
    meta_updates: dict[str, Any] | None = None,
) -> None:
    with app.app_context():
        db.session.remove()
        try:
            task_log = db.session.get(TaskLog, task_log_id)
            if not task_log:
                return

            if status is not _UNSET:
                task_log.status = status
            if total_count is not _UNSET:
                task_log.total_count = int(total_count)
            if success_count is not _UNSET:
                task_log.success_count = int(success_count)
            if error_count is not _UNSET:
                task_log.error_count = int(error_count)
            if error_message is not _UNSET:
                task_log.error_message = error_message
            if finished_at is not _UNSET:
                task_log.finished_at = finished_at
            if meta_updates:
                merged_meta = dict(task_log.meta or {})
                merged_meta.update(meta_updates)
                task_log.meta = merged_meta

            db.session.commit()
        finally:
            db.session.remove()


def _monitor_download_progress(
    app,
    task_log_id: int,
    target_path: str,
    manifest: dict[str, Any],
    stop_event: threading.Event,
) -> None:
    last_snapshot: dict[str, Any] | None = None
    while not stop_event.wait(PROGRESS_UPDATE_INTERVAL_SECONDS):
        snapshot = _collect_download_progress(target_path, manifest)
        # Avoid excessive writes if the visible progress hasn't changed.
        if snapshot == last_snapshot:
            continue
        last_snapshot = snapshot
        _update_task_log(
            app,
            task_log_id,
            status=TASK_RUNNING_STATUS,
            total_count=snapshot["total_files"],
            success_count=snapshot["downloaded_files"],
            meta_updates=snapshot,
        )


@celery.task(
    name="app.tasks.local_models.download_local_model",
    bind=True,
    max_retries=0,
    soft_time_limit=3600,
    time_limit=7200,
)
def download_local_model(self, model_type: str, variant: str = "2B"):
    from flask import current_app

    config = current_app.config
    app = current_app._get_current_object()
    spec = get_local_model_spec(config, model_type, variant=variant)
    target_path = spec["path"]
    repo_id = spec["repo_id"]
    hf_endpoint = (config.get("HF_ENDPOINT") or "").strip()
    manifest = _fetch_repo_manifest(repo_id, hf_endpoint=hf_endpoint)

    task_log = TaskLog(
        task_type="local_model_download",
        task_date=date.today(),
        meta={
            "model_type": model_type,
            "variant": spec["variant"],
            "repo_id": repo_id,
            "target_path": target_path,
            "hf_endpoint": hf_endpoint or DEFAULT_HF_ENDPOINT,
            "progress_percent": 0.0,
            "downloaded_bytes": 0,
            "total_bytes": int(manifest.get("total_bytes") or 0),
            "downloaded_files": 0,
            "total_files": int(manifest.get("total_files") or 0),
            "status_text": "等待下载开始",
        },
    )
    db.session.add(task_log)
    db.session.commit()
    task_log_id = task_log.id

    stop_event = threading.Event()
    progress_thread = threading.Thread(
        target=_monitor_download_progress,
        args=(app, task_log_id, target_path, manifest, stop_event),
        daemon=True,
    )
    try:
        os.makedirs(target_path, exist_ok=True)
        initial_snapshot = _collect_download_progress(target_path, manifest)
        _update_task_log(
            app,
            task_log_id,
            status=TASK_RUNNING_STATUS,
            total_count=initial_snapshot["total_files"],
            success_count=initial_snapshot["downloaded_files"],
            meta_updates={
                **initial_snapshot,
                "status_text": "正在连接模型源并开始下载",
                "hf_endpoint": hf_endpoint or DEFAULT_HF_ENDPOINT,
            },
        )
        progress_thread.start()
        snapshot_download(
            repo_id=repo_id,
            local_dir=target_path,
            resume_download=True,
            endpoint=hf_endpoint or None,
        )
        stop_event.set()
        progress_thread.join(timeout=5)
        final_snapshot = _collect_download_progress(target_path, manifest)
        if final_snapshot["total_files"] > 0:
            final_snapshot["downloaded_files"] = final_snapshot["total_files"]
        if final_snapshot["total_bytes"] > 0:
            final_snapshot["downloaded_bytes"] = final_snapshot["total_bytes"]
        final_snapshot["progress_percent"] = 100.0
        final_snapshot["status_text"] = "模型下载完成"
        _update_task_log(
            app,
            task_log_id,
            status=TASK_SUCCESS_STATUS,
            total_count=max(final_snapshot["total_files"], 1),
            success_count=max(final_snapshot["downloaded_files"], 1),
            finished_at=datetime.utcnow(),
            meta_updates={**final_snapshot, "hf_endpoint": hf_endpoint or DEFAULT_HF_ENDPOINT},
        )
        return {
            "model_type": model_type,
            "variant": spec["variant"],
            "repo_id": repo_id,
            "target_path": target_path,
        }
    except Exception as e:
        stop_event.set()
        if progress_thread.is_alive():
            progress_thread.join(timeout=5)
        failed_snapshot = _collect_download_progress(target_path, manifest)
        failed_snapshot["status_text"] = "模型下载失败"
        logger.error("Failed to download local model %s from %s: %s", model_type, repo_id, e, exc_info=True)
        _update_task_log(
            app,
            task_log_id,
            status=TASK_FAILED_STATUS,
            total_count=failed_snapshot["total_files"],
            success_count=failed_snapshot["downloaded_files"],
            error_count=1,
            error_message=str(e)[:1000],
            finished_at=datetime.utcnow(),
            meta_updates={**failed_snapshot, "hf_endpoint": hf_endpoint or DEFAULT_HF_ENDPOINT},
        )
        raise
