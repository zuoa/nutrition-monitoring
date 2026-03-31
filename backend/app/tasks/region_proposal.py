import logging
from datetime import datetime
from typing import Any

from celery_app import celery

from app import db
from app.models import CapturedImage, TaskLog

logger = logging.getLogger(__name__)


def _merge_meta(task_log: TaskLog, updates: dict[str, Any]) -> None:
    merged = dict(task_log.meta or {})
    merged.update(updates)
    task_log.meta = merged


@celery.task(
    name="app.tasks.region_proposal.propose_regions_for_image",
    bind=True,
    max_retries=0,
    soft_time_limit=900,
    time_limit=1200,
)
def propose_regions_for_image(self, task_log_id: int, image_id: int, prompt: str | None = None):
    from flask import current_app
    from app.services.inference_client import make_detector_client

    task_log = db.session.get(TaskLog, task_log_id)
    if not task_log:
        logger.warning("Region proposal task log %s not found", task_log_id)
        return

    normalized_prompt = (prompt or "").strip() or None
    if normalized_prompt:
        raise ValueError("当前检测服务不支持自定义提示词，请留空后重试")
    _merge_meta(task_log, {
        "image_id": image_id,
        "prompt": normalized_prompt or "",
        "celery_task_id": getattr(self.request, "id", None),
        "status_text": "正在生成菜区提议",
    })
    db.session.commit()

    try:
        img = db.session.get(CapturedImage, image_id)
        if not img:
            raise ValueError("图片不存在")
        if not img.image_path:
            raise ValueError("图片路径不存在")

        detector_result = make_detector_client(current_app.config).post_file(
            "/v1/detect",
            image_path=img.image_path,
        )
        proposals = list(detector_result.get("regions") or detector_result.get("proposals") or [])
        backend = detector_result.get("backend")

        task_log.status = "success"
        task_log.total_count = len(proposals)
        task_log.success_count = len(proposals)
        task_log.error_count = 0
        task_log.error_message = None
        task_log.finished_at = datetime.utcnow()
        _merge_meta(task_log, {
            "image_id": image_id,
            "image_path": img.image_path,
            "backend": backend,
            "prompt": normalized_prompt or "",
            "proposals": proposals,
            "status_text": (
                f"已生成 {len(proposals)} 个菜区提议"
                if proposals
                else "未检测到明显菜区"
            ),
        })
        db.session.commit()
    except Exception as e:
        logger.error("Failed to generate region proposals for image %s: %s", image_id, e, exc_info=True)
        task_log.status = "failed"
        task_log.total_count = 0
        task_log.success_count = 0
        task_log.error_count = 1
        task_log.error_message = str(e)
        task_log.finished_at = datetime.utcnow()
        _merge_meta(task_log, {
            "image_id": image_id,
            "prompt": normalized_prompt or "",
            "status_text": "菜区提议生成失败",
        })
        db.session.commit()
        raise
