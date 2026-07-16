"""Scheduled daily system-operation notifications."""

import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from celery_app import celery
from app import db
from app.models import TaskLog
from app.services.dingtalk import redact_dingtalk_request_error
from app.services.system_runtime_notifications import (
    SYSTEM_RUNTIME_NOTIFICATION_TASK_TYPE,
    build_system_runtime_summary,
    resolve_system_runtime_webhook_url,
    send_system_runtime_notification,
)


logger = logging.getLogger(__name__)


def _notification_schedule(config: dict) -> tuple[int, int]:
    raw_time = str(config.get("SYSTEM_RUNTIME_NOTIFICATION_TIME") or "08:10").strip()
    try:
        hour_text, minute_text = raw_time.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        logger.warning(
            "Invalid SYSTEM_RUNTIME_NOTIFICATION_TIME=%r, fallback to 08:10",
            raw_time,
        )
        hour, minute = 8, 10
    return hour, minute


def _notification_now(config: dict, now_iso: str | None = None) -> datetime:
    timezone_name = str(config.get("APP_TIMEZONE") or "Asia/Shanghai")
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo("Asia/Shanghai")
    if not now_iso:
        return datetime.now(tz)
    parsed = datetime.fromisoformat(now_iso)
    return parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed.astimezone(tz)


@celery.task(name="app.tasks.system_notifications.dispatch_daily_system_runtime_notification")
def dispatch_daily_system_runtime_notification(
    now_iso: str | None = None,
    target_date_str: str | None = None,
):
    """Send the previous day's runtime report when the configured minute is due."""
    from flask import current_app
    from app.services.runtime_config import get_effective_config

    config = get_effective_config(current_app.config)
    if not config.get("SYSTEM_RUNTIME_NOTIFICATION_ENABLED", False):
        return {"sent": False, "reason": "notification_disabled"}
    try:
        webhook_url = resolve_system_runtime_webhook_url(config)
    except ValueError as exc:
        return {"sent": False, "reason": "invalid_webhook", "error": str(exc)}
    if not webhook_url:
        return {"sent": False, "reason": "webhook_not_configured"}

    now = _notification_now(config, now_iso)
    scheduled_hour, scheduled_minute = _notification_schedule(config)
    if (now.hour, now.minute) < (scheduled_hour, scheduled_minute):
        return {"sent": False, "reason": "not_due"}

    target_date = (
        date.fromisoformat(target_date_str)
        if target_date_str
        else now.date() - timedelta(days=1)
    )
    existing = TaskLog.query.filter_by(
        task_type=SYSTEM_RUNTIME_NOTIFICATION_TASK_TYPE,
        task_date=target_date,
        status="success",
    ).first()
    if existing is not None:
        return {"sent": False, "reason": "already_sent", "task_id": existing.id}

    task_log = TaskLog(
        task_type=SYSTEM_RUNTIME_NOTIFICATION_TASK_TYPE,
        task_date=target_date,
        status="running",
        meta={"scheduled_at": now.isoformat()},
    )
    db.session.add(task_log)
    db.session.commit()

    try:
        summary = build_system_runtime_summary(target_date, now=now)
        send_system_runtime_notification(config, summary)
        task_log.status = "success"
        task_log.success_count = 1
        task_log.finished_at = datetime.now(timezone.utc)
        task_log.meta = {
            **dict(task_log.meta or {}),
            "summary": summary,
        }
        db.session.commit()
        return {
            "sent": True,
            "task_id": task_log.id,
            "date": target_date.isoformat(),
            "health": summary["health"]["overall"],
        }
    except Exception as exc:
        db.session.rollback()
        safe_error = redact_dingtalk_request_error(exc)
        current = db.session.get(TaskLog, task_log.id)
        if current is not None:
            current.status = "failed"
            current.error_count = 1
            current.error_message = safe_error[:2000]
            current.finished_at = datetime.now(timezone.utc)
            db.session.commit()
        logger.warning("System runtime notification failed: %s", safe_error)
        return {
            "sent": False,
            "reason": "send_failed",
            "task_id": task_log.id,
            "error": safe_error,
        }
