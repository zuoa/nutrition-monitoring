import logging
import os
from datetime import date, datetime

from celery_app import celery
from huggingface_hub import snapshot_download

from app import db
from app.models import TaskLog
from app.services.local_model_manager import get_local_model_spec

logger = logging.getLogger(__name__)


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
    spec = get_local_model_spec(config, model_type, variant=variant)
    target_path = spec["path"]
    repo_id = spec["repo_id"]

    task_log = TaskLog(
        task_type="local_model_download",
        task_date=date.today(),
        meta={
            "model_type": model_type,
            "variant": spec["variant"],
            "repo_id": repo_id,
            "target_path": target_path,
        },
    )
    db.session.add(task_log)
    db.session.commit()

    try:
        os.makedirs(target_path, exist_ok=True)
        snapshot_download(
            repo_id=repo_id,
            local_dir=target_path,
            resume_download=True,
        )
        task_log.status = "success"
        task_log.total_count = 1
        task_log.success_count = 1
        task_log.finished_at = datetime.utcnow()
        db.session.commit()
        return {
            "model_type": model_type,
            "variant": spec["variant"],
            "repo_id": repo_id,
            "target_path": target_path,
        }
    except Exception as e:
        logger.error("Failed to download local model %s from %s: %s", model_type, repo_id, e, exc_info=True)
        task_log.status = "failed"
        task_log.error_count = 1
        task_log.error_message = str(e)[:1000]
        task_log.finished_at = datetime.utcnow()
        db.session.commit()
        raise
