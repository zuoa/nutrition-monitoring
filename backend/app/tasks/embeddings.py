import logging
from datetime import datetime, date

from celery_app import celery
from app import db
from app.models import TaskLog

logger = logging.getLogger(__name__)


@celery.task(name="app.tasks.embeddings.rebuild_sample_embeddings", bind=True, max_retries=1)
def rebuild_sample_embeddings(self):
    from flask import current_app
    from app.services.local_embedding import LocalEmbeddingIndexService

    cfg = current_app.config
    task_log = TaskLog(task_type="dish_embedding", task_date=date.today())
    db.session.add(task_log)
    db.session.commit()

    try:
        result = LocalEmbeddingIndexService(cfg).rebuild_index()
        task_log.status = "success" if result.get("failed", 0) == 0 else "partial"
        task_log.total_count = int(result.get("total", 0))
        task_log.success_count = int(result.get("ready", 0))
        task_log.error_count = int(result.get("failed", 0))
        task_log.finished_at = datetime.utcnow()
        db.session.commit()
        return result
    except Exception as e:
        logger.error("Failed to rebuild local sample embeddings: %s", e, exc_info=True)
        task_log.status = "failed"
        task_log.error_message = str(e)[:255]
        task_log.finished_at = datetime.utcnow()
        db.session.commit()
        raise
