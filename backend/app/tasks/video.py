import logging
import os
from copy import deepcopy
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo

from celery_app import celery
from app import db
from app.models import CapturedImage, TaskLog, ImageStatusEnum
from app.services.runtime_config import get_effective_config
from app.services.video_sources import VideoSourceConfigError, VideoSourceManager

logger = logging.getLogger(__name__)


LEGACY_SYNC_TASK_TYPES = ("video_source_sync", "nvr_download")
ACTIVE_SYNC_STATUSES = ("pending", "running")
VIDEO_SYNC_TASK_SOFT_TIME_LIMIT = 1800
VIDEO_SYNC_TASK_TIME_LIMIT = 2100
DEFAULT_MEAL_WINDOWS = [
    {"start": "07:00", "end": "09:00"},
    {"start": "11:30", "end": "13:00"},
    {"start": "17:30", "end": "19:00"},
]
DEFAULT_VIDEO_STORAGE_PATH = "/data/nvr_cache"
STALE_ACTIVE_SYNC_AFTER = timedelta(hours=6)


@celery.task(
    name="app.tasks.video.sync_video_source_media",
    bind=True,
    max_retries=2,
    soft_time_limit=VIDEO_SYNC_TASK_SOFT_TIME_LIMIT,
    time_limit=VIDEO_SYNC_TASK_TIME_LIMIT,
)
def sync_video_source_media(self, date_str: str = None):
    """Synchronize recordings from the active video source and extract cashier frames."""
    from flask import current_app
    from app.services.video_analyzer import VideoAnalyzer

    cfg = get_effective_config(current_app.config)
    target_date = _resolve_target_date(cfg, date_str)
    active_task = _find_active_sync_task()
    if active_task is not None:
        logger.warning(
            "Skip video source sync for %s because task %s is already %s",
            target_date,
            active_task.id,
            active_task.status,
        )
        return {
            "skipped": True,
            "reason": "active_task_exists",
            "active_task_id": active_task.id,
            "active_task_date": active_task.task_date.isoformat() if active_task.task_date else None,
        }

    task_log = TaskLog(
        task_type="video_source_sync",
        task_date=target_date,
        meta={
            "status_text": "正在查询录像",
            "recordings": [],
            "empty_windows": [],
            "image_ids": [],
            "primary_count": 0,
            "candidate_count": 0,
        },
    )
    db.session.add(task_log)
    db.session.commit()

    try:
        manager = VideoSourceManager(cfg)
        runtime_source = manager.get_active_runtime_source()
        video_source = _make_video_source(runtime_source, app_config=cfg)
        analyzer = VideoAnalyzer(cfg)

        source_config = runtime_source.get("config") or {}
        meal_windows = _resolve_sync_meal_windows(cfg)
        channel_ids = _resolve_sync_channel_ids(source_config)
        storage_path = source_config.get("local_storage_path") or DEFAULT_VIDEO_STORAGE_PATH
        image_path = cfg.get("IMAGE_STORAGE_PATH", "/data/images")
        task_meta = dict(task_log.meta or {})
        task_meta.update({
            "status_text": "正在同步视频源录像",
            "source_name": runtime_source.get("name", ""),
            "source_type": runtime_source.get("source_type", ""),
            "channel_ids": channel_ids,
            "recordings": list(task_meta.get("recordings") or []),
            "empty_windows": list(task_meta.get("empty_windows") or []),
            "image_ids": list(task_meta.get("image_ids") or []),
            "primary_count": int(task_meta.get("primary_count") or 0),
            "candidate_count": int(task_meta.get("candidate_count") or 0),
        })
        _persist_task_meta(task_log, task_meta)
        db.session.commit()

        total_images = 0

        for channel_id in channel_ids:
            for window in meal_windows:
                start_dt = datetime.strptime(
                    f"{target_date} {window['start']}", "%Y-%m-%d %H:%M"
                )
                end_dt = datetime.strptime(
                    f"{target_date} {window['end']}", "%Y-%m-%d %H:%M"
                )

                recordings = video_source.list_recordings(channel_id, start_dt, end_dt)
                if not recordings:
                    logger.warning(f"No recordings for channel {channel_id} {start_dt}-{end_dt}")
                    task_meta["empty_windows"].append({
                        "channel_id": channel_id,
                        "window_start": start_dt.isoformat(),
                        "window_end": end_dt.isoformat(),
                    })
                    task_meta["status_text"] = f"通道 {channel_id} 在 {window['start']}-{window['end']} 未查询到录像"
                    _persist_task_meta(task_log, task_meta)
                    db.session.commit()
                    continue

                for rec in recordings:
                    video_filename = rec.get("filename", f"{channel_id}_{int(start_dt.timestamp())}.mp4")
                    video_save_path = os.path.join(storage_path, str(target_date), video_filename)
                    recording_meta = {
                        "channel_id": channel_id,
                        "window_start": start_dt.isoformat(),
                        "window_end": end_dt.isoformat(),
                        "filename": video_filename,
                        "recording_start": rec.get("start_time"),
                        "recording_end": rec.get("end_time"),
                        "download_status": "pending",
                        "frame_count": 0,
                        "image_ids": [],
                    }
                    task_meta["recordings"].append(recording_meta)
                    task_meta["status_text"] = f"正在下载录像 {video_filename}"
                    _persist_task_meta(task_log, task_meta)
                    db.session.commit()

                    # Download
                    resume_offset = os.path.getsize(video_save_path) if os.path.exists(video_save_path) else 0
                    ok = video_source.download_recording(
                        rec.get("download_url", ""), video_save_path, resume_offset
                    )
                    if not ok:
                        logger.error(f"Failed to download {video_filename}")
                        recording_meta["download_status"] = "failed"
                        task_meta["status_text"] = f"录像下载失败：{video_filename}"
                        task_log.error_count = int(task_log.error_count or 0) + 1
                        _persist_task_meta(task_log, task_meta)
                        db.session.commit()
                        continue
                    recording_meta["download_status"] = "success"

                    # Extract frames
                    video_start = rec.get("start_time")
                    if isinstance(video_start, str):
                        video_start = datetime.fromisoformat(video_start)
                    else:
                        video_start = start_dt

                    output_dir = os.path.join(image_path, str(target_date), channel_id)
                    try:
                        frames = analyzer.extract_frames(
                            video_save_path, output_dir, video_start, channel_id
                        )
                    except Exception as e:
                        logger.error(f"Frame extraction failed for {video_filename}: {e}")
                        recording_meta["download_status"] = "frame_extract_failed"
                        recording_meta["error"] = _format_task_error(e)
                        task_meta["status_text"] = f"抽帧失败：{video_filename}"
                        task_log.error_count = int(task_log.error_count or 0) + 1
                        _persist_task_meta(task_log, task_meta)
                        db.session.commit()
                        continue

                    created_images: list[CapturedImage] = []
                    for frame in frames:
                        img = CapturedImage(
                            capture_date=target_date,
                            channel_id=frame["channel_id"],
                            captured_at=frame["captured_at"],
                            image_path=frame["image_path"],
                            status=ImageStatusEnum.pending,
                            source_video=video_filename,
                            diff_score=frame.get("diff_score"),
                            is_candidate=frame.get("is_candidate", False),
                        )
                        db.session.add(img)
                        created_images.append(img)
                        total_images += 1

                    db.session.commit()
                    created_image_ids = [img.id for img in created_images if img.id]
                    recording_meta["frame_count"] = len(frames)
                    recording_meta["image_ids"] = created_image_ids
                    task_meta["image_ids"].extend(created_image_ids)
                    task_meta["primary_count"] += len([frame for frame in frames if not frame.get("is_candidate", False)])
                    task_meta["candidate_count"] += len([frame for frame in frames if frame.get("is_candidate", False)])
                    task_meta["status_text"] = f"已处理录像 {video_filename}，抽取 {len(frames)} 张图片"
                    _persist_task_meta(task_log, task_meta)
                    task_log.total_count = total_images
                    task_log.success_count = total_images
                    db.session.commit()

        task_log.status = "partial" if int(task_log.error_count or 0) > 0 else "success"
        task_log.total_count = total_images
        task_log.success_count = total_images
        task_log.finished_at = datetime.utcnow()
        task_meta["recording_count"] = len(task_meta["recordings"])
        task_meta["status_text"] = (
            f"同步完成，共查询到 {len(task_meta['recordings'])} 段录像，抽取 {total_images} 张图片"
            if task_log.status == "success"
            else f"同步部分完成，共查询到 {len(task_meta['recordings'])} 段录像，抽取 {total_images} 张图片，失败 {task_log.error_count} 次"
        )
        _persist_task_meta(task_log, task_meta)
        db.session.commit()

        # Trigger recognition
        from app.tasks.recognition import run_recognition_batch
        run_recognition_batch.delay(str(target_date))

        logger.info(f"Video source sync complete for {target_date}: {total_images} images")

    except Exception as e:
        logger.error(f"Video source sync task failed: {e}", exc_info=True)
        task_log.status = "failed"
        task_log.error_message = str(e)
        task_log.finished_at = datetime.utcnow()
        failed_meta = {
            **dict(task_log.meta or {}),
            "status_text": "视频源同步失败",
        }
        _persist_task_meta(task_log, failed_meta)
        db.session.commit()

        # Alert admin via DingTalk
        _send_admin_alert(f"视频源同步任务失败（{target_date}）: {str(e)[:200]}")
        raise self.retry(exc=e, countdown=300)


@celery.task(name="app.tasks.video.schedule_video_source_sync")
def schedule_video_source_sync():
    """Periodically check whether the active video source should sync now."""
    from flask import current_app

    cfg = get_effective_config(current_app.config)
    try:
        target_date = _get_scheduled_sync_target_date(cfg)
    except VideoSourceConfigError as e:
        logger.info("Skip scheduled video source sync: %s", e)
        return {"scheduled": False, "reason": str(e)}

    if target_date is None:
        return {"scheduled": False}

    active_task = _find_active_sync_task()
    if active_task is not None:
        logger.info(
            "Skip scheduled video source sync for %s: task %s is already %s",
            target_date,
            active_task.id,
            active_task.status,
        )
        return {
            "scheduled": False,
            "reason": "active_task_exists",
            "active_task_id": active_task.id,
            "date": target_date.isoformat(),
        }

    sync_video_source_media.delay(target_date.isoformat())
    logger.info("Scheduled video source sync dispatched for %s", target_date.isoformat())
    return {"scheduled": True, "date": target_date.isoformat()}


def download_nvr_videos(date_str: str = None):
    """Backward-compatible wrapper for older imports."""
    return sync_video_source_media(date_str=date_str)


def _resolve_sync_channel_ids(source_config) -> list[str]:
    channel_ids = source_config.get("channel_ids")
    if isinstance(channel_ids, list):
        normalized = [str(item).strip() for item in channel_ids if str(item).strip()]
        if normalized:
            return normalized

    cameras = source_config.get("cameras")
    if isinstance(cameras, list):
        normalized = [
            str(camera.get("channel_id") or "").strip()
            for camera in cameras
            if str(camera.get("channel_id") or "").strip()
        ]
        if normalized:
            return normalized

    raise VideoSourceConfigError("当前视频源未配置可用的 channel_id")


def _resolve_sync_meal_windows(cfg) -> list[dict[str, str]]:
    raw = cfg.get("VIDEO_SYNC_MEAL_WINDOWS")
    if not isinstance(raw, list):
        return deepcopy(DEFAULT_MEAL_WINDOWS)

    normalized = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        start = str(item.get("start") or "").strip()
        end = str(item.get("end") or "").strip()
        if not start or not end:
            continue
        normalized.append({"start": start, "end": end})

    return normalized or deepcopy(DEFAULT_MEAL_WINDOWS)


def _parse_trigger_time(value) -> tuple[int, int]:
    raw = str(value or "").strip() or "21:30"
    try:
        hour_str, minute_str = raw.split(":", 1)
        hour = int(hour_str)
        minute = int(minute_str)
    except (AttributeError, ValueError):
        logger.warning("Invalid download_trigger_time=%r, fallback to 21:30", value)
        return 21, 30

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        logger.warning("Out-of-range download_trigger_time=%r, fallback to 21:30", value)
        return 21, 30
    return hour, minute


def _get_local_now(cfg, now: datetime | None = None) -> datetime:
    tz = ZoneInfo(str(cfg.get("VIDEO_TIMEZONE") or cfg.get("APP_TIMEZONE", "Asia/Shanghai") or "Asia/Shanghai"))
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        return now.replace(tzinfo=tz)
    return now.astimezone(tz)


def _has_existing_sync_task(target_date: date) -> bool:
    return db.session.query(TaskLog.id).filter(
        TaskLog.task_date == target_date,
        TaskLog.task_type.in_(LEGACY_SYNC_TASK_TYPES),
        TaskLog.status.in_(("pending", "running", "success")),
    ).first() is not None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _persist_task_meta(task_log: TaskLog, task_meta: dict) -> None:
    task_log.meta = deepcopy(task_meta)


def mark_sync_task_failed(task_log: TaskLog, reason: str, *, now: datetime | None = None) -> TaskLog:
    resolved_now = now or _utcnow()
    task_log.status = "failed"
    task_log.error_message = reason
    task_log.finished_at = resolved_now
    _persist_task_meta(task_log, {
        **dict(task_log.meta or {}),
        "status_text": reason,
    })
    return task_log


def _format_task_error(exc: Exception) -> str:
    text = str(exc).strip()
    if text:
        return text
    exc_name = exc.__class__.__name__
    if exc_name == "SoftTimeLimitExceeded":
        return (
            "抽帧超时，已超过视频同步任务的软超时限制。"
            f"当前限制为 {VIDEO_SYNC_TASK_SOFT_TIME_LIMIT // 60} 分钟。"
        )
    return exc_name


def _mark_stale_active_sync_tasks(now: datetime | None = None) -> list[int]:
    resolved_now = now or _utcnow()
    cutoff = resolved_now - STALE_ACTIVE_SYNC_AFTER
    stale_tasks = TaskLog.query.filter(
        TaskLog.task_type.in_(LEGACY_SYNC_TASK_TYPES),
        TaskLog.status.in_(ACTIVE_SYNC_STATUSES),
        TaskLog.finished_at.is_(None),
        TaskLog.started_at.is_not(None),
        TaskLog.started_at < cutoff,
    ).all()

    if not stale_tasks:
        return []

    stale_ids: list[int] = []
    for task in stale_tasks:
        stale_ids.append(task.id)
        mark_sync_task_failed(task, "同步任务长时间未完成，系统已自动标记为失败", now=resolved_now)

    db.session.commit()
    logger.warning("Marked stale video sync tasks as failed: %s", stale_ids)
    return stale_ids


def _find_active_sync_task() -> TaskLog | None:
    _mark_stale_active_sync_tasks()
    return TaskLog.query.filter(
        TaskLog.task_type.in_(LEGACY_SYNC_TASK_TYPES),
        TaskLog.status.in_(ACTIVE_SYNC_STATUSES),
    ).order_by(TaskLog.id.desc()).first()


def has_active_sync_task() -> bool:
    return _find_active_sync_task() is not None


def _get_scheduled_sync_target_date(cfg, now: datetime | None = None) -> date | None:
    manager = VideoSourceManager(cfg)
    runtime_source = manager.get_active_runtime_source()
    source_config = runtime_source.get("config") or {}
    hour, minute = _parse_trigger_time(source_config.get("download_trigger_time"))
    current_dt = _get_local_now(cfg, now)
    trigger_dt = current_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if current_dt < trigger_dt:
        return None

    target_date = current_dt.date()
    if _has_existing_sync_task(target_date):
        return None
    return target_date


def _resolve_target_date(cfg, date_str: str | None = None, now: datetime | None = None) -> date:
    if date_str:
        return date.fromisoformat(date_str)
    return _get_local_now(cfg, now).date()


def _make_video_source(runtime_source, app_config=None):
    """Return a concrete video source adapter for the resolved runtime source."""
    from app.services.video_sources.factory import build_video_source_adapter

    if not runtime_source:
        raise VideoSourceConfigError("未解析到可用的视频源")
    return build_video_source_adapter(runtime_source, app_config=app_config)


def _send_admin_alert(message: str):
    try:
        from flask import current_app
        from app.services.dingtalk import DingTalkService
        from app.models import User, RoleEnum
        cfg = current_app.config
        dt = DingTalkService(cfg)
        admins = User.query.filter_by(role=RoleEnum.admin, is_active=True).all()
        for admin in admins:
            dt.send_work_notification(
                [admin.dingtalk_user_id],
                {"msgtype": "text", "text": {"content": f"[营养监测系统告警] {message}"}},
            )
    except Exception as e:
        logger.error(f"Failed to send admin alert: {e}")
