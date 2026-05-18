import logging
import os
import shutil
import subprocess
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo

from celery_app import celery
from app import db
from app.models import CapturedImage, DailyMenu, TaskLog, ImageStatusEnum
from app.models.menu import MENU_NOT_CONFIGURED_ALERT_TYPE, RECOGNITION_MENU_SCOPE_ALL, is_menu_configured, menu_not_configured_message, normalize_recognition_menu_scope
from app.services.runtime_config import get_effective_config
from app.services.video_sources import VideoSourceConfigError, VideoSourceManager

logger = logging.getLogger(__name__)


LEGACY_SYNC_TASK_TYPES = ("video_source_sync", "nvr_download")
ACTIVE_SYNC_STATUSES = ("pending", "running")
VIDEO_SYNC_TASK_SOFT_TIME_LIMIT = 1800
VIDEO_SYNC_TASK_TIME_LIMIT = 2100
MANUAL_UPLOAD_TASK_SOFT_TIME_LIMIT = 7200
MANUAL_UPLOAD_TASK_TIME_LIMIT = 7500
DEFAULT_MEAL_WINDOWS = [
    {"start": "07:00", "end": "09:00"},
    {"start": "11:30", "end": "13:00"},
    {"start": "17:30", "end": "19:00"},
]
DEFAULT_VIDEO_STORAGE_PATH = "/data/nvr_cache"
DEFAULT_VIDEO_ANALYSIS_MAX_CONCURRENCY = 3
DEFAULT_VIDEO_RECORDING_RETENTION_DAYS = 3
STALE_ACTIVE_SYNC_AFTER = timedelta(hours=6)
TASK_PROGRESS_HEARTBEAT_KEY = "last_progress_at"
VIDEO_RECORDING_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".part"}


def _requires_configured_menu_for_recognition(cfg: dict) -> bool:
    return normalize_recognition_menu_scope(
        cfg.get("RECOGNITION_MENU_SCOPE", "meal"),
    ) != RECOGNITION_MENU_SCOPE_ALL


def _record_menu_not_configured_sync_alert(target_date: date) -> TaskLog:
    message = menu_not_configured_message(target_date)
    existing = TaskLog.query.filter(
        TaskLog.task_type == "video_source_sync",
        TaskLog.task_date == target_date,
        TaskLog.status == "failed",
        TaskLog.meta["alert_type"].as_string() == MENU_NOT_CONFIGURED_ALERT_TYPE,
    ).order_by(TaskLog.id.desc()).first()
    if existing:
        return existing

    task_log = TaskLog(
        task_type="video_source_sync",
        task_date=target_date,
        status="failed",
        error_message=message,
        error_count=1,
        finished_at=_utcnow(),
        meta={
            "alert_type": MENU_NOT_CONFIGURED_ALERT_TYPE,
            "status_text": message,
        },
    )
    db.session.add(task_log)
    db.session.commit()
    logger.warning(message)
    _send_admin_alert(message)
    return task_log


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

    cfg = get_effective_config(current_app.config)
    target_date = _resolve_target_date(cfg, date_str)
    menu = DailyMenu.query.filter_by(menu_date=target_date).first()
    if _requires_configured_menu_for_recognition(cfg) and not is_menu_configured(menu):
        task_log = _record_menu_not_configured_sync_alert(target_date)
        return {
            "skipped": True,
            "reason": MENU_NOT_CONFIGURED_ALERT_TYPE,
            "task_id": task_log.id,
            "date": target_date.isoformat(),
        }
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
        source_config = runtime_source.get("config") or {}
        analysis_cfg = _with_channel_roi_regions(cfg, source_config)
        meal_windows = _resolve_sync_meal_windows(cfg)
        channel_ids = _resolve_sync_channel_ids(source_config)
        analysis_max_concurrency = _resolve_analysis_max_concurrency(cfg)
        storage_path = source_config.get("local_storage_path") or DEFAULT_VIDEO_STORAGE_PATH
        retention_days = _resolve_video_recording_retention_days(source_config)
        image_path = cfg.get("IMAGE_STORAGE_PATH", "/data/images")
        task_meta = dict(task_log.meta or {})
        cleanup_result = _cleanup_expired_video_recordings(
            storage_path,
            retention_days,
            cfg,
            keep_dates={target_date},
        )
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
            "analysis_max_concurrency": analysis_max_concurrency,
            "recording_retention_days": retention_days,
            "recording_cleanup": cleanup_result,
        })
        _persist_task_meta(task_log, task_meta)
        db.session.commit()

        total_images = 0
        recording_jobs: list[dict] = []
        downloaded_recording_jobs: list[dict] = []
        used_recording_paths: set[str] = set()

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
                    video_start = _coerce_recording_datetime(rec.get("start_time"), start_dt)
                    video_end = _coerce_recording_datetime(rec.get("end_time"), end_dt)
                    source_start = _coerce_recording_datetime(rec.get("source_start_time"), video_start)
                    source_end = _coerce_recording_datetime(rec.get("source_end_time"), video_end)
                    video_filename = _dedupe_recording_filename(
                        _build_recording_filename(
                            runtime_source.get("source_type", ""),
                            channel_id,
                            video_start,
                            cfg,
                        ),
                        used_recording_paths,
                    )
                    video_save_path = os.path.join(storage_path, str(target_date), video_filename)
                    recording_meta = {
                        "channel_id": channel_id,
                        "window_start": start_dt.isoformat(),
                        "window_end": end_dt.isoformat(),
                        "filename": video_filename,
                        "relative_path": os.path.join(str(target_date), video_filename).replace("\\", "/"),
                        "recording_start": rec.get("start_time"),
                        "recording_end": rec.get("end_time"),
                        "source_start": rec.get("source_start_time"),
                        "source_end": rec.get("source_end_time"),
                        "download_status": "pending",
                        "frame_count": 0,
                        "image_ids": [],
                    }
                    task_meta["recordings"].append(recording_meta)
                    recording_jobs.append({
                        "channel_id": channel_id,
                        "video_filename": video_filename,
                        "video_save_path": video_save_path,
                        "video_start": video_start,
                        "video_end": video_end,
                        "source_start": source_start,
                        "source_end": source_end,
                        "download_url": rec.get("download_url", ""),
                        "recording_meta": recording_meta,
                    })
                    task_meta["status_text"] = f"已登记录像 {video_filename}，等待下载"
                    _persist_task_meta(task_log, task_meta)
                    db.session.commit()

        for job in recording_jobs:
            video_filename = job["video_filename"]
            video_save_path = job["video_save_path"]
            recording_meta = job["recording_meta"]

            task_meta["status_text"] = f"正在下载录像 {video_filename}"
            _persist_task_meta(task_log, task_meta)
            db.session.commit()

            resume_offset = os.path.getsize(video_save_path) if os.path.exists(video_save_path) else 0
            ok = video_source.download_recording(
                job["download_url"], video_save_path, resume_offset
            )
            if not ok:
                logger.error(f"Failed to download {video_filename}")
                recording_meta["download_status"] = "failed"
                task_meta["status_text"] = f"录像下载失败：{video_filename}"
                task_log.error_count = int(task_log.error_count or 0) + 1
                _persist_task_meta(task_log, task_meta)
                db.session.commit()
                continue

            trim_result = _trim_downloaded_recording_to_window(
                cfg,
                video_save_path,
                job.get("source_start"),
                job.get("source_end"),
                job.get("video_start"),
                job.get("video_end"),
            )
            if trim_result.get("trimmed"):
                recording_meta["trimmed"] = True
                recording_meta["trim_offset_seconds"] = trim_result.get("offset_seconds")
                recording_meta["trim_duration_seconds"] = trim_result.get("duration_seconds")
            elif trim_result.get("error"):
                logger.warning("Failed to trim %s: %s", video_filename, trim_result["error"])
                recording_meta["trim_error"] = trim_result["error"]

            recording_meta["download_status"] = "downloaded"
            downloaded_recording_jobs.append(job)
            task_meta["status_text"] = f"已下载录像 {video_filename}，等待抽帧"
            _persist_task_meta(task_log, task_meta)
            db.session.commit()

        if downloaded_recording_jobs:
            task_meta["status_text"] = (
                f"全部下载完成，开始并发抽帧（最大并发 {analysis_max_concurrency}）"
            )
            _persist_task_meta(task_log, task_meta)
            db.session.commit()

        with ThreadPoolExecutor(max_workers=analysis_max_concurrency) as executor:
            pending_futures: dict = {}
            remaining_jobs = iter(downloaded_recording_jobs)

            def submit_extract_job(job: dict) -> None:
                channel_id = job["channel_id"]
                video_filename = job["video_filename"]
                video_save_path = job["video_save_path"]
                video_start = job["video_start"]
                recording_meta = job["recording_meta"]

                recording_meta["download_status"] = "extracting"
                task_meta["status_text"] = f"正在抽帧 {video_filename}"
                _persist_task_meta(task_log, task_meta)
                db.session.commit()

                output_dir = os.path.join(image_path, str(target_date), channel_id)
                future = executor.submit(
                    _extract_frames_for_recording,
                    analysis_cfg,
                    video_save_path,
                    output_dir,
                    video_start,
                    channel_id,
                )
                pending_futures[future] = job

            for _ in range(min(analysis_max_concurrency, len(downloaded_recording_jobs))):
                submit_extract_job(next(remaining_jobs))

            while pending_futures:
                done_futures, _ = wait(
                    list(pending_futures.keys()),
                    return_when=FIRST_COMPLETED,
                )
                for future in done_futures:
                    job = pending_futures.pop(future)
                    channel_id = job["channel_id"]
                    video_filename = job["video_filename"]
                    recording_meta = job["recording_meta"]

                    try:
                        frames = future.result()
                    except Exception as e:
                        logger.error(f"Frame extraction failed for {video_filename}: {e}")
                        recording_meta["download_status"] = "frame_extract_failed"
                        recording_meta["error"] = _format_task_error(e)
                        task_meta["status_text"] = f"抽帧失败：{video_filename}"
                        task_log.error_count = int(task_log.error_count or 0) + 1
                        _persist_task_meta(task_log, task_meta)
                        db.session.commit()
                    else:
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
                        recording_meta["download_status"] = "success"
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

                    try:
                        submit_extract_job(next(remaining_jobs))
                    except StopIteration:
                        pass

        task_log.status = "partial" if int(task_log.error_count or 0) > 0 else "success"
        task_log.total_count = total_images
        task_log.success_count = total_images
        task_log.finished_at = _utcnow()
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
        task_log.finished_at = _utcnow()
        failed_meta = {
            **dict(task_log.meta or {}),
            "status_text": "视频源同步失败",
        }
        _persist_task_meta(task_log, failed_meta)
        db.session.commit()

        # Alert admin via DingTalk
        _send_admin_alert(f"视频源同步任务失败（{target_date}）: {str(e)[:200]}")
        raise self.retry(exc=e, countdown=300)


@celery.task(
    name="app.tasks.video.process_manual_video_upload",
    bind=True,
    soft_time_limit=MANUAL_UPLOAD_TASK_SOFT_TIME_LIMIT,
    time_limit=MANUAL_UPLOAD_TASK_TIME_LIMIT,
)
def process_manual_video_upload(
    self,
    task_log_id: int,
    video_path: str,
    output_dir: str,
    video_start_time_iso: str,
    channel_id: str,
    source_video: str,
):
    """Extract frames for a manually uploaded video outside the HTTP request."""
    from flask import current_app
    from app.services.video_analyzer import VideoAnalyzer

    task_log = TaskLog.query.get(task_log_id)
    if task_log is None:
        logger.warning("Manual upload task log %s no longer exists", task_log_id)
        return {"missing_task": True, "task_log_id": task_log_id}

    cfg = get_effective_config(current_app.config)
    try:
        runtime_source = VideoSourceManager(cfg).get_active_runtime_source()
        cfg = _with_channel_roi_regions(cfg, runtime_source.get("config") or {})
    except VideoSourceConfigError:
        pass
    capture_date = task_log.task_date
    video_start_time = datetime.fromisoformat(video_start_time_iso)

    task_meta = dict(task_log.meta or {})
    task_log.status = "running"
    task_meta.update({
        "status_text": "正在提取视频图片",
        "video_path": video_path,
        "output_dir": output_dir,
        "progress_percent": 0,
        "extracted_count": 0,
    })
    _persist_task_meta(task_log, task_meta)
    db.session.commit()

    last_persisted_percent = -1.0

    def persist_progress(progress: dict) -> None:
        nonlocal last_persisted_percent
        percent = progress.get("progress_percent")
        if percent is not None:
            percent = float(percent)
            if percent < 100.0 and last_persisted_percent >= 0 and percent - last_persisted_percent < 2.0:
                return
            last_persisted_percent = percent

        progress_meta = dict(task_log.meta or {})
        progress_meta.update({
            "status_text": (
                f"正在提取视频图片 {percent:.1f}%"
                if percent is not None
                else "正在提取视频图片"
            ),
            "progress_percent": percent,
            "current_frame": progress.get("frame_no"),
            "total_frames": progress.get("total_frames"),
            "extracted_count": progress.get("extracted_count"),
            "frame_step": progress.get("frame_step"),
            "effective_scan_fps": progress.get("effective_scan_fps"),
        })
        _persist_task_meta(task_log, progress_meta)
        db.session.commit()

    try:
        analyzer = VideoAnalyzer(cfg)
        frames = analyzer.extract_frames(
            video_path,
            output_dir,
            video_start_time,
            channel_id,
            progress_callback=persist_progress,
        )

        created_images: list[CapturedImage] = []
        for frame in frames:
            img = CapturedImage(
                capture_date=capture_date,
                channel_id=channel_id,
                captured_at=frame["captured_at"],
                image_path=frame["image_path"],
                status=ImageStatusEnum.pending,
                source_video=source_video,
                diff_score=frame.get("diff_score"),
                is_candidate=frame.get("is_candidate", False),
            )
            db.session.add(img)
            created_images.append(img)

        db.session.commit()

        image_ids = [img.id for img in created_images if img.id]
        primary_image_ids = [img.id for img in created_images if img.id and not img.is_candidate]
        candidate_image_ids = [img.id for img in created_images if img.id and img.is_candidate]
        total_images = len(created_images)

        task_log.status = "success"
        task_log.total_count = total_images
        task_log.success_count = total_images
        task_log.finished_at = _utcnow()
        task_meta = dict(task_log.meta or {})
        task_meta.update({
            "image_ids": image_ids,
            "primary_image_ids": primary_image_ids,
            "candidate_image_ids": candidate_image_ids,
            "primary_count": len(primary_image_ids),
            "candidate_count": len(candidate_image_ids),
            "progress_percent": 100.0,
            "extracted_count": total_images,
            "status_text": f"已提取 {total_images} 张图片（主帧 {len(primary_image_ids)}，候选帧 {len(candidate_image_ids)}）",
        })
        _persist_task_meta(task_log, task_meta)
        db.session.commit()

        if total_images > 0:
            from app.tasks.recognition import run_recognition_batch
            run_recognition_batch.delay(str(capture_date))

        logger.info("Manual video upload task %s complete: %s, extracted %s frames", task_log_id, source_video, total_images)
        return {
            "task_log_id": task_log_id,
            "frames_extracted": total_images,
            "capture_date": str(capture_date),
        }

    except Exception as e:
        logger.error("Manual video upload task %s failed: %s", task_log_id, e, exc_info=True)
        task_log.status = "failed"
        task_log.error_count = int(task_log.error_count or 0) + 1
        task_log.error_message = _format_task_error(e)
        task_log.finished_at = _utcnow()
        failed_meta = {
            **dict(task_log.meta or {}),
            "status_text": "视频处理失败",
        }
        _persist_task_meta(task_log, failed_meta)
        db.session.commit()
        raise


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

    menu = DailyMenu.query.filter_by(menu_date=target_date).first()
    if _requires_configured_menu_for_recognition(cfg) and not is_menu_configured(menu):
        task_log = _record_menu_not_configured_sync_alert(target_date)
        return {
            "scheduled": False,
            "reason": MENU_NOT_CONFIGURED_ALERT_TYPE,
            "task_id": task_log.id,
            "date": target_date.isoformat(),
        }

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


def _resolve_video_timezone(cfg) -> ZoneInfo:
    timezone_name = str(
        cfg.get("VIDEO_TIMEZONE")
        or cfg.get("APP_TIMEZONE")
        or "Asia/Shanghai"
    ).strip() or "Asia/Shanghai"
    try:
        return ZoneInfo(timezone_name)
    except Exception:
        logger.warning("Unknown VIDEO_TIMEZONE=%s, fallback to Asia/Shanghai", timezone_name)
        return ZoneInfo("Asia/Shanghai")


def _coerce_recording_datetime(value, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            logger.warning("Invalid recording start_time=%r, fallback to window start", value)
    return fallback


def _safe_filename_part(value, fallback: str = "unknown") -> str:
    normalized = str(value or "").strip()
    result = "".join(
        char if (char.isascii() and (char.isalnum() or char in {"-", "_"})) else "_"
        for char in normalized
    ).strip("_")
    return result or fallback


def _build_recording_filename(source_type: str, channel_id: str, recording_start: datetime, cfg) -> str:
    tz = _resolve_video_timezone(cfg)
    if recording_start.tzinfo is None:
        local_start = recording_start.replace(tzinfo=tz)
    else:
        local_start = recording_start.astimezone(tz)
    normalized_source_type = str(source_type or "").strip()
    if normalized_source_type == "nvr":
        prefix = "nvr"
    elif normalized_source_type == "hikvision_camera":
        prefix = "cam"
    else:
        prefix = "video"
    channel_part = _safe_filename_part(channel_id, "channel")
    time_part = local_start.strftime("%Y-%m-%d_%H-%M-%S")
    return f"{prefix}_ch{channel_part}_{time_part}.mp4"


def _dedupe_recording_filename(filename: str, used_filenames: set[str]) -> str:
    base, ext = os.path.splitext(filename)
    candidate = filename
    index = 2
    while candidate in used_filenames:
        candidate = f"{base}_{index}{ext}"
        index += 1
    used_filenames.add(candidate)
    return candidate


def _localize_recording_datetime(cfg, value: datetime) -> datetime:
    tz = _resolve_video_timezone(cfg)
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)


def _trim_downloaded_recording_to_window(
    cfg,
    video_path: str,
    source_start: datetime | None,
    source_end: datetime | None,
    clip_start: datetime | None,
    clip_end: datetime | None,
) -> dict:
    if not (source_start and source_end and clip_start and clip_end):
        return {"trimmed": False, "reason": "missing_time"}
    if not os.path.exists(video_path):
        return {"trimmed": False, "error": "downloaded_file_missing"}

    source_start_local = _localize_recording_datetime(cfg, source_start)
    source_end_local = _localize_recording_datetime(cfg, source_end)
    clip_start_local = _localize_recording_datetime(cfg, clip_start)
    clip_end_local = _localize_recording_datetime(cfg, clip_end)
    offset_seconds = max(0.0, (clip_start_local - source_start_local).total_seconds())
    duration_seconds = (clip_end_local - clip_start_local).total_seconds()
    source_duration_seconds = (source_end_local - source_start_local).total_seconds()
    if duration_seconds <= 0:
        return {"trimmed": False, "error": "invalid_clip_window"}
    if offset_seconds < 0.5 and abs(duration_seconds - source_duration_seconds) < 0.5:
        return {"trimmed": False, "reason": "already_window_sized"}

    base_path, ext = os.path.splitext(video_path)
    temp_path = f"{base_path}.trim{ext or '.mp4'}"
    ffmpeg_bin = str(cfg.get("FFMPEG_BIN") or "ffmpeg")
    command = [
        ffmpeg_bin,
        "-y",
        "-v",
        "error",
        "-ss",
        f"{offset_seconds:.3f}",
        "-t",
        f"{duration_seconds:.3f}",
        "-i",
        video_path,
        "-c",
        "copy",
        temp_path,
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        if not os.path.exists(temp_path) or os.path.getsize(temp_path) <= 0:
            return {"trimmed": False, "error": "ffmpeg_trim_empty_output"}
        os.replace(temp_path, video_path)
        return {
            "trimmed": True,
            "offset_seconds": round(offset_seconds, 3),
            "duration_seconds": round(duration_seconds, 3),
        }
    except Exception as exc:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        stderr = getattr(exc, "stderr", "") or ""
        return {"trimmed": False, "error": (stderr.strip() or str(exc))[:500]}


def _resolve_video_recording_retention_days(source_config) -> int:
    raw = source_config.get("retention_days", DEFAULT_VIDEO_RECORDING_RETENTION_DAYS)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid video retention_days=%r, fallback to %s",
            raw,
            DEFAULT_VIDEO_RECORDING_RETENTION_DAYS,
        )
        return DEFAULT_VIDEO_RECORDING_RETENTION_DAYS
    if value < 1:
        logger.warning(
            "Out-of-range video retention_days=%r, fallback to %s",
            raw,
            DEFAULT_VIDEO_RECORDING_RETENTION_DAYS,
        )
        return DEFAULT_VIDEO_RECORDING_RETENTION_DAYS
    return value


def _cleanup_expired_video_recordings(
    storage_path: str,
    retention_days: int,
    cfg,
    *,
    now: datetime | None = None,
    keep_dates: set[date] | None = None,
) -> dict:
    resolved_path = os.path.abspath(str(storage_path or "").strip() or DEFAULT_VIDEO_STORAGE_PATH)
    summary = {
        "storage_path": resolved_path,
        "retention_days": retention_days,
        "deleted_dirs": [],
        "deleted_files": [],
        "errors": [],
    }

    if resolved_path in {"/", ""}:
        summary["errors"].append("refuse_to_cleanup_unsafe_storage_path")
        return summary
    if not os.path.isdir(resolved_path):
        return summary

    local_now = _get_local_now(cfg, now)
    cutoff_date = local_now.date() - timedelta(days=max(1, retention_days) - 1)
    keep_date_values = keep_dates or set()
    summary["cutoff_date"] = cutoff_date.isoformat()

    for entry in os.scandir(resolved_path):
        entry_path = entry.path
        entry_date = _parse_recording_date_dir(entry.name)
        if entry_date and entry_date in keep_date_values:
            continue
        try:
            if entry_date and entry_date < cutoff_date:
                if entry.is_dir(follow_symlinks=False):
                    shutil.rmtree(entry_path)
                    summary["deleted_dirs"].append(entry.name)
                elif entry.is_file(follow_symlinks=False):
                    os.remove(entry_path)
                    summary["deleted_files"].append(entry.name)
                continue

            if entry.is_file(follow_symlinks=False) and _is_video_recording_file(entry.name):
                modified_at = datetime.fromtimestamp(entry.stat(follow_symlinks=False).st_mtime, local_now.tzinfo)
                if modified_at.date() < cutoff_date:
                    os.remove(entry_path)
                    summary["deleted_files"].append(entry.name)
        except Exception as exc:
            logger.warning("Failed to cleanup expired recording %s: %s", entry_path, exc)
            summary["errors"].append({"path": entry.name, "error": str(exc)})

    return summary


def _parse_recording_date_dir(name: str) -> date | None:
    try:
        return date.fromisoformat(str(name or "").strip())
    except ValueError:
        return None


def _is_video_recording_file(name: str) -> bool:
    return os.path.splitext(str(name or "").lower())[1] in VIDEO_RECORDING_EXTENSIONS


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


def _resolve_analysis_max_concurrency(cfg) -> int:
    raw = cfg.get("VIDEO_ANALYSIS_MAX_CONCURRENCY", DEFAULT_VIDEO_ANALYSIS_MAX_CONCURRENCY)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid VIDEO_ANALYSIS_MAX_CONCURRENCY=%r, fallback to %s",
            raw,
            DEFAULT_VIDEO_ANALYSIS_MAX_CONCURRENCY,
        )
        return DEFAULT_VIDEO_ANALYSIS_MAX_CONCURRENCY
    return max(1, value)


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


def _as_utc_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sync_task_stale_reference_at(task_log: TaskLog) -> datetime | None:
    meta = task_log.meta or {}
    progress_at = _as_utc_datetime(meta.get(TASK_PROGRESS_HEARTBEAT_KEY))
    started_at = _as_utc_datetime(task_log.started_at)
    candidates = [value for value in (progress_at, started_at) if value is not None]
    return max(candidates) if candidates else None


def _persist_task_meta(task_log: TaskLog, task_meta: dict) -> None:
    next_meta = deepcopy(task_meta)
    next_meta[TASK_PROGRESS_HEARTBEAT_KEY] = _utcnow().isoformat()
    task_log.meta = next_meta


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
    candidate_tasks = TaskLog.query.filter(
        TaskLog.task_type.in_(LEGACY_SYNC_TASK_TYPES),
        TaskLog.status.in_(ACTIVE_SYNC_STATUSES),
        TaskLog.finished_at.is_(None),
        TaskLog.started_at.is_not(None),
        TaskLog.started_at < cutoff,
    ).all()
    stale_tasks = [
        task
        for task in candidate_tasks
        if (reference_at := _sync_task_stale_reference_at(task)) is not None and reference_at < cutoff
    ]

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


def _extract_frames_for_recording(cfg, video_save_path: str, output_dir: str, video_start, channel_id: str):
    from app.services.video_analyzer import VideoAnalyzer

    analyzer = VideoAnalyzer(cfg)
    return analyzer.extract_frames(video_save_path, output_dir, video_start, channel_id)


def _with_channel_roi_regions(cfg: dict, source_config: dict) -> dict:
    channel_rois = _extract_channel_roi_regions(source_config)
    if not channel_rois:
        return cfg
    return {
        **dict(cfg),
        "VIDEO_CHANNEL_ROI_REGIONS": channel_rois,
    }


def _extract_channel_roi_regions(source_config: dict) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    cameras = source_config.get("cameras")
    if isinstance(cameras, list):
        for camera in cameras:
            if not isinstance(camera, dict):
                continue
            channel_id = str(camera.get("channel_id") or "").strip()
            roi_region = _normalize_roi_region(camera.get("roi_region"))
            if channel_id and roi_region:
                result[channel_id] = roi_region
    channel_rois = source_config.get("channel_rois")
    if isinstance(channel_rois, dict):
        for channel_id, roi_value in channel_rois.items():
            normalized_channel_id = str(channel_id or "").strip()
            roi_region = _normalize_roi_region(roi_value)
            if normalized_channel_id and roi_region:
                result[normalized_channel_id] = roi_region
    return result


def _normalize_roi_region(value) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    try:
        x = int(round(float(value.get("x"))))
        y = int(round(float(value.get("y"))))
        w = int(round(float(value.get("w"))))
        h = int(round(float(value.get("h"))))
    except (TypeError, ValueError):
        return None
    if x < 0 or y < 0 or w <= 0 or h <= 0:
        return None
    return {"x": x, "y": y, "w": w, "h": h}


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
