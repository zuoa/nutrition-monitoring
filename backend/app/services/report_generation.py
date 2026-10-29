"""Persist batch progress independently of existing or overwritten report rows."""

from datetime import datetime, timezone

from app import db
from app.models import TaskLog

GENERATION_TASK_TYPE = "report_gen"


def generation_task_payload(task):
    meta = task.meta or {}
    processed = (task.success_count or 0) + (task.invalid_count or 0) + (task.error_count or 0)
    return {
        "id": task.id, "status": task.status, "report_type": meta.get("report_type"),
        "period_start": meta.get("period_start"), "period_end": meta.get("period_end"),
        "overwrite": bool(meta.get("overwrite")), "total_count": task.total_count or 0,
        "processed_count": processed, "success_count": task.success_count or 0,
        "skipped_count": task.invalid_count or 0, "error_count": task.error_count or 0,
        "error_message": task.error_message,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
    }


def _finish_batch(task, meta):
    processed = (task.success_count or 0) + (task.invalid_count or 0) + (task.error_count or 0)
    if task.status not in ("pending", "running") or not meta.get("dispatch_complete") or processed < task.total_count:
        return
    task.status = "partial" if task.error_count and (task.success_count or task.invalid_count) else "failed" if task.error_count else "success"
    task.finished_at = datetime.now(timezone.utc)


def record_generation_result(task_id, key=None, outcome=None, error=None, *, dispatch_complete=False):
    # Lock only while recording completion, so report generation remains parallel.
    task = TaskLog.query.filter_by(id=task_id, task_type=GENERATION_TASK_TYPE).populate_existing().with_for_update().one()
    meta = dict(task.meta or {})
    completed = list(meta.get("completed_keys") or [])
    if key is not None and key not in completed:
        completed.append(key)
        if outcome == "success":
            task.success_count = (task.success_count or 0) + 1
        elif outcome == "skipped":
            task.invalid_count = (task.invalid_count or 0) + 1
        else:
            task.error_count = (task.error_count or 0) + 1
            task.error_message = str(error or "报告生成失败")[:1000]
        meta["completed_keys"] = completed
    if dispatch_complete:
        meta["dispatch_complete"] = True
    task.meta = meta
    _finish_batch(task, meta)
    db.session.commit()


def fail_generation_task(task_id, message):
    db.session.rollback()
    task = db.session.get(TaskLog, task_id)
    task.status = "failed"
    task.error_message = message
    task.finished_at = datetime.now(timezone.utc)
    db.session.commit()
