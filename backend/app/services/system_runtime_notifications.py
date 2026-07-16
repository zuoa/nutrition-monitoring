"""Daily operational statistics and DingTalk runtime notifications."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import func

from app.models import (
    CapturedImage,
    ConsumptionRecord,
    DishRecognition,
    ImageStatusEnum,
    MatchResult,
    MatchStatusEnum,
    TaskLog,
    VideoRecordingJob,
    VideoSource,
    VideoSourceStatus,
    VideoSourceValidationStatus,
)
from app.services.dingtalk import (
    DingTalkService,
    normalize_robot_webhook_prefix,
    normalize_robot_webhook_url,
)


DEFAULT_SYSTEM_RUNTIME_NOTIFICATION_PREFIX = "[营养监测系统运行]"
SYSTEM_RUNTIME_NOTIFICATION_TASK_TYPE = "system_runtime_notification"
VIDEO_SYNC_TASK_TYPES = ("video_source_sync", "nvr_download")
ACTIVE_TASK_STATUSES = ("pending", "running")
TERMINAL_IMAGE_STATUSES = (
    ImageStatusEnum.identified,
    ImageStatusEnum.matched,
    ImageStatusEnum.invalid,
    ImageStatusEnum.error,
)


def resolve_system_runtime_webhook_url(config: dict) -> str:
    return normalize_robot_webhook_url(
        config.get("SYSTEM_RUNTIME_NOTIFICATION_WEBHOOK_URL")
    )


def resolve_system_runtime_webhook_prefix(config: dict) -> str:
    configured = config.get("SYSTEM_RUNTIME_NOTIFICATION_WEBHOOK_PREFIX")
    if configured is None or not str(configured).strip():
        return DEFAULT_SYSTEM_RUNTIME_NOTIFICATION_PREFIX
    return normalize_robot_webhook_prefix(configured)


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _count_by_status(query, status_column) -> dict[str, int]:
    return {
        _enum_value(status): int(count or 0)
        for status, count in query.with_entities(
            status_column,
            func.count(),
        ).group_by(status_column).all()
    }


def _legacy_recording_stats(task_logs: list[TaskLog], job_task_ids: set[int]) -> dict[str, int]:
    stats = {"total": 0, "success": 0, "failed": 0, "active": 0}
    for task_log in task_logs:
        if task_log.id in job_task_ids:
            continue
        meta = task_log.meta or {}
        recordings = meta.get("recordings") if isinstance(meta, dict) else None
        if isinstance(recordings, list) and recordings:
            for recording in recordings:
                recording_data = recording if isinstance(recording, dict) else {}
                status = str(recording_data.get("download_status") or "").strip().lower()
                stats["total"] += 1
                if status in {"success", "downloaded", "extracted"}:
                    stats["success"] += 1
                elif status in {"failed", "cancelled", "frame_extract_failed"}:
                    stats["failed"] += 1
                else:
                    stats["active"] += 1
            continue

        recording_count = _safe_int(meta.get("recording_count")) if isinstance(meta, dict) else 0
        if recording_count <= 0:
            continue
        stats["total"] += recording_count
        if task_log.status == "success":
            stats["success"] += recording_count
        elif task_log.status == "failed":
            stats["failed"] += recording_count
        elif task_log.status == "partial":
            failed_count = min(recording_count, _safe_int(meta.get("failed_recording_count")))
            stats["failed"] += failed_count
            stats["success"] += recording_count - failed_count
        else:
            stats["active"] += recording_count
    return stats


def _build_video_stats(target_date: date) -> dict[str, int]:
    sync_logs = TaskLog.query.filter(
        TaskLog.task_date == target_date,
        TaskLog.task_type.in_(VIDEO_SYNC_TASK_TYPES),
    ).all()
    task_ids = [task.id for task in sync_logs]
    jobs = (
        VideoRecordingJob.query.filter(VideoRecordingJob.task_log_id.in_(task_ids)).all()
        if task_ids
        else []
    )
    stats = {"total": len(jobs), "success": 0, "failed": 0, "active": 0}
    for job in jobs:
        if job.status == "success":
            stats["success"] += 1
        elif job.status in {"failed", "cancelled"}:
            stats["failed"] += 1
        else:
            stats["active"] += 1

    legacy_stats = _legacy_recording_stats(sync_logs, {job.task_log_id for job in jobs})
    for key in stats:
        stats[key] += legacy_stats[key]
    stats["sync_task_count"] = len(sync_logs)
    return stats


def _build_image_stats(target_date: date) -> dict[str, int]:
    primary_query = CapturedImage.query.filter(
        CapturedImage.capture_date == target_date,
        CapturedImage.is_candidate.is_(False),
    )
    status_counts = _count_by_status(primary_query, CapturedImage.status)
    total = primary_query.count()
    processed = sum(status_counts.get(status.value, 0) for status in TERMINAL_IMAGE_STATUSES)
    candidate_count = CapturedImage.query.filter(
        CapturedImage.capture_date == target_date,
        CapturedImage.is_candidate.is_(True),
    ).count()
    recognition_query = DishRecognition.query.join(
        CapturedImage,
        CapturedImage.id == DishRecognition.image_id,
    ).filter(
        CapturedImage.capture_date == target_date,
        CapturedImage.is_candidate.is_(False),
    )
    recognized_image_count = recognition_query.with_entities(
        func.count(func.distinct(DishRecognition.image_id))
    ).scalar() or 0
    return {
        "total": int(total),
        "candidate": int(candidate_count),
        "processed": int(processed),
        "pending": max(0, int(total) - int(processed)),
        "identified": status_counts.get(ImageStatusEnum.identified.value, 0),
        "matched": status_counts.get(ImageStatusEnum.matched.value, 0),
        "invalid": status_counts.get(ImageStatusEnum.invalid.value, 0),
        "error": status_counts.get(ImageStatusEnum.error.value, 0),
        "recognized_images": int(recognized_image_count),
        "recognized_dishes": int(recognition_query.count()),
        "low_confidence_dishes": int(
            recognition_query.filter(DishRecognition.is_low_confidence.is_(True)).count()
        ),
    }


def _build_match_stats(target_date: date) -> dict[str, int]:
    match_query = MatchResult.query.filter(MatchResult.match_date == target_date)
    status_counts = _count_by_status(match_query, MatchResult.status)
    day_start = datetime.combine(target_date, time.min)
    day_end = day_start + timedelta(days=1)
    consumption_count = ConsumptionRecord.query.filter(
        ConsumptionRecord.transaction_time >= day_start,
        ConsumptionRecord.transaction_time < day_end,
        ConsumptionRecord.amount < 0,
    ).count()
    return {
        "consumptions": int(consumption_count),
        "matched": status_counts.get(MatchStatusEnum.matched.value, 0),
        "confirmed": status_counts.get(MatchStatusEnum.confirmed.value, 0),
        "pending_confirmation": status_counts.get(MatchStatusEnum.time_matched_only.value, 0),
        "unmatched_records": status_counts.get(MatchStatusEnum.unmatched_record.value, 0),
        "unmatched_images": status_counts.get(MatchStatusEnum.unmatched_image.value, 0),
    }


def _redis_status() -> str:
    from app import redis_client

    if redis_client is None:
        return "unknown"
    try:
        return "ok" if redis_client.ping() else "error"
    except Exception:
        return "error"


def _build_health_stats(target_date: date, now: datetime) -> dict[str, Any]:
    source_query = VideoSource.query.filter(VideoSource.status == VideoSourceStatus.enabled.value)
    enabled_sources = source_query.count()
    active_sources = source_query.filter(VideoSource.is_active.is_(True)).count()
    invalid_sources = source_query.filter(
        VideoSource.last_validation_status == VideoSourceValidationStatus.failed.value
    ).count()
    failed_tasks = TaskLog.query.filter(
        TaskLog.task_date == target_date,
        TaskLog.task_type != SYSTEM_RUNTIME_NOTIFICATION_TASK_TYPE,
        TaskLog.status == "failed",
    ).count()
    partial_tasks = TaskLog.query.filter(
        TaskLog.task_date == target_date,
        TaskLog.task_type != SYSTEM_RUNTIME_NOTIFICATION_TASK_TYPE,
        TaskLog.status == "partial",
    ).count()
    active_tasks = TaskLog.query.filter(
        TaskLog.task_type != SYSTEM_RUNTIME_NOTIFICATION_TASK_TYPE,
        TaskLog.status.in_(ACTIVE_TASK_STATUSES),
    ).count()
    stale_before = now.astimezone(timezone.utc) - timedelta(hours=6)
    stale_tasks = TaskLog.query.filter(
        TaskLog.status.in_(ACTIVE_TASK_STATUSES),
        TaskLog.task_type != SYSTEM_RUNTIME_NOTIFICATION_TASK_TYPE,
        TaskLog.started_at < stale_before,
    ).count()
    redis_status = _redis_status()
    warning = any((failed_tasks, partial_tasks, invalid_sources, stale_tasks))
    critical = redis_status == "error"
    if enabled_sources > 0 and active_sources == 0:
        warning = True
    return {
        "overall": "error" if critical else ("warning" if warning else "healthy"),
        "database": "ok",
        "redis": redis_status,
        "enabled_video_sources": int(enabled_sources),
        "active_video_sources": int(active_sources),
        "invalid_video_sources": int(invalid_sources),
        "failed_tasks": int(failed_tasks),
        "partial_tasks": int(partial_tasks),
        "active_tasks": int(active_tasks),
        "stale_tasks": int(stale_tasks),
    }


def build_system_runtime_summary(
    target_date: date,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    generated_at = now or datetime.now(timezone.utc)
    return {
        "date": target_date.isoformat(),
        "generated_at": generated_at.isoformat(),
        "video": _build_video_stats(target_date),
        "images": _build_image_stats(target_date),
        "matches": _build_match_stats(target_date),
        "health": _build_health_stats(target_date, generated_at),
    }


def format_system_runtime_message(summary: dict[str, Any], prefix: str) -> str:
    video = summary["video"]
    images = summary["images"]
    matches = summary["matches"]
    health = summary["health"]
    health_label = {
        "healthy": "正常",
        "warning": "需关注",
        "error": "异常",
    }.get(health["overall"], "未知")
    redis_label = {"ok": "正常", "error": "异常", "unknown": "未检测"}.get(
        health["redis"], "未知"
    )
    matched_count = matches["matched"] + matches["confirmed"]
    return "\n".join((
        f"{prefix} 每日运行报告{'（测试推送）' if summary.get('test') else ''}",
        f"统计日期：{summary['date']}｜系统状态：{health_label}",
        "",
        "【视频同步】",
        f"录像 {video['total']} 段：成功 {video['success']}，失败 {video['failed']}，处理中 {video['active']}",
        "",
        "【图像分析与识别】",
        f"主图 {images['total']} 张（候选图 {images['candidate']} 张），已分析 {images['processed']} 张，待处理 {images['pending']} 张",
        f"已识别 {images['identified']} 张，已匹配 {images['matched']} 张，无效 {images['invalid']} 张，异常 {images['error']} 张",
        f"识别菜品 {images['recognized_dishes']} 项，覆盖 {images['recognized_images']} 张图片，低置信 {images['low_confidence_dishes']} 项",
        "",
        "【消费匹配】",
        (
            f"消费 {matches['consumptions']} 笔：成功匹配 {matched_count} 笔，"
            f"待确认 {matches['pending_confirmation']} 笔，未匹配消费 {matches['unmatched_records']} 笔，"
            f"未匹配图片 {matches['unmatched_images']} 张"
        ),
        "",
        "【运行状态】",
        (
            f"数据库正常，Redis {redis_label}；视频源启用 {health['enabled_video_sources']} 个，"
            f"当前激活 {health['active_video_sources']} 个，校验异常 {health['invalid_video_sources']} 个"
        ),
        f"任务失败 {health['failed_tasks']} 个，部分成功 {health['partial_tasks']} 个，运行中 {health['active_tasks']} 个，疑似停滞 {health['stale_tasks']} 个",
    ))


def send_system_runtime_notification(config: dict, summary: dict[str, Any]) -> dict:
    webhook_url = resolve_system_runtime_webhook_url(config)
    if not webhook_url:
        raise ValueError("未配置系统运行通知 Webhook")
    prefix = resolve_system_runtime_webhook_prefix(config)
    result = DingTalkService(config).send_robot_webhook(
        {
            "msgtype": "text",
            "text": {"content": format_system_runtime_message(summary, prefix)},
        },
        webhook_url=webhook_url,
    )
    if str(result.get("errcode")) != "0":
        raise RuntimeError(result.get("errmsg") or "钉钉返回未知错误")
    return result
