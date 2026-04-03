import logging
import os
from datetime import datetime, date
from zoneinfo import ZoneInfo

from celery_app import celery
from app import db
from app.models import CapturedImage, TaskLog, ImageStatusEnum
from app.services.video_sources import VideoSourceConfigError, VideoSourceManager

logger = logging.getLogger(__name__)


LEGACY_SYNC_TASK_TYPES = ("video_source_sync", "nvr_download")
DEFAULT_MEAL_WINDOWS = [
    {"start": "11:30", "end": "13:00"},
    {"start": "17:30", "end": "19:00"},
]
DEFAULT_VIDEO_STORAGE_PATH = "/data/nvr_cache"


@celery.task(name="app.tasks.video.sync_video_source_media", bind=True, max_retries=2)
def sync_video_source_media(self, date_str: str = None):
    """Synchronize recordings from the active video source and extract cashier frames."""
    from flask import current_app
    from app.services.video_analyzer import VideoAnalyzer

    cfg = current_app.config
    target_date = date.fromisoformat(date_str) if date_str else date.today()

    task_log = TaskLog(task_type="video_source_sync", task_date=target_date)
    db.session.add(task_log)
    db.session.commit()

    try:
        manager = VideoSourceManager(cfg)
        runtime_source = manager.get_active_runtime_source()
        video_source = _make_video_source(runtime_source)
        analyzer = VideoAnalyzer(cfg)

        source_config = runtime_source.get("config") or {}
        meal_windows = source_config.get("meal_windows") or list(DEFAULT_MEAL_WINDOWS)
        channel_ids = _resolve_sync_channel_ids(source_config)
        storage_path = source_config.get("local_storage_path") or DEFAULT_VIDEO_STORAGE_PATH
        image_path = cfg.get("IMAGE_STORAGE_PATH", "/data/images")

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
                    continue

                for rec in recordings:
                    video_filename = rec.get("filename", f"{channel_id}_{int(start_dt.timestamp())}.mp4")
                    video_save_path = os.path.join(storage_path, str(target_date), video_filename)

                    # Download
                    resume_offset = os.path.getsize(video_save_path) if os.path.exists(video_save_path) else 0
                    ok = video_source.download_recording(
                        rec.get("download_url", ""), video_save_path, resume_offset
                    )
                    if not ok:
                        logger.error(f"Failed to download {video_filename}")
                        continue

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
                        continue

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
                        total_images += 1

                    db.session.commit()

        task_log.status = "success"
        task_log.total_count = total_images
        task_log.success_count = total_images
        task_log.finished_at = datetime.utcnow()
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
        db.session.commit()

        # Alert admin via DingTalk
        _send_admin_alert(f"视频源同步任务失败（{target_date}）: {str(e)[:200]}")
        raise self.retry(exc=e, countdown=300)


@celery.task(name="app.tasks.video.schedule_video_source_sync")
def schedule_video_source_sync():
    """Periodically check whether the active video source should sync now."""
    from flask import current_app

    cfg = current_app.config
    try:
        target_date = _get_scheduled_sync_target_date(cfg)
    except VideoSourceConfigError as e:
        logger.info("Skip scheduled video source sync: %s", e)
        return {"scheduled": False, "reason": str(e)}

    if target_date is None:
        return {"scheduled": False}

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


def _get_scheduled_sync_target_date(cfg, now: datetime | None = None) -> date | None:
    manager = VideoSourceManager(cfg)
    runtime_source = manager.get_active_runtime_source()
    source_config = runtime_source.get("config") or {}
    hour, minute = _parse_trigger_time(source_config.get("download_trigger_time"))
    current_dt = _get_local_now(cfg, now)
    if current_dt.hour != hour or current_dt.minute != minute:
        return None

    target_date = current_dt.date()
    if _has_existing_sync_task(target_date):
        return None
    return target_date


def _make_video_source(runtime_source):
    """Return a concrete video source adapter for the resolved runtime source."""
    from app.services.video_sources.factory import build_video_source_adapter

    if not runtime_source:
        raise VideoSourceConfigError("未解析到可用的视频源")
    return build_video_source_adapter(runtime_source)


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
