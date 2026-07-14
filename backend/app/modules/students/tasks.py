"""学生管理模块的 Celery 同步任务。"""

import logging
from datetime import datetime, timezone

from celery_app import celery
from app import db

logger = logging.getLogger(__name__)


@celery.task(name="app.modules.students.tasks.sync_students")
def sync_students(provider_name: str | None = None):
    """调用已配置的 Provider 同步学生，并统一记录 TaskLog。"""
    from flask import current_app
    from app.models import TaskLog
    from app.modules.students.services.sync_backends import get_student_sync_backend

    selected_provider = provider_name or current_app.config.get("STUDENT_SYNC_PROVIDER") or "dingtalk"
    log = TaskLog(
        task_type="student_sync",
        status="running",
        meta={"provider": str(selected_provider)},
    )
    db.session.add(log)
    db.session.commit()
    log_id = log.id

    try:
        backend = get_student_sync_backend(current_app.config, provider_name)
        result = backend.sync()
        student_stats = result.get("students") or {}
        success_count = int(student_stats.get("students", 0))
        total_count = int(student_stats.get("total", success_count))

        log = db.session.get(TaskLog, log_id)
        log.status = "success"
        log.meta = {
            "provider": backend.key,
            "provider_label": backend.label,
            **result,
        }
        log.total_count = total_count
        log.success_count = success_count
        log.error_count = int(student_stats.get("error_count", 0))
        logger.info("学生同步成功：provider=%s result=%s", backend.key, result)
        return result
    except Exception as exc:
        db.session.rollback()
        log = db.session.get(TaskLog, log_id)
        if log:
            log.status = "failed"
            log.error_message = str(exc)[:1000]
        logger.exception("学生同步失败：provider=%s", selected_provider)
        return {"error": str(exc)}
    finally:
        log = db.session.get(TaskLog, log_id)
        if log:
            log.finished_at = datetime.now(timezone.utc)
            db.session.commit()


@celery.task(name="app.modules.students.tasks.sync_dingtalk_school")
def sync_dingtalk_school():
    """兼容旧的 Celery 任务名；新调度统一使用 ``sync_students``。"""
    return sync_students.run(provider_name="dingtalk")
