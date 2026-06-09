import logging

from celery_app import celery

logger = logging.getLogger(__name__)


@celery.task(name="app.tasks.ztk_consumption.sync_ztk_consumption")
def sync_ztk_consumption(force: bool = False):
    from flask import current_app
    from app.services.ztk_consumption_sync import ZtkConsumptionSyncService

    if not force and not current_app.config.get("ZTK_SYNC_ENABLED"):
        return {
            "source_system": ZtkConsumptionSyncService.SOURCE_SYSTEM,
            "disabled": True,
            "message": "一卡通数据库同步未启用",
        }

    result = ZtkConsumptionSyncService().sync_once()
    if result.get("imported", 0) > 0:
        from app.tasks.matching import run_matching_for_batch

        run_matching_for_batch.delay(result["batch_id"])
    logger.info("ZTK consumption sync complete: %s", result)
    return result
