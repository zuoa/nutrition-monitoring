import logging
import time
from datetime import date, datetime
from typing import Any

from celery_app import celery

from app import db
from app.models import TaskLog
from app.services.inference_client import (
    InferenceServiceError,
    make_retrieval_client,
    make_retrieval_control_client,
)
from app.services.local_model_manager import get_local_model_spec
from app.services.model_downloads import (
    DEFAULT_HF_ENDPOINT,
    fetch_repo_manifest,
    run_snapshot_download_with_progress,
)
from app.services.model_management import is_retrieval_api_model_management
from app.services.runtime_config import get_effective_config

logger = logging.getLogger(__name__)

REMOTE_STATUS_POLL_INTERVAL_SECONDS = 2.0
REMOTE_STATUS_RETRYABLE_ERROR_CODES = {404, 502, 503, 504}
REMOTE_STATUS_ERROR_GRACE_SECONDS = 180.0
TASK_PENDING_STATUS = "pending"
TASK_RUNNING_STATUS = "running"
TASK_SUCCESS_STATUS = "success"
TASK_FAILED_STATUS = "failed"
_UNSET = object()


def _is_retryable_remote_status_error(exc: Exception) -> bool:
    if not isinstance(exc, InferenceServiceError):
        return False
    return int(getattr(exc, "status_code", 0) or 0) in REMOTE_STATUS_RETRYABLE_ERROR_CODES


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


def _mirror_remote_model_download(
    app,
    task_log_id: int,
    config: dict[str, Any],
    *,
    model_type: str,
    variant: str | None,
) -> dict[str, Any]:
    client = make_retrieval_client(config)
    status_client = make_retrieval_control_client(config)
    remote_task = client.post_json(
        "/v1/models/download",
        {
            "model_type": model_type,
            "variant": variant,
        },
    )
    remote_task_id = str(remote_task.get("task_id") or "").strip()
    if not remote_task_id:
        raise RuntimeError("远程下载任务未返回 task_id")

    _update_task_log(
        app,
        task_log_id,
        status=TASK_PENDING_STATUS,
        total_count=int(remote_task.get("total_files") or 0),
        success_count=int(remote_task.get("downloaded_files") or 0),
        meta_updates={
            **remote_task,
            "remote_task_id": remote_task_id,
            "execution_target": "retrieval-api",
        },
    )

    transient_error_since: float | None = None
    while True:
        try:
            snapshot = status_client.get_json(f"/v1/models/download/{remote_task_id}")
            transient_error_since = None
        except Exception as exc:
            if _is_retryable_remote_status_error(exc):
                now = time.monotonic()
                if transient_error_since is None:
                    transient_error_since = now
                if (now - transient_error_since) <= REMOTE_STATUS_ERROR_GRACE_SECONDS:
                    _update_task_log(
                        app,
                        task_log_id,
                        meta_updates={
                            "remote_task_id": remote_task_id,
                            "execution_target": "retrieval-api",
                            "status_text": "等待 retrieval-api 恢复后继续同步下载进度",
                            "last_poll_error": str(exc)[:1000],
                        },
                    )
                    time.sleep(REMOTE_STATUS_POLL_INTERVAL_SECONDS)
                    continue
            raise

        remote_status = str(snapshot.get("status") or TASK_RUNNING_STATUS)
        finished_at = datetime.utcnow() if remote_status in {TASK_SUCCESS_STATUS, TASK_FAILED_STATUS} else _UNSET
        _update_task_log(
            app,
            task_log_id,
            status=remote_status if remote_status in {TASK_PENDING_STATUS, TASK_RUNNING_STATUS, TASK_SUCCESS_STATUS, TASK_FAILED_STATUS} else TASK_RUNNING_STATUS,
            total_count=int(snapshot.get("total_files") or 0),
            success_count=int(snapshot.get("downloaded_files") or 0),
            error_count=1 if remote_status == TASK_FAILED_STATUS else 0,
            error_message=str(snapshot.get("error_message") or "")[:1000] if remote_status == TASK_FAILED_STATUS else None,
            finished_at=finished_at,
            meta_updates={
                **snapshot,
                "remote_task_id": remote_task_id,
                "execution_target": "retrieval-api",
                "last_poll_error": "",
            },
        )
        if remote_status == TASK_SUCCESS_STATUS:
            return {
                "model_type": model_type,
                "variant": snapshot.get("variant"),
                "repo_id": snapshot.get("repo_id"),
                "target_path": snapshot.get("target_path"),
                "remote_task_id": remote_task_id,
            }
        if remote_status == TASK_FAILED_STATUS:
            raise RuntimeError(str(snapshot.get("error_message") or "远程模型下载失败"))
        time.sleep(REMOTE_STATUS_POLL_INTERVAL_SECONDS)


@celery.task(
    name="app.tasks.local_models.download_local_model",
    bind=True,
    max_retries=0,
    soft_time_limit=3600,
    time_limit=7200,
)
def download_local_model(self, model_type: str, variant: str | None = "2B"):
    from flask import current_app

    config = get_effective_config(current_app.config)
    app = current_app._get_current_object()
    spec = get_local_model_spec(config, model_type, variant=variant or None)
    target_path = spec["path"]
    repo_id = spec["repo_id"]
    hf_endpoint = (config.get("HF_ENDPOINT") or "").strip()
    manifest = fetch_repo_manifest(repo_id, hf_endpoint=hf_endpoint)

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
            "execution_target": "retrieval-api" if is_retrieval_api_model_management(config) else "local",
        },
    )
    db.session.add(task_log)
    db.session.commit()
    task_log_id = task_log.id

    try:
        if is_retrieval_api_model_management(config):
            return _mirror_remote_model_download(
                app,
                task_log_id,
                config,
                model_type=model_type,
                variant=spec["variant"] or None,
            )

        _update_task_log(
            app,
            task_log_id,
            status=TASK_RUNNING_STATUS,
            total_count=int(manifest.get("total_files") or 0),
            success_count=0,
            meta_updates={
                "hf_endpoint": hf_endpoint or DEFAULT_HF_ENDPOINT,
                "execution_target": "local",
            },
        )

        def _on_progress(snapshot: dict[str, Any]) -> None:
            status_text = str(snapshot.get("status_text") or "")
            status = TASK_RUNNING_STATUS
            finished_at: datetime | object = _UNSET
            if status_text == "模型下载完成":
                status = TASK_SUCCESS_STATUS
                finished_at = datetime.utcnow()
            elif status_text == "模型下载失败":
                status = TASK_FAILED_STATUS
                finished_at = datetime.utcnow()
            _update_task_log(
                app,
                task_log_id,
                status=status,
                total_count=int(snapshot.get("total_files") or 0),
                success_count=int(snapshot.get("downloaded_files") or 0),
                finished_at=finished_at,
                meta_updates={**snapshot, "hf_endpoint": hf_endpoint or DEFAULT_HF_ENDPOINT, "execution_target": "local"},
            )

        _, final_snapshot = run_snapshot_download_with_progress(
            repo_id=repo_id,
            target_path=target_path,
            hf_endpoint=hf_endpoint,
            manifest=manifest,
            progress_callback=_on_progress,
        )
        _update_task_log(
            app,
            task_log_id,
            status=TASK_SUCCESS_STATUS,
            total_count=max(int(final_snapshot.get("total_files") or 0), 1),
            success_count=max(int(final_snapshot.get("downloaded_files") or 0), 1),
            finished_at=datetime.utcnow(),
            meta_updates={**final_snapshot, "hf_endpoint": hf_endpoint or DEFAULT_HF_ENDPOINT, "execution_target": "local"},
        )
        return {
            "model_type": model_type,
            "variant": spec["variant"],
            "repo_id": repo_id,
            "target_path": target_path,
        }
    except Exception as e:
        logger.error("Failed to download local model %s from %s: %s", model_type, repo_id, e, exc_info=True)
        _update_task_log(
            app,
            task_log_id,
            status=TASK_FAILED_STATUS,
            error_count=1,
            error_message=str(e)[:1000],
            finished_at=datetime.utcnow(),
            meta_updates={"status_text": "模型下载失败"},
        )
        raise
