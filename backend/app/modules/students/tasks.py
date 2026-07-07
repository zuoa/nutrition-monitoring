"""学生管理模块的 Celery 任务：钉钉家校通讯录全量同步。"""
import logging
from datetime import datetime, timezone

from celery_app import celery
from app import db

logger = logging.getLogger(__name__)


@celery.task(name="app.modules.students.tasks.sync_dingtalk_school")
def sync_dingtalk_school():
    """全量同步：组织树 → 学生 + 监护人。结果写入 TaskLog(task_type='student_sync')。"""
    from flask import current_app
    from app.models import TaskLog
    from app.modules.students.services.dingtalk_edu import DingTalkEduService
    from app.modules.students.services.org_sync_service import OrgSyncService
    from app.modules.students.services.student_sync_service import StudentSyncService

    cfg = current_app.config
    edu = DingTalkEduService(cfg)

    log = TaskLog(task_type="student_sync", status="running")
    db.session.add(log)
    db.session.commit()
    log_id = log.id

    try:
        org_stats = OrgSyncService(edu).sync()
        stu_stats = StudentSyncService(edu).sync()
        log = db.session.get(TaskLog, log_id)
        log.status = "success"
        log.meta = {"org": org_stats, "students": stu_stats}
        log.total_count = stu_stats.get("students", 0)
        log.success_count = stu_stats.get("students", 0)
        logger.info("钉钉家校通讯录同步成功：%s | %s", org_stats, stu_stats)
    except Exception as exc:
        db.session.rollback()
        log = db.session.get(TaskLog, log_id)
        if log:
            log.status = "failed"
            log.error_message = str(exc)[:1000]
        logger.exception("钉钉家校通讯录同步失败")
    finally:
        log = db.session.get(TaskLog, log_id)
        if log:
            log.finished_at = datetime.now(timezone.utc)
            db.session.commit()
