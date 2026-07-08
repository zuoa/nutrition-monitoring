import logging
from datetime import datetime, timedelta, timezone

from celery_app import celery

logger = logging.getLogger(__name__)

SOURCE_SYSTEM = "ztk_plus"
DEFAULT_SYNC_INTERVAL_MINUTES = 5


def _sync_interval_minutes(config) -> int:
    try:
        return max(1, int(config.get("ZTK_SYNC_INTERVAL_MINUTES") or DEFAULT_SYNC_INTERVAL_MINUTES))
    except (TypeError, ValueError):
        return DEFAULT_SYNC_INTERVAL_MINUTES


def _as_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _next_sync_at(last_synced_at: datetime | None, interval_minutes: int) -> datetime | None:
    last_sync = _as_utc_datetime(last_synced_at)
    if last_sync is None:
        return None
    return last_sync + timedelta(minutes=interval_minutes)


def _sync_due_status(config, now: datetime | None = None) -> tuple[bool, int, datetime | None]:
    from app.models import ConsumptionSyncState

    interval_minutes = _sync_interval_minutes(config)
    state = ConsumptionSyncState.query.filter_by(source_system=SOURCE_SYSTEM).first()
    next_sync_at = _next_sync_at(state.last_synced_at if state else None, interval_minutes)
    if next_sync_at is None:
        return True, interval_minutes, None

    current_time = _as_utc_datetime(now) or datetime.now(timezone.utc)
    return current_time >= next_sync_at, interval_minutes, next_sync_at


def _mark_sync_attempt_started(now: datetime | None = None) -> None:
    from app import db
    from app.services.ztk_consumption_sync import get_or_create_consumption_sync_state

    current_time = _as_utc_datetime(now) or datetime.now(timezone.utc)
    state = get_or_create_consumption_sync_state(SOURCE_SYSTEM, lock=True)
    state.last_synced_at = current_time
    state.updated_at = current_time
    db.session.commit()


@celery.task(name="app.tasks.ztk_consumption.sync_ztk_consumption")
def sync_ztk_consumption(force: bool = False):
    from flask import current_app
    from app.services.runtime_config import get_effective_config
    from app.services.ztk_consumption_sync import ZtkConsumptionSyncService

    # Read the enable flag from the effective config so runtime overrides
    # (runtime_config.json) take effect on the worker without a restart.
    effective_config = get_effective_config(current_app.config)
    interval_minutes = _sync_interval_minutes(effective_config)
    if not force and not effective_config.get("ZTK_SYNC_ENABLED"):
        return {
            "source_system": SOURCE_SYSTEM,
            "disabled": True,
            "sync_interval_minutes": interval_minutes,
            "message": "一卡通数据库同步未启用",
        }

    if not force:
        due, interval_minutes, next_sync_at = _sync_due_status(effective_config)
        if not due:
            return {
                "source_system": SOURCE_SYSTEM,
                "skipped": True,
                "sync_interval_minutes": interval_minutes,
                "next_sync_at": next_sync_at.isoformat() if next_sync_at else None,
                "message": "未到一卡通数据库同步间隔，已跳过本次检查",
            }
        _mark_sync_attempt_started()

    result = ZtkConsumptionSyncService(config=effective_config).sync_once()
    result["sync_interval_minutes"] = interval_minutes
    if result.get("imported", 0) > 0:
        from app.tasks.matching import run_matching_for_batch

        run_matching_for_batch.delay(result["batch_id"])
    logger.info("ZTK consumption sync complete: %s", result)
    return result
