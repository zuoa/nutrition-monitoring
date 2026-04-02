import logging
import os
import sys
from typing import Any

from app.inference_api.model_download_tasks import (
    hold_remote_download_lock,
    read_remote_download_state_file,
    utcnow_iso,
    write_remote_download_state_file,
)
from app.services.model_downloads import (
    DEFAULT_HF_ENDPOINT,
    fetch_repo_manifest,
    run_snapshot_download_with_progress,
)


logger = logging.getLogger(__name__)


def _update_state(state_path: str, **updates: Any) -> dict[str, Any] | None:
    state = read_remote_download_state_file(state_path)
    if not state:
        return None
    state.update(updates)
    return write_remote_download_state_file(state_path, state)


def run_remote_model_download_worker(state_path: str) -> int:
    state = read_remote_download_state_file(state_path)
    if not state:
        logger.error("Remote model download state file missing: %s", state_path)
        return 1

    if state.get("status") not in {"pending", "running"}:
        return 0

    repo_id = str(state.get("repo_id") or "").strip()
    target_path = str(state.get("target_path") or "").strip()
    hf_endpoint = str(state.get("hf_endpoint") or "").strip()
    if not repo_id or not target_path:
        _update_state(
            state_path,
            status="failed",
            finished_at=utcnow_iso(),
            status_text="模型下载失败",
            error_message="模型下载任务缺少 repo_id 或 target_path",
        )
        return 1

    parent_dir = os.path.dirname(target_path) or "."
    try:
        os.makedirs(parent_dir, exist_ok=True)
    except OSError as exc:
        _update_state(
            state_path,
            status="failed",
            finished_at=utcnow_iso(),
            status_text="模型下载失败",
            error_message=f"无法创建模型目录: {parent_dir} ({exc})"[:1000],
        )
        return 1

    with hold_remote_download_lock(state_path):
        state = read_remote_download_state_file(state_path) or state
        if state.get("status") not in {"pending", "running"}:
            return 0

        manifest = fetch_repo_manifest(repo_id, hf_endpoint=hf_endpoint or DEFAULT_HF_ENDPOINT)

        def _on_progress(snapshot: dict[str, Any]) -> None:
            next_updates: dict[str, Any] = {
                **snapshot,
                "error_message": "",
            }
            if snapshot.get("status_text") == "模型下载完成":
                next_updates["status"] = "success"
                next_updates["finished_at"] = utcnow_iso()
            elif snapshot.get("status_text") == "模型下载失败":
                next_updates["status"] = "failed"
                next_updates["finished_at"] = utcnow_iso()
            else:
                next_updates["status"] = "running"
            _update_state(state_path, **next_updates)

        try:
            _update_state(
                state_path,
                status="running",
                started_at=str(state.get("started_at") or utcnow_iso()),
                finished_at=None,
                error_message="",
                total_bytes=int(manifest.get("total_bytes") or 0),
                total_files=int(manifest.get("total_files") or 0),
                status_text="正在连接模型源并开始下载",
            )
            _, final_snapshot = run_snapshot_download_with_progress(
                repo_id=repo_id,
                target_path=target_path,
                hf_endpoint=hf_endpoint,
                manifest=manifest,
                progress_callback=_on_progress,
            )
            _update_state(
                state_path,
                status="success",
                finished_at=utcnow_iso(),
                error_message="",
                **final_snapshot,
            )
            return 0
        except Exception as exc:
            logger.error("Remote model download worker failed for %s: %s", repo_id, exc, exc_info=True)
            _update_state(
                state_path,
                status="failed",
                finished_at=utcnow_iso(),
                status_text="模型下载失败",
                error_message=str(exc)[:1000],
            )
            return 1


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    if len(args) != 1:
        print("Usage: python -m app.inference_api.model_download_worker <state_path>", file=sys.stderr)
        return 2
    return run_remote_model_download_worker(args[0])


if __name__ == "__main__":
    raise SystemExit(main())
