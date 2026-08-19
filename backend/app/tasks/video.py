import json
import logging
import os
import re
import select
import shutil
import subprocess
import sys
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy
from datetime import datetime, date, timedelta, timezone
from queue import Empty, Queue
from threading import Event
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, text
from sqlalchemy.exc import IntegrityError

from celery_app import celery
from app import db
from app.models import CapturedImage, DailyMenu, TaskLog, ImageStatusEnum, VideoRecordingJob, VideoSource
from app.models.menu import (
    DEFAULT_MEAL_SLOTS,
    MENU_NOT_CONFIGURED_ALERT_TYPE,
    RECOGNITION_MENU_SCOPE_ALL,
    get_meal_slots,
    is_menu_configured,
    menu_not_configured_message,
    normalize_recognition_menu_scope,
)
from app.services.runtime_config import get_effective_config
from app.services.video_sources import VideoSourceConfigError, VideoSourceManager

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


LEGACY_SYNC_TASK_TYPES = ("video_source_sync", "nvr_download")
ACTIVE_SYNC_STATUSES = ("pending", "running")
VIDEO_SYNC_TASK_SOFT_TIME_LIMIT = _env_int("VIDEO_SYNC_TASK_SOFT_TIME_LIMIT", 43200)
VIDEO_SYNC_TASK_TIME_LIMIT = max(
    VIDEO_SYNC_TASK_SOFT_TIME_LIMIT + 300,
    _env_int("VIDEO_SYNC_TASK_TIME_LIMIT", 43800),
)
MANUAL_UPLOAD_TASK_SOFT_TIME_LIMIT = 7200
MANUAL_UPLOAD_TASK_TIME_LIMIT = 7500
RECORDING_DOWNLOAD_TASK_SOFT_TIME_LIMIT = _env_int("VIDEO_DOWNLOAD_TASK_SOFT_TIME_LIMIT", 1800)
RECORDING_DOWNLOAD_TASK_TIME_LIMIT = max(
    RECORDING_DOWNLOAD_TASK_SOFT_TIME_LIMIT + 300,
    _env_int("VIDEO_DOWNLOAD_TASK_TIME_LIMIT", 2100),
)
RECORDING_EXTRACT_TASK_SOFT_TIME_LIMIT = _env_int("VIDEO_RECORDING_TASK_SOFT_TIME_LIMIT", 10800)
RECORDING_EXTRACT_TASK_TIME_LIMIT = max(
    RECORDING_EXTRACT_TASK_SOFT_TIME_LIMIT + 300,
    _env_int("VIDEO_RECORDING_TASK_TIME_LIMIT", 11400),
)
# Deprecated standalone video-sync windows; sync windows are now derived from MEAL_SLOTS.
DEFAULT_MEAL_WINDOWS = [
    {"start": slot["start"], "end": slot["end"]}
    for slot in DEFAULT_MEAL_SLOTS
]
DEFAULT_VIDEO_STORAGE_PATH = "/data/nvr_cache"
DEFAULT_VIDEO_ANALYSIS_MAX_CONCURRENCY = 2
DEFAULT_VIDEO_RECORDING_RETENTION_DAYS = 3
DEFAULT_VIDEO_DOWNLOAD_MAX_IN_FLIGHT = 4
DEFAULT_VIDEO_EXTRACT_MAX_IN_FLIGHT = 4
DEFAULT_VIDEO_DISPATCH_LEASE_SECONDS = 300
DEFAULT_VIDEO_QUEUED_LEASE_SECONDS = 21600
DEFAULT_VIDEO_HEARTBEAT_LEASE_SECONDS = 1800
DEFAULT_VIDEO_DISPATCH_MAX_ATTEMPTS = 20
VIDEO_DISPATCH_ADVISORY_LOCK_ID = 0x564944454F  # "VIDEO"
VIDEO_SYNC_ENQUEUE_ADVISORY_LOCK_ID = 0x5653594E43  # "VSYNC"
EXTRACT_PROGRESS_POLL_SECONDS = 5.0
EXTRACT_PROGRESS_STALL_SECONDS = _env_int("VIDEO_EXTRACT_PROGRESS_STALL_SECONDS", 900)
EXTRACT_MAX_RUNTIME_SECONDS = _env_int("VIDEO_EXTRACT_MAX_RUNTIME_SECONDS", 7200)
EXTRACT_FFMPEG_TIMEOUT_SECONDS = _env_int("VIDEO_EXTRACT_FFMPEG_TIMEOUT_SECONDS", 1800)
EXTRACT_FALLBACK_INTERVAL_SECONDS = _env_int("VIDEO_EXTRACT_FALLBACK_INTERVAL_SECONDS", 30)
EXTRACT_FALLBACK_MAX_FRAMES = _env_int("VIDEO_EXTRACT_FALLBACK_MAX_FRAMES", 500)
STALE_ACTIVE_SYNC_AFTER = timedelta(hours=6)
TASK_PROGRESS_HEARTBEAT_KEY = "last_progress_at"
VIDEO_RECORDING_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".ps", ".part"}


def _config_int(cfg: dict, key: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(cfg.get(key, default)))
    except (TypeError, ValueError):
        return max(minimum, default)


def _nonnegative_int(value, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, default)


class _CancelableThreadPoolExecutor(ThreadPoolExecutor):
    """Do not make a Celery soft timeout wait for every extraction thread."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cancel_event = Event()

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.shutdown(wait=True)
        else:
            self.cancel_event.set()
            self.shutdown(wait=False, cancel_futures=True)
        return False


def _requires_configured_menu_for_recognition(cfg: dict) -> bool:
    return normalize_recognition_menu_scope(
        cfg.get("RECOGNITION_MENU_SCOPE", "all"),
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
def sync_video_source_media(self, date_str: str = None, task_log_id: int | None = None):
    """Synchronize recordings from the active video source and extract cashier frames."""
    from flask import current_app

    sync_started_monotonic = time.monotonic()
    cfg = get_effective_config(current_app.config)
    target_date = _resolve_target_date(cfg, date_str)
    task_log = None
    if task_log_id is not None:
        task_log = TaskLog.query.get(task_log_id)
        if task_log is None:
            logger.warning("Skip video source sync because reserved task %s is missing", task_log_id)
            return {"skipped": True, "reason": "reserved_task_missing", "task_id": task_log_id}
        if task_log.task_date != target_date or task_log.task_type not in LEGACY_SYNC_TASK_TYPES:
            logger.error(
                "Skip video source sync because reserved task %s does not match date/type",
                task_log_id,
            )
            return {"skipped": True, "reason": "reserved_task_mismatch", "task_id": task_log_id}

    menu = DailyMenu.query.filter_by(menu_date=target_date).first()
    if _requires_configured_menu_for_recognition(cfg) and not is_menu_configured(menu, cfg):
        if task_log is not None and task_log.status in ACTIVE_SYNC_STATUSES:
            message = menu_not_configured_message(target_date)
            mark_sync_task_failed(task_log, message)
            task_log.error_count = 1
            task_log.meta = {
                **dict(task_log.meta or {}),
                "alert_type": MENU_NOT_CONFIGURED_ALERT_TYPE,
                "status_text": message,
            }
            db.session.commit()
            logger.warning(message)
            _send_admin_alert(message)
        else:
            task_log = _record_menu_not_configured_sync_alert(target_date)
        return {
            "skipped": True,
            "reason": MENU_NOT_CONFIGURED_ALERT_TYPE,
            "task_id": task_log.id,
            "date": target_date.isoformat(),
        }

    if task_log_id is None:
        task_log, created = _reserve_video_source_sync_task(target_date, cfg)
        if not created:
            logger.warning(
                "Skip video source sync for %s because task %s is already %s",
                target_date,
                task_log.id,
                task_log.status,
            )
            return {
                "skipped": True,
                "reason": "active_task_exists",
                "active_task_id": task_log.id,
                "active_task_date": task_log.task_date.isoformat() if task_log.task_date else None,
            }

    claimed = TaskLog.query.filter(
        TaskLog.id == task_log.id,
        TaskLog.status == "pending",
    ).update({
        TaskLog.status: "running",
        TaskLog.finished_at: None,
        TaskLog.error_message: None,
    }, synchronize_session=False)
    db.session.commit()
    if not claimed:
        current = TaskLog.query.get(task_log.id)
        return {
            "skipped": True,
            "reason": "reserved_task_already_claimed",
            "task_id": task_log.id,
            "status": current.status if current is not None else None,
        }
    task_log = TaskLog.query.get(task_log.id)
    existing_recording_jobs = VideoRecordingJob.query.filter_by(task_log_id=task_log.id).count()
    if existing_recording_jobs:
        # A previous parent attempt already committed the distributed source
        # of truth. Resume those rows instead of rebuilding duplicate children.
        dispatch_result = _dispatch_available_video_recording_jobs()
        _refresh_distributed_sync_task(task_log.id)
        current = TaskLog.query.get(task_log.id)
        return {
            "resumed": True,
            "distributed": True,
            "task_id": task_log.id,
            "recording_jobs": existing_recording_jobs,
            "status": current.status if current is not None else None,
            "queued": dispatch_result["queued"],
            "published": dispatch_result["published"],
        }

    initial_meta = {
        **dict(task_log.meta or {}),
        "status_text": "正在查询录像",
        "sync_claimed_at": _utcnow().isoformat(),
        "recordings": [],
        "empty_windows": [],
        "image_ids": [],
        "primary_count": 0,
        "candidate_count": 0,
    }
    task_log.total_count = 0
    task_log.success_count = 0
    task_log.error_count = 0
    _persist_task_meta(task_log, initial_meta)
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
        analysis_max_pending = _resolve_analysis_max_pending(cfg, analysis_max_concurrency)
        extract_progress_stall_seconds = _extract_progress_stall_seconds(cfg)
        primary_extract_deadline = (
            sync_started_monotonic
            + VIDEO_SYNC_TASK_SOFT_TIME_LIMIT
            - _extract_degrade_before_sync_timeout_seconds(cfg)
        )
        storage_path = source_config.get("local_storage_path") or DEFAULT_VIDEO_STORAGE_PATH
        retention_days = _resolve_video_recording_retention_days(source_config)
        image_path = cfg.get("IMAGE_STORAGE_PATH", "/data/images")
        task_meta = dict(task_log.meta or {})
        cleanup_result = _cleanup_video_recordings_preserving_active_dates(
            storage_path,
            retention_days,
            cfg,
            target_date,
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
            "analysis_max_pending": analysis_max_pending,
            "effective_scan_fps": float(analysis_cfg.get("EVENT_SCAN_FPS", 12.0)),
            "decode_backend_requested": str(analysis_cfg.get("VIDEO_EXTRACT_DECODE_BACKEND", "opencv")),
            "recording_retention_days": retention_days,
            "recording_cleanup": cleanup_result,
        })
        _persist_task_meta(task_log, task_meta)
        db.session.commit()

        total_images = 0
        recording_jobs: list[dict] = []
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
                    source_start, source_time_basis = _resolve_recording_source_start(rec, cfg)
                    source_end = _coerce_recording_datetime(rec.get("source_end_time"), video_end)
                    video_filename = _dedupe_recording_filename(
                        _build_recording_filename(
                            runtime_source.get("source_type", ""),
                            channel_id,
                            source_start,
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
                        "source_start": source_start.isoformat(),
                        "source_start_reported": rec.get("source_start_time"),
                        "source_end": rec.get("source_end_time"),
                        "time_basis": source_time_basis,
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
                        "source_time_basis": source_time_basis,
                        "download_url": rec.get("download_url", ""),
                        "recording_meta": recording_meta,
                    })
                    task_meta["status_text"] = f"已登记录像 {video_filename}，等待下载"
                    _persist_task_meta(task_log, task_meta)
                    db.session.commit()

        recording_jobs = _round_robin_recording_jobs(recording_jobs)
        task_meta["scheduling_strategy"] = "channel_round_robin"
        _persist_task_meta(task_log, task_meta)
        db.session.commit()

        if _distributed_video_pipeline_enabled(cfg):
            return _dispatch_distributed_recording_jobs(
                task_log,
                task_meta,
                recording_jobs,
                video_source_id=runtime_source.get("id"),
                target_date=target_date,
                image_path=image_path,
            )

        with _CancelableThreadPoolExecutor(max_workers=analysis_max_concurrency) as executor:
            pending_futures: dict = {}
            progress_events: Queue = Queue()

            def submit_extract_job(job: dict) -> None:
                channel_id = job["channel_id"]
                video_filename = job["video_filename"]
                video_save_path = job["video_save_path"]
                video_start = job.get("analysis_start") or job.get("source_start") or job["video_start"]
                recording_meta = job["recording_meta"]

                recording_meta["download_status"] = "queued_for_extract"
                recording_meta["progress_percent"] = 0.0
                recording_meta["current_frame"] = 0
                recording_meta["total_frames"] = None
                recording_meta["extracted_count"] = 0
                recording_meta["extract_queued_at"] = _utcnow().isoformat()
                task_meta["status_text"] = f"等待抽帧 {video_filename}"
                _persist_task_meta(task_log, task_meta)
                db.session.commit()
                logger.info("Frame extraction queued for %s", video_filename)

                def enqueue_progress(progress: dict) -> None:
                    progress_events.put((job, dict(progress or {})))

                output_dir = os.path.join(image_path, str(target_date), channel_id)
                future = executor.submit(
                    _extract_frames_for_recording,
                    analysis_cfg,
                    video_save_path,
                    output_dir,
                    video_start,
                    channel_id,
                    enqueue_progress,
                    cancel_event=executor.cancel_event,
                    primary_deadline_monotonic=primary_extract_deadline,
                    window_start=job.get("video_start"),
                    window_end=job.get("video_end"),
                )
                pending_futures[future] = job

            def drain_progress_events() -> None:
                changed = False
                while True:
                    try:
                        job, progress = progress_events.get_nowait()
                    except Empty:
                        break
                    recording_meta = job["recording_meta"]
                    if recording_meta.get("download_status") not in ("queued_for_extract", "extracting", "extract_stalled"):
                        continue

                    video_filename = job["video_filename"]
                    if recording_meta.get("download_status") == "queued_for_extract":
                        recording_meta["download_status"] = "extracting"
                        recording_meta["extract_started_at"] = _utcnow().isoformat()
                        logger.info("Frame extraction started for %s", video_filename)
                    elif recording_meta.get("download_status") == "extract_stalled":
                        recording_meta["download_status"] = "extracting"
                        recording_meta.pop("stalled_at", None)
                        recording_meta.pop("stall_seconds", None)
                        recording_meta["stall_recovered_at"] = _utcnow().isoformat()
                        logger.info("Frame extraction progress recovered for %s", video_filename)

                    percent = progress.get("progress_percent")
                    if percent is not None:
                        percent = max(0.0, min(float(percent), 100.0))
                        recording_meta["progress_percent"] = round(percent, 1)
                    for progress_key, meta_key in (
                        ("frame_no", "current_frame"),
                        ("total_frames", "total_frames"),
                        ("extracted_count", "extracted_count"),
                        ("frame_step", "frame_step"),
                        ("effective_scan_fps", "effective_scan_fps"),
                        ("extract_attempt", "extract_attempt"),
                        ("extract_strategy", "extract_strategy"),
                        ("recovery_status", "recovery_status"),
                        ("recovery_error", "recovery_error"),
                        ("decode_backend", "decode_backend"),
                        ("frame_timestamp_basis", "frame_timestamp_basis"),
                        ("stream_start_time_seconds", "stream_start_time_seconds"),
                        ("decode_fallback_reason", "decode_fallback_reason"),
                        ("analysis_width", "analysis_width"),
                        ("analysis_height", "analysis_height"),
                        ("elapsed_seconds", "elapsed_seconds"),
                        ("processing_fps", "processing_fps"),
                        ("realtime_factor", "realtime_factor"),
                        ("stage_timings", "stage_timings"),
                    ):
                        if progress_key in progress:
                            recording_meta[meta_key] = progress.get(progress_key)
                    recording_meta["last_progress_at"] = _utcnow().isoformat()
                    if progress.get("recovery_status") in {"repairing", "retrying", "fallback"}:
                        task_meta["status_text"] = f"正在恢复抽帧 {video_filename}：{recording_meta.get('extract_strategy') or 'fallback'}"
                    else:
                        task_meta["status_text"] = (
                            f"正在抽帧 {video_filename}"
                            + (f" {recording_meta['progress_percent']:.1f}%" if percent is not None else "")
                        )
                    changed = True

                    log_percent = recording_meta.get("progress_percent")
                    last_logged = job.get("last_logged_progress_percent", -10.0)
                    if log_percent is not None and (log_percent >= last_logged + 10.0 or log_percent >= 100.0):
                        job["last_logged_progress_percent"] = log_percent
                        logger.info(
                            "Frame extraction progress for %s: %.1f%% frame=%s/%s extracted=%s scan_fps=%s",
                            video_filename,
                            log_percent,
                            recording_meta.get("current_frame"),
                            recording_meta.get("total_frames"),
                            recording_meta.get("extracted_count"),
                            recording_meta.get("effective_scan_fps"),
                        )

                if changed:
                    _persist_task_meta(task_log, task_meta)
                    db.session.commit()

            def mark_stalled_extract_jobs() -> None:
                stalled_filenames = _mark_stalled_extract_recordings(
                    task_meta,
                    list(pending_futures.values()),
                    _utcnow(),
                    extract_progress_stall_seconds,
                )
                if not stalled_filenames:
                    return
                task_meta["status_text"] = f"抽帧疑似卡住：{stalled_filenames[-1]}"
                _persist_task_meta(task_log, task_meta)
                db.session.commit()
                for stalled_filename in stalled_filenames:
                    logger.warning(
                        "Frame extraction stalled for %s: no progress for %s seconds",
                        stalled_filename,
                        extract_progress_stall_seconds,
                    )

            def handle_extract_result(future) -> None:
                nonlocal total_images
                job = pending_futures.pop(future)
                video_filename = job["video_filename"]
                recording_meta = job["recording_meta"]

                try:
                    frames = future.result()
                except Exception as e:
                    logger.error(f"Frame extraction failed for {video_filename}: {e}")
                    recording_meta["download_status"] = "frame_extract_failed"
                    recording_meta["error"] = _format_task_error(e)
                    recording_meta["extract_finished_at"] = _utcnow().isoformat()
                    task_meta["status_text"] = f"抽帧失败：{video_filename}"
                    task_log.error_count = int(task_log.error_count or 0) + 1
                    _persist_task_meta(task_log, task_meta)
                    db.session.commit()
                    return

                frame_strategies = sorted({
                    str(frame.get("extraction_strategy"))
                    for frame in frames
                    if frame.get("extraction_strategy")
                })
                if frame_strategies:
                    recording_meta["extract_strategies"] = frame_strategies
                    recording_meta["fallback_used"] = any(
                        _is_recovery_strategy(strategy) for strategy in frame_strategies
                    )
                decoder_strategies = sorted({
                    str(frame.get("decoder_strategy"))
                    for frame in frames
                    if frame.get("decoder_strategy")
                })
                decode_backends = sorted({
                    str(frame.get("decode_backend"))
                    for frame in frames
                    if frame.get("decode_backend")
                })
                if decoder_strategies:
                    recording_meta["decoder_strategies"] = decoder_strategies
                if decode_backends:
                    recording_meta["decode_backends"] = decode_backends
                    recording_meta["decode_backend"] = decode_backends[-1]
                timestamp_bases = sorted({
                    str(frame.get("frame_timestamp_basis"))
                    for frame in frames
                    if frame.get("frame_timestamp_basis")
                })
                if timestamp_bases:
                    recording_meta["frame_timestamp_bases"] = timestamp_bases
                    recording_meta["frame_timestamp_basis"] = timestamp_bases[-1]

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
                recording_meta["progress_percent"] = 100.0
                recording_meta["extract_finished_at"] = _utcnow().isoformat()
                task_meta["image_ids"].extend(created_image_ids)
                task_meta["primary_count"] += len([frame for frame in frames if not frame.get("is_candidate", False)])
                task_meta["candidate_count"] += len([frame for frame in frames if frame.get("is_candidate", False)])
                task_meta["status_text"] = f"已处理录像 {video_filename}，抽取 {len(frames)} 张图片"
                _persist_task_meta(task_log, task_meta)
                task_log.total_count = total_images
                task_log.success_count = total_images
                db.session.commit()

                primary_image_ids = [img.id for img in created_images if img.id and not img.is_candidate]
                if primary_image_ids:
                    try:
                        from app.tasks.recognition import enqueue_recognition_images

                        recognition_task = enqueue_recognition_images(
                            primary_image_ids,
                            target_date=target_date,
                        )
                        if recognition_task:
                            recording_meta["recognition_task_log_id"] = recognition_task.id
                            recording_meta["recognition_queued_count"] = recognition_task.total_count
                            _persist_task_meta(task_log, task_meta)
                            db.session.commit()
                    except Exception as recognition_error:
                        logger.error(
                            "Failed to enqueue recognition for recording %s: %s",
                            video_filename,
                            recognition_error,
                            exc_info=True,
                        )
                        recording_meta["recognition_queue_error"] = _format_task_error(recognition_error)
                        _persist_task_meta(task_log, task_meta)
                        db.session.commit()

            def drain_extract_jobs(block: bool) -> None:
                if not pending_futures:
                    return
                done_futures, _ = wait(
                    list(pending_futures.keys()),
                    timeout=EXTRACT_PROGRESS_POLL_SECONDS if block else 0,
                    return_when=FIRST_COMPLETED,
                )
                drain_progress_events()
                mark_stalled_extract_jobs()
                if not done_futures:
                    return
                for future in done_futures:
                    handle_extract_result(future)
                drain_progress_events()

            for job in recording_jobs:
                while len(pending_futures) >= analysis_max_pending:
                    drain_extract_jobs(block=True)
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
                    drain_extract_jobs(block=False)
                    continue

                # Anchor timestamps to the recording segment origin.  When
                # ISAPI omitted that field, source_start was recovered from the
                # recording filename while the job was created.  Never use the
                # clipped query-window start as the timestamp origin.
                job["analysis_start"] = job["source_start"]
                recording_meta["time_basis"] = job["source_time_basis"]
                recording_meta["analysis_start"] = (
                    job["analysis_start"].isoformat()
                    if hasattr(job["analysis_start"], "isoformat")
                    else str(job["analysis_start"])
                )
                _, window_offset, window_duration = _resolve_recording_window(
                    analysis_cfg,
                    job["analysis_start"],
                    job.get("video_start"),
                    job.get("video_end"),
                )
                recording_meta["timestamp_origin"] = recording_meta["analysis_start"]
                recording_meta["analysis_window_offset_seconds"] = window_offset
                recording_meta["analysis_window_duration_seconds"] = window_duration

                recording_meta["download_status"] = "downloaded"
                task_meta["status_text"] = f"已下载录像 {video_filename}，提交抽帧"
                _persist_task_meta(task_log, task_meta)
                db.session.commit()

                submit_extract_job(job)
                drain_extract_jobs(block=False)

            while pending_futures:
                drain_extract_jobs(block=True)

        sync_error_count = int(task_log.error_count or 0)
        task_log.status = "failed" if sync_error_count > 0 else "success"
        task_log.total_count = total_images
        task_log.success_count = total_images
        task_log.finished_at = _utcnow()
        task_meta["recording_count"] = len(task_meta["recordings"])
        task_meta["status_text"] = (
            f"同步完成，共查询到 {len(task_meta['recordings'])} 段录像，抽取 {total_images} 张图片"
            if task_log.status == "success"
            else f"同步失败，共查询到 {len(task_meta['recordings'])} 段录像，抽取 {total_images} 张图片，失败 {sync_error_count} 次"
        )
        if task_log.status == "failed":
            task_log.error_message = task_meta["status_text"]
        _persist_task_meta(task_log, task_meta)
        db.session.commit()

        if task_log.status != "success":
            logger.warning(
                "Video source sync failed for %s: %s extraction/download errors",
                target_date,
                sync_error_count,
            )

        logger.info(f"Video source sync complete for {target_date}: {total_images} images")

    except Exception as e:
        logger.error(f"Video source sync task failed: {e}", exc_info=True)
        request_retries = int(getattr(getattr(self, "request", None), "retries", 0) or 0)
        max_retries = int(getattr(self, "max_retries", 2) or 2)
        will_retry = request_retries < max_retries
        task_log.status = "pending" if will_retry else "failed"
        task_log.error_message = str(e)
        task_log.finished_at = None if will_retry else _utcnow()
        failed_meta = {
            **dict(task_log.meta or {}),
            "status_text": "视频源同步失败，等待自动重试" if will_retry else "视频源同步失败",
        }
        _persist_task_meta(task_log, failed_meta)
        db.session.commit()

        # Alert admin via DingTalk
        _send_admin_alert(f"视频源同步任务失败（{target_date}）: {str(e)[:200]}")
        if will_retry:
            raise self.retry(
                exc=e,
                countdown=300,
                args=(target_date.isoformat(), task_log.id),
                kwargs={},
            )
        raise


def _dispatch_distributed_recording_jobs(
    task_log: TaskLog,
    task_meta: dict,
    recording_jobs: list[dict],
    *,
    video_source_id: int | None,
    target_date: date,
    image_path: str,
) -> dict:
    persisted_jobs: list[VideoRecordingJob] = []
    channel_count = max(1, len({str(item["channel_id"]) for item in recording_jobs}))
    for index, item in enumerate(recording_jobs):
        recording_meta = item["recording_meta"]
        output_dir = os.path.join(image_path, str(target_date), item["channel_id"])
        job = VideoRecordingJob(
            task_log_id=task_log.id,
            video_source_id=video_source_id,
            channel_id=item["channel_id"],
            filename=item["video_filename"],
            video_path=item["video_save_path"],
            output_dir=output_dir,
            download_url=item.get("download_url") or "",
            recording_start=item.get("video_start"),
            recording_end=item.get("video_end"),
            source_start=item.get("source_start"),
            source_end=item.get("source_end"),
            status="pending",
            stage="awaiting_download",
            details={
                **{
                    key: value
                    for key, value in recording_meta.items()
                    if key not in {"download_status", "progress_percent", "frame_count", "image_ids"}
                },
                "dispatch_priority": min(9, index // channel_count),
            },
        )
        db.session.add(job)
        persisted_jobs.append(job)

    db.session.flush()
    for item, job in zip(recording_jobs, persisted_jobs):
        item["recording_meta"]["recording_job_id"] = job.id
        item["recording_meta"]["download_status"] = "pending"

    task_meta.update({
        "distributed_pipeline": True,
        "status_text": (
            f"已创建 {len(persisted_jobs)} 个录像任务，等待并行下载"
            if persisted_jobs
            else "同步完成，未查询到录像"
        ),
        "recording_count": len(persisted_jobs),
        "recordings": [item["recording_meta"] for item in recording_jobs],
    })
    _persist_task_meta(task_log, task_meta)
    if not persisted_jobs:
        task_log.status = "success"
        task_log.finished_at = _utcnow()
    db.session.commit()

    if not persisted_jobs:
        return {
            "scheduled": False,
            "distributed": True,
            "task_id": task_log.id,
            "recording_jobs": 0,
            "dispatch_errors": [],
        }

    dispatch_result = _dispatch_available_video_recording_jobs()
    failed_ids = set(dispatch_result["publish_failed"])
    dispatch_errors = [job.filename for job in persisted_jobs if job.id in failed_ids]
    _refresh_distributed_sync_task(task_log.id)
    return {
        "scheduled": True,
        "distributed": True,
        "task_id": task_log.id,
        "recording_jobs": len(persisted_jobs),
        "dispatch_errors": dispatch_errors,
    }


def _job_parent_is_active(job: VideoRecordingJob) -> bool:
    task = TaskLog.query.get(job.task_log_id)
    return bool(task is not None and task.status in ACTIVE_SYNC_STATUSES)


def _recording_job_request_id(task) -> str | None:
    request_id = getattr(getattr(task, "request", None), "id", None)
    return str(request_id) if request_id else None


def _recording_job_task_is_current(
    job: VideoRecordingJob,
    task_kind: str,
    request_id: str | None,
) -> bool:
    if request_id is None:
        return True
    field_name = "download_task_id" if task_kind == "download" else "extract_task_id"
    return str(getattr(job, field_name) or "") == request_id


def _claim_recording_job_execution(
    recording_job_id: int,
    task_kind: str,
    request_id: str | None,
) -> VideoRecordingJob | None:
    """Atomically move one queued job into a running stage.

    The persisted Celery task id acts as an execution token. Superseded broker
    messages are harmless even if they are delivered after a recovery dispatch.
    """
    now = _utcnow()
    if task_kind == "download":
        task_id_column = VideoRecordingJob.download_task_id
        allowed_statuses = ("pending", "retry_wait")
        allowed_stages = ("queued_download", "download_retry_wait")
        updates = {
            VideoRecordingJob.status: "downloading",
            VideoRecordingJob.stage: "downloading",
            VideoRecordingJob.download_attempt_count: VideoRecordingJob.download_attempt_count + 1,
            VideoRecordingJob.download_started_at: now,
            VideoRecordingJob.last_progress_at: now,
            VideoRecordingJob.lease_expires_at: now + timedelta(
                seconds=RECORDING_DOWNLOAD_TASK_TIME_LIMIT + DEFAULT_VIDEO_DISPATCH_LEASE_SECONDS
            ),
            VideoRecordingJob.next_dispatch_at: None,
        }
    else:
        task_id_column = VideoRecordingJob.extract_task_id
        allowed_statuses = ("queued_for_extract", "retry_wait")
        allowed_stages = ("queued_extract", "extract_retry_wait")
        updates = {
            VideoRecordingJob.status: "extracting",
            VideoRecordingJob.stage: "extracting",
            VideoRecordingJob.extract_attempt_count: VideoRecordingJob.extract_attempt_count + 1,
            VideoRecordingJob.extract_started_at: now,
            VideoRecordingJob.last_progress_at: now,
            VideoRecordingJob.lease_expires_at: now + timedelta(
                seconds=RECORDING_EXTRACT_TASK_TIME_LIMIT + DEFAULT_VIDEO_DISPATCH_LEASE_SECONDS
            ),
            VideoRecordingJob.next_dispatch_at: None,
        }

    query = VideoRecordingJob.query.filter(
        VideoRecordingJob.id == recording_job_id,
        VideoRecordingJob.status.in_(allowed_statuses),
        VideoRecordingJob.stage.in_(allowed_stages),
    )
    if request_id is not None:
        query = query.filter(task_id_column == request_id)
    claimed = query.update(updates, synchronize_session=False)
    db.session.commit()
    if not claimed:
        return None
    return VideoRecordingJob.query.get(recording_job_id)


def _recording_job_task_kind(job: VideoRecordingJob) -> str | None:
    if job.stage in {"awaiting_download", "queued_download", "downloading", "download_retry_wait"}:
        return "download"
    if job.stage in {"awaiting_extract", "queued_extract", "extracting", "extract_retry_wait"}:
        return "extract"
    return None


def _recording_job_failed_task_kind(job: VideoRecordingJob) -> str | None:
    details = job.details or {}
    failed_task_kind = str(details.get("failed_task_kind") or "").strip().lower()
    if failed_task_kind in {"download", "extract"}:
        return failed_task_kind
    error_code = str(job.error_code or "").lower()
    if error_code.startswith("download_"):
        return "download"
    if error_code.startswith("extract_"):
        return "extract"
    return _recording_job_task_kind(job)


def _set_recording_job_failed_task_kind(job: VideoRecordingJob, task_kind: str | None) -> None:
    if task_kind not in {"download", "extract"}:
        return
    details = dict(job.details or {})
    details["failed_task_kind"] = task_kind
    job.details = details


def _await_phase_changed_recording_job_recovery(
    job: VideoRecordingJob,
    task_kind: str,
    recovery_count: int,
) -> None:
    """Persist a phase-changing recovery for bounded dispatcher admission."""
    now = _utcnow()
    details = dict(job.details or {})
    details["recovery_count"] = recovery_count
    details["last_recovered_at"] = now.isoformat()
    details.pop("failed_task_kind", None)
    job.details = details
    job.recovery_count = recovery_count
    job.dispatch_attempt_count = 0
    job.published_at = None
    job.next_dispatch_at = None
    job.lease_expires_at = None
    job.finished_at = None
    job.error_code = None
    job.error_message = None
    # Clear both execution tokens so a message from the abandoned phase is
    # guaranteed to be superseded before the dispatcher admits the new phase.
    job.download_task_id = None
    job.extract_task_id = None
    if task_kind == "download":
        job.status = "pending"
        job.stage = "awaiting_download"
    else:
        job.status = "queued_for_extract"
        job.stage = "awaiting_extract"
    job.last_progress_at = now
    db.session.commit()


def _prepare_recording_job_dispatch(
    job: VideoRecordingJob,
    task_kind: str,
    *,
    priority: int | None = None,
    recovery_count: int | None = None,
    cfg: dict | None = None,
) -> str:
    """Commit a durable embedded-outbox entry before touching the broker."""
    cfg = cfg or {}
    now = _utcnow()
    task_id = str(uuid.uuid4())
    details = dict(job.details or {})
    details.pop("failed_task_kind", None)
    if priority is not None:
        details["dispatch_priority"] = priority
    if recovery_count is not None:
        details["recovery_count"] = recovery_count
        details["last_recovered_at"] = now.isoformat()
        job.recovery_count = recovery_count
    job.details = details
    job.finished_at = None
    job.dispatch_attempt_count = 0
    job.published_at = None
    job.next_dispatch_at = now
    job.lease_expires_at = now + timedelta(
        seconds=_config_int(
            cfg,
            "VIDEO_RECORDING_DISPATCH_LEASE_SECONDS",
            DEFAULT_VIDEO_DISPATCH_LEASE_SECONDS,
            minimum=60,
        )
    )
    if task_kind == "download":
        job.status = "pending"
        job.stage = "queued_download"
        job.download_task_id = task_id
    else:
        job.status = "queued_for_extract"
        job.stage = "queued_extract"
        job.extract_task_id = task_id
    job.last_progress_at = now
    db.session.commit()
    return task_id


def _publish_prepared_recording_job(
    recording_job_id: int,
    task_kind: str,
    task_id: str,
    *,
    priority: int | None = None,
    cfg: dict | None = None,
) -> bool:
    """Publish one committed outbox entry; duplicate delivery is harmless."""
    cfg = cfg or {}
    now = _utcnow()
    expected_stage = "queued_download" if task_kind == "download" else "queued_extract"
    job = VideoRecordingJob.query.get(recording_job_id)
    if (
        job is None
        or job.stage != expected_stage
        or not _recording_job_task_is_current(job, task_kind, task_id)
    ):
        return False
    if job.published_at is not None:
        return True
    next_dispatch_at = _as_utc_datetime(job.next_dispatch_at)
    if next_dispatch_at is not None and next_dispatch_at > now:
        return False

    attempt = int(job.dispatch_attempt_count or 0) + 1
    dispatch_lease_seconds = _config_int(
        cfg,
        "VIDEO_RECORDING_DISPATCH_LEASE_SECONDS",
        DEFAULT_VIDEO_DISPATCH_LEASE_SECONDS,
        minimum=60,
    )
    job.dispatch_attempt_count = attempt
    # If this process dies inside apply_async, the reconciler will retry the
    # same task id after this lease. Both messages may arrive, but only one can
    # atomically claim the recording job.
    job.next_dispatch_at = now + timedelta(seconds=dispatch_lease_seconds)
    job.lease_expires_at = job.next_dispatch_at
    db.session.commit()

    task = download_video_recording_job if task_kind == "download" else extract_video_recording_job
    options = {"args": [recording_job_id], "task_id": task_id}
    if priority is not None:
        options["priority"] = priority
    options["queue"] = "video-download" if task_kind == "download" else "video-extract"
    try:
        task.apply_async(**options)
    except Exception as exc:
        db.session.rollback()
        current = VideoRecordingJob.query.get(recording_job_id)
        if current is None or not _recording_job_task_is_current(current, task_kind, task_id):
            return False
        current.error_code = f"{task_kind}_dispatch_failed"
        current.error_message = _format_task_error(exc)
        max_attempts = _config_int(
            cfg,
            "VIDEO_RECORDING_DISPATCH_MAX_ATTEMPTS",
            DEFAULT_VIDEO_DISPATCH_MAX_ATTEMPTS,
        )
        if attempt >= max_attempts:
            current.status = "failed"
            current.stage = "dispatch_failed"
            current.error_code = f"{task_kind}_dispatch_attempts_exhausted"
            _set_recording_job_failed_task_kind(current, task_kind)
            current.finished_at = _utcnow()
            current.lease_expires_at = None
            current.next_dispatch_at = None
        else:
            backoff_seconds = min(300, 15 * (2 ** min(attempt - 1, 5)))
            current.next_dispatch_at = _utcnow() + timedelta(seconds=backoff_seconds)
            current.lease_expires_at = current.next_dispatch_at
        db.session.commit()
        return False

    published_at = _utcnow()
    task_id_column = (
        VideoRecordingJob.download_task_id
        if task_kind == "download"
        else VideoRecordingJob.extract_task_id
    )
    VideoRecordingJob.query.filter(
        VideoRecordingJob.id == recording_job_id,
        VideoRecordingJob.stage == expected_stage,
        task_id_column == task_id,
        VideoRecordingJob.published_at.is_(None),
    ).update({
        VideoRecordingJob.published_at: published_at,
        VideoRecordingJob.next_dispatch_at: None,
        VideoRecordingJob.lease_expires_at: published_at + timedelta(
            seconds=_config_int(
                cfg,
                "VIDEO_RECORDING_QUEUED_LEASE_SECONDS",
                DEFAULT_VIDEO_QUEUED_LEASE_SECONDS,
                minimum=3600,
            )
        ),
        VideoRecordingJob.last_progress_at: published_at,
        VideoRecordingJob.error_code: None,
        VideoRecordingJob.error_message: None,
    }, synchronize_session=False)
    db.session.commit()
    return True


def _queue_recording_job(
    job: VideoRecordingJob,
    task_kind: str,
    *,
    priority: int | None = None,
    terminal_on_publish_error: bool = False,
    recovery_count: int | None = None,
    cfg: dict | None = None,
) -> bool:
    """Prepare and publish a recording task without a commit/publish gap.

    ``terminal_on_publish_error`` remains accepted for compatibility, but
    transient broker failures now stay durable and are retried by reconciliation.
    """
    del terminal_on_publish_error
    task_id = _prepare_recording_job_dispatch(
        job,
        task_kind,
        priority=priority,
        recovery_count=recovery_count,
        cfg=cfg,
    )
    return _publish_prepared_recording_job(
        job.id,
        task_kind,
        task_id,
        priority=priority,
        cfg=cfg,
    )


def _dispatch_available_video_recording_jobs() -> dict:
    """Republish durable outbox rows, then fill bounded stage capacity."""
    from flask import current_app

    cfg = get_effective_config(current_app.config)
    lock_connection = None
    if db.engine.dialect.name == "postgresql":
        lock_connection = db.engine.connect()
        lock_acquired = bool(
            lock_connection.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": VIDEO_DISPATCH_ADVISORY_LOCK_ID},
            ).scalar()
        )
        if not lock_acquired:
            lock_connection.close()
            return {
                "queued": [],
                "published": [],
                "publish_failed": [],
                "lock_busy": True,
            }

    try:
        return _dispatch_available_video_recording_jobs_locked(cfg)
    finally:
        if lock_connection is not None:
            try:
                lock_connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": VIDEO_DISPATCH_ADVISORY_LOCK_ID},
                )
            finally:
                lock_connection.close()


def _dispatch_available_video_recording_jobs_locked(cfg: dict) -> dict:
    """Dispatch while the cross-process scheduler lock is held."""
    now = _utcnow()
    result = {"queued": [], "published": [], "publish_failed": []}
    refresh_task_ids: set[int] = set()

    unpublished_jobs = (
        VideoRecordingJob.query.join(TaskLog, TaskLog.id == VideoRecordingJob.task_log_id)
        .filter(
            TaskLog.status.in_(ACTIVE_SYNC_STATUSES),
            VideoRecordingJob.stage.in_(("queued_download", "queued_extract")),
            VideoRecordingJob.published_at.is_(None),
            or_(
                VideoRecordingJob.next_dispatch_at.is_(None),
                VideoRecordingJob.next_dispatch_at <= now,
            ),
        )
        .order_by(VideoRecordingJob.next_dispatch_at.asc(), VideoRecordingJob.id.asc())
        .limit(200)
        .all()
    )
    for job in unpublished_jobs:
        task_kind = _recording_job_task_kind(job)
        if task_kind is None:
            continue
        task_id = job.download_task_id if task_kind == "download" else job.extract_task_id
        if not task_id:
            task_id = _prepare_recording_job_dispatch(job, task_kind, cfg=cfg)
        priority = _nonnegative_int((job.details or {}).get("dispatch_priority"))
        if _publish_prepared_recording_job(
            job.id,
            task_kind,
            task_id,
            priority=priority,
            cfg=cfg,
        ):
            result["published"].append(job.id)
        else:
            result["publish_failed"].append(job.id)
            current = VideoRecordingJob.query.get(job.id)
            if current is not None and current.status == "failed":
                refresh_task_ids.add(current.task_log_id)

    stage_settings = (
        (
            "download",
            "awaiting_download",
            ("queued_download", "downloading", "download_retry_wait"),
            _config_int(
                cfg,
                "VIDEO_DOWNLOAD_MAX_IN_FLIGHT",
                DEFAULT_VIDEO_DOWNLOAD_MAX_IN_FLIGHT,
            ),
        ),
        (
            "extract",
            "awaiting_extract",
            ("queued_extract", "extracting", "extract_retry_wait"),
            _config_int(
                cfg,
                "VIDEO_EXTRACT_MAX_IN_FLIGHT",
                DEFAULT_VIDEO_EXTRACT_MAX_IN_FLIGHT,
            ),
        ),
    )
    for task_kind, awaiting_stage, in_flight_stages, capacity in stage_settings:
        in_flight = (
            db.session.query(VideoRecordingJob.id)
            .join(TaskLog, TaskLog.id == VideoRecordingJob.task_log_id)
            .filter(
                TaskLog.status.in_(ACTIVE_SYNC_STATUSES),
                VideoRecordingJob.stage.in_(in_flight_stages),
            )
            .count()
        )
        for _ in range(max(0, capacity - in_flight)):
            job = (
                VideoRecordingJob.query.join(TaskLog, TaskLog.id == VideoRecordingJob.task_log_id)
                .filter(
                    TaskLog.status.in_(ACTIVE_SYNC_STATUSES),
                    VideoRecordingJob.stage == awaiting_stage,
                )
                .order_by(VideoRecordingJob.task_log_id.asc(), VideoRecordingJob.id.asc())
                .with_for_update(skip_locked=True)
                .first()
            )
            if job is None:
                break
            priority = _nonnegative_int((job.details or {}).get("dispatch_priority"))
            published = _queue_recording_job(
                job,
                task_kind,
                priority=priority,
                cfg=cfg,
            )
            result["queued"].append(job.id)
            if published:
                result["published"].append(job.id)
            else:
                result["publish_failed"].append(job.id)

            current = VideoRecordingJob.query.get(job.id)
            if current is not None and current.status == "failed":
                refresh_task_ids.add(current.task_log_id)

    for task_log_id in refresh_task_ids:
        _refresh_distributed_sync_task(task_log_id)

    return result


def _runtime_source_for_recording_job(job: VideoRecordingJob, cfg: dict) -> dict:
    source = VideoSource.query.get(job.video_source_id) if job.video_source_id else None
    if source is None:
        raise VideoSourceConfigError("录像任务关联的视频源已不存在")
    return VideoSourceManager(cfg).build_runtime_source(source)


@celery.task(
    name="app.tasks.video.download_video_recording_job",
    bind=True,
    max_retries=2,
    soft_time_limit=RECORDING_DOWNLOAD_TASK_SOFT_TIME_LIMIT,
    time_limit=RECORDING_DOWNLOAD_TASK_TIME_LIMIT,
)
def download_video_recording_job(self, recording_job_id: int):
    from flask import current_app

    request_id = _recording_job_request_id(self)
    job = VideoRecordingJob.query.get(recording_job_id)
    if job is None:
        return {"missing": True, "recording_job_id": recording_job_id}
    if not _recording_job_task_is_current(job, "download", request_id):
        return {"skipped": True, "reason": "superseded", "recording_job_id": job.id}
    if job.status in {"downloaded", "queued_for_extract", "extracting", "success"}:
        return {"skipped": True, "status": job.status, "recording_job_id": job.id}
    if not _job_parent_is_active(job):
        job.status = "cancelled"
        job.stage = "cancelled"
        job.finished_at = _utcnow()
        job.last_progress_at = job.finished_at
        job.lease_expires_at = None
        job.next_dispatch_at = None
        db.session.commit()
        _dispatch_available_video_recording_jobs()
        return {"cancelled": True, "recording_job_id": job.id}

    job = _claim_recording_job_execution(recording_job_id, "download", request_id)
    if job is None:
        current = VideoRecordingJob.query.get(recording_job_id)
        return {
            "skipped": True,
            "reason": "not_claimed",
            "status": current.status if current else None,
            "recording_job_id": recording_job_id,
        }
    cfg = get_effective_config(current_app.config)

    try:
        runtime_source = _runtime_source_for_recording_job(job, cfg)
        video_source = _make_video_source(runtime_source, app_config=cfg)
        resume_offset = os.path.getsize(job.video_path) if os.path.exists(job.video_path) else 0
        if not video_source.download_recording(job.download_url, job.video_path, resume_offset):
            raise RuntimeError("视频源返回下载失败")

        details = dict(job.details or {})
        analysis_start, time_basis = _resolve_recording_job_source_start(job, cfg)
        if "source_start_reported" not in details:
            details["source_start_reported"] = details.get("source_start")
        details["source_start"] = analysis_start.isoformat()
        details["time_basis"] = time_basis
        details["analysis_start"] = analysis_start.isoformat() if analysis_start else None
        db.session.expire_all()
        job = VideoRecordingJob.query.get(recording_job_id)
        if job is None or not _recording_job_task_is_current(job, "download", request_id):
            return {"skipped": True, "reason": "superseded", "recording_job_id": recording_job_id}
        if not _job_parent_is_active(job):
            job.status = "cancelled"
            job.stage = "cancelled"
            job.last_progress_at = _utcnow()
            job.finished_at = job.last_progress_at
            job.lease_expires_at = None
            job.next_dispatch_at = None
            db.session.commit()
            _dispatch_available_video_recording_jobs()
            return {"cancelled": True, "recording_job_id": recording_job_id}
        job.source_start = analysis_start
        job.details = details
        job.status = "queued_for_extract"
        job.stage = "awaiting_extract"
        job.download_finished_at = _utcnow()
        job.last_progress_at = job.download_finished_at
        job.dispatch_attempt_count = 0
        job.published_at = None
        job.next_dispatch_at = None
        job.lease_expires_at = None
        db.session.commit()

        task_log_id = job.task_log_id
        _dispatch_available_video_recording_jobs()
        _refresh_distributed_sync_task(task_log_id)
        current = VideoRecordingJob.query.get(recording_job_id)
        extract_queued = bool(
            current
            and current.stage in {"queued_extract", "extracting", "extract_retry_wait"}
        )
        return {"downloaded": True, "extract_queued": extract_queued, "recording_job_id": job.id}
    except Exception as exc:
        db.session.rollback()
        job = VideoRecordingJob.query.get(recording_job_id)
        if job is None:
            raise
        if not _recording_job_task_is_current(job, "download", request_id):
            return {"skipped": True, "reason": "superseded", "recording_job_id": recording_job_id}
        retries = int(getattr(self.request, "retries", 0) or 0)
        if retries < int(getattr(self, "max_retries", 2) or 2):
            countdown = min(300, 30 * (2 ** retries))
            retry_at = _utcnow()
            job.status = "retry_wait"
            job.stage = "download_retry_wait"
            job.error_code = "download_failed"
            job.error_message = _format_task_error(exc)
            job.last_progress_at = retry_at
            job.lease_expires_at = retry_at + timedelta(
                seconds=countdown + RECORDING_DOWNLOAD_TASK_TIME_LIMIT + DEFAULT_VIDEO_DISPATCH_LEASE_SECONDS
            )
            db.session.commit()
            raise self.retry(exc=exc, countdown=countdown)
        _finish_recording_job_failure(job, "download_failed", exc)
        return {"failed": True, "recording_job_id": job.id, "error": _format_task_error(exc)}


@celery.task(
    name="app.tasks.video.extract_video_recording_job",
    bind=True,
    max_retries=1,
    soft_time_limit=RECORDING_EXTRACT_TASK_SOFT_TIME_LIMIT,
    time_limit=RECORDING_EXTRACT_TASK_TIME_LIMIT,
)
def extract_video_recording_job(self, recording_job_id: int):
    from flask import current_app

    request_id = _recording_job_request_id(self)
    job = VideoRecordingJob.query.get(recording_job_id)
    if job is None:
        return {"missing": True, "recording_job_id": recording_job_id}
    if not _recording_job_task_is_current(job, "extract", request_id):
        return {"skipped": True, "reason": "superseded", "recording_job_id": job.id}
    if job.status == "success":
        return {"skipped": True, "status": job.status, "recording_job_id": job.id}
    if not _job_parent_is_active(job):
        job.status = "cancelled"
        job.stage = "cancelled"
        job.finished_at = _utcnow()
        job.last_progress_at = job.finished_at
        job.lease_expires_at = None
        job.next_dispatch_at = None
        db.session.commit()
        _dispatch_available_video_recording_jobs()
        return {"cancelled": True, "recording_job_id": job.id}

    job = _claim_recording_job_execution(recording_job_id, "extract", request_id)
    if job is None:
        current = VideoRecordingJob.query.get(recording_job_id)
        return {
            "skipped": True,
            "reason": "not_claimed",
            "status": current.status if current else None,
            "recording_job_id": recording_job_id,
        }
    cfg = get_effective_config(current_app.config)
    try:
        details = dict(job.details or {})
        analysis_start, time_basis = _resolve_recording_job_source_start(job, cfg)
        job.source_start = analysis_start
        if "source_start_reported" not in details:
            details["source_start_reported"] = details.get("source_start")
        details["source_start"] = analysis_start.isoformat()
        details["time_basis"] = time_basis
        details["analysis_start"] = analysis_start.isoformat()
        _, window_offset, window_duration = _resolve_recording_window(
            cfg,
            analysis_start,
            job.recording_start,
            job.recording_end,
        )
        details["timestamp_origin"] = details["analysis_start"]
        details["analysis_window_offset_seconds"] = window_offset
        details["analysis_window_duration_seconds"] = window_duration
        job.details = details
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        job = VideoRecordingJob.query.get(recording_job_id)
        if job is None or not _recording_job_task_is_current(job, "extract", request_id):
            return {"skipped": True, "reason": "superseded", "recording_job_id": recording_job_id}
        _finish_recording_job_failure(job, "extract_timestamp_origin_failed", exc)
        return {"failed": True, "recording_job_id": recording_job_id, "error": _format_task_error(exc)}

    cancel_event = Event()
    last_persisted_at = 0.0
    last_parent_refresh_at = 0.0
    last_percent = -1.0

    def persist_progress(progress: dict) -> None:
        nonlocal last_parent_refresh_at, last_persisted_at, last_percent
        now_monotonic = time.monotonic()
        percent = progress.get("progress_percent")
        meaningful_percent = percent is not None and float(percent) >= last_percent + 1.0
        if not meaningful_percent and now_monotonic - last_persisted_at < 5.0:
            return
        db.session.expire_all()
        current = VideoRecordingJob.query.get(recording_job_id)
        if (
            current is None
            or not _job_parent_is_active(current)
            or not _recording_job_task_is_current(current, "extract", request_id)
        ):
            cancel_event.set()
            return
        if percent is not None:
            last_percent = max(last_percent, float(percent))
            current.progress_percent = max(0.0, min(100.0, float(percent)))
        current.current_frame = progress.get("frame_no", current.current_frame)
        current.total_frames = progress.get("total_frames", current.total_frames)
        current.extracted_count = int(progress.get("extracted_count", current.extracted_count) or 0)
        current.extraction_strategy = progress.get("extract_strategy", current.extraction_strategy)
        details = dict(current.details or {})
        for key in (
            "effective_scan_fps",
            "frame_step",
            "recovery_status",
            "recovery_error",
            "ffmpeg_out_time",
            "ffmpeg_speed",
            "decode_backend",
            "frame_timestamp_basis",
            "stream_start_time_seconds",
            "decode_fallback_reason",
            "analysis_width",
            "analysis_height",
            "elapsed_seconds",
            "processing_fps",
            "realtime_factor",
            "stage_timings",
        ):
            if key in progress:
                details[key] = progress.get(key)
        current.details = details
        current.last_progress_at = _utcnow()
        current.lease_expires_at = current.last_progress_at + timedelta(
            seconds=_config_int(
                cfg,
                "VIDEO_RECORDING_HEARTBEAT_LEASE_SECONDS",
                DEFAULT_VIDEO_HEARTBEAT_LEASE_SECONDS,
                minimum=600,
            )
        )
        task_log_id = current.task_log_id
        db.session.commit()
        last_persisted_at = now_monotonic
        if now_monotonic - last_parent_refresh_at >= 30.0:
            _refresh_distributed_sync_task(task_log_id)
            last_parent_refresh_at = now_monotonic

    try:
        runtime_source = _runtime_source_for_recording_job(job, cfg)
        analysis_cfg = _with_channel_roi_regions(cfg, runtime_source.get("config") or {})
        frames = _extract_frames_for_recording(
            analysis_cfg,
            job.video_path,
            job.output_dir,
            analysis_start,
            job.channel_id,
            persist_progress,
            cancel_event=cancel_event,
            window_start=job.recording_start,
            window_end=job.recording_end,
        )
        db.session.expire_all()
        job = VideoRecordingJob.query.get(recording_job_id)
        if job is None or not _recording_job_task_is_current(job, "extract", request_id):
            return {"skipped": True, "reason": "superseded", "recording_job_id": recording_job_id}
        if not _job_parent_is_active(job):
            job.status = "cancelled"
            job.stage = "cancelled"
            job.last_progress_at = _utcnow()
            job.finished_at = job.last_progress_at
            job.lease_expires_at = None
            job.next_dispatch_at = None
            db.session.commit()
            _dispatch_available_video_recording_jobs()
            return {"cancelled": True, "recording_job_id": recording_job_id}
        existing_images = CapturedImage.query.filter_by(video_recording_job_id=job.id).all()
        created_images = list(existing_images)
        if not existing_images:
            for frame in frames:
                image = CapturedImage(
                    capture_date=job.task_log.task_date,
                    channel_id=frame["channel_id"],
                    captured_at=frame["captured_at"],
                    image_path=frame["image_path"],
                    status=ImageStatusEnum.pending,
                    source_video=job.filename,
                    video_recording_job_id=job.id,
                    diff_score=frame.get("diff_score"),
                    is_candidate=frame.get("is_candidate", False),
                )
                db.session.add(image)
                created_images.append(image)
            db.session.flush()

        strategies = sorted({str(frame.get("extraction_strategy")) for frame in frames if frame.get("extraction_strategy")})
        decoder_strategies = sorted({str(frame.get("decoder_strategy")) for frame in frames if frame.get("decoder_strategy")})
        decode_backends = sorted({str(frame.get("decode_backend")) for frame in frames if frame.get("decode_backend")})
        timestamp_bases = sorted({str(frame.get("frame_timestamp_basis")) for frame in frames if frame.get("frame_timestamp_basis")})
        details = dict(job.details or {})
        details["image_ids"] = [image.id for image in created_images if image.id]
        details["extract_strategies"] = strategies
        details["decoder_strategies"] = decoder_strategies
        details["decode_backends"] = decode_backends
        details["frame_timestamp_bases"] = timestamp_bases
        if decode_backends:
            details["decode_backend"] = decode_backends[-1]
        if timestamp_bases:
            details["frame_timestamp_basis"] = timestamp_bases[-1]
        job.details = details
        job.status = "success"
        job.stage = "complete"
        job.progress_percent = 100.0
        job.frame_count = len(created_images)
        job.extracted_count = len(created_images)
        job.extraction_strategy = strategies[-1] if strategies else job.extraction_strategy
        job.fallback_used = any(_is_recovery_strategy(strategy) for strategy in strategies)
        job.error_code = None
        job.error_message = None
        job.extract_finished_at = _utcnow()
        job.last_progress_at = job.extract_finished_at
        job.finished_at = job.extract_finished_at
        job.lease_expires_at = None
        job.next_dispatch_at = None
        db.session.commit()

        primary_ids = [image.id for image in created_images if image.id and not image.is_candidate]
        if primary_ids:
            try:
                from app.tasks.recognition import enqueue_recognition_images

                recognition_task = enqueue_recognition_images(primary_ids, target_date=job.task_log.task_date)
                if recognition_task:
                    details = dict(job.details or {})
                    details["recognition_task_log_id"] = recognition_task.id
                    details["recognition_queued_count"] = recognition_task.total_count
                    job.details = details
                    db.session.commit()
            except Exception as recognition_error:
                logger.error("Failed to enqueue recognition for recording job %s: %s", job.id, recognition_error, exc_info=True)

        task_log_id = job.task_log_id
        _dispatch_available_video_recording_jobs()
        _refresh_distributed_sync_task(task_log_id)
        return {"success": True, "recording_job_id": job.id, "frames_extracted": job.frame_count}
    except InterruptedError as exc:
        db.session.rollback()
        job = VideoRecordingJob.query.get(recording_job_id)
        if job is None or not _recording_job_task_is_current(job, "extract", request_id):
            return {"skipped": True, "reason": "superseded", "recording_job_id": recording_job_id}
        _finish_recording_job_failure(job, "extract_cancelled", exc, status="cancelled")
        return {"cancelled": True, "recording_job_id": recording_job_id}
    except Exception as exc:
        db.session.rollback()
        job = VideoRecordingJob.query.get(recording_job_id)
        if job is None or not _recording_job_task_is_current(job, "extract", request_id):
            return {"skipped": True, "reason": "superseded", "recording_job_id": recording_job_id}
        retries = int(getattr(self.request, "retries", 0) or 0)
        if retries < int(getattr(self, "max_retries", 1) or 1):
            retry_at = _utcnow()
            job.status = "retry_wait"
            job.stage = "extract_retry_wait"
            job.error_code = "extract_failed"
            job.error_message = _format_task_error(exc)
            job.last_progress_at = retry_at
            job.lease_expires_at = retry_at + timedelta(
                seconds=60 + RECORDING_EXTRACT_TASK_TIME_LIMIT + DEFAULT_VIDEO_DISPATCH_LEASE_SECONDS
            )
            db.session.commit()
            raise self.retry(exc=exc, countdown=60)
        _finish_recording_job_failure(job, "extract_failed", exc)
        return {"failed": True, "recording_job_id": recording_job_id, "error": _format_task_error(exc)}


def _finish_recording_job_failure(
    job: VideoRecordingJob,
    error_code: str,
    exc: Exception,
    *,
    status: str = "failed",
) -> None:
    job.status = status
    job.stage = status
    job.error_code = error_code
    job.error_message = _format_task_error(exc)
    failed_task_kind = None
    if error_code.startswith("download"):
        failed_task_kind = "download"
    elif error_code.startswith("extract"):
        failed_task_kind = "extract"
    _set_recording_job_failed_task_kind(job, failed_task_kind)
    job.last_progress_at = _utcnow()
    job.finished_at = job.last_progress_at
    job.lease_expires_at = None
    job.next_dispatch_at = None
    if error_code.startswith("extract"):
        job.extract_finished_at = job.last_progress_at
    task_log_id = job.task_log_id
    db.session.commit()
    _dispatch_available_video_recording_jobs()
    _refresh_distributed_sync_task(task_log_id)


def _refresh_distributed_sync_task(task_log_id: int) -> None:
    task = TaskLog.query.get(task_log_id)
    if task is None:
        return
    jobs = VideoRecordingJob.query.filter_by(task_log_id=task_log_id).order_by(VideoRecordingJob.id).all()
    if not jobs:
        return

    job_ids = [job.id for job in jobs]
    images = CapturedImage.query.filter(CapturedImage.video_recording_job_id.in_(job_ids)).all()
    success_jobs = [job for job in jobs if job.status == "success"]
    failed_jobs = [job for job in jobs if job.status in {"failed", "cancelled"}]
    terminal_count = len(success_jobs) + len(failed_jobs)
    stage_counts: dict[str, int] = {}
    for job in jobs:
        stage_counts[job.stage] = stage_counts.get(job.stage, 0) + 1
    primary_count = len([image for image in images if not image.is_candidate])
    candidate_count = len(images) - primary_count
    task.total_count = len(images)
    task.success_count = len(images)
    task.error_count = len(failed_jobs)
    meta = dict(task.meta or {})
    meta.update({
        "recordings": [job.to_recording_meta() for job in jobs],
        "recording_count": len(jobs),
        "completed_recording_count": len(success_jobs),
        "failed_recording_count": len(failed_jobs),
        "active_recording_count": len(jobs) - terminal_count,
        "image_ids": [image.id for image in images],
        "primary_count": primary_count,
        "candidate_count": candidate_count,
        "pipeline_stage_counts": stage_counts,
        "status_text": (
            f"录像处理中 {terminal_count}/{len(jobs)}，已抽取 {len(images)} 张图片"
            if terminal_count < len(jobs)
            else f"录像处理完成 {len(success_jobs)}/{len(jobs)}，抽取 {len(images)} 张图片"
        ),
    })
    if terminal_count == len(jobs):
        if task.status in ACTIVE_SYNC_STATUSES:
            task.status = "success" if not failed_jobs else ("partial" if success_jobs else "failed")
            task.finished_at = _utcnow()
            task.error_message = None if not failed_jobs else f"{len(failed_jobs)} 段录像处理失败"
    elif task.status in ACTIVE_SYNC_STATUSES:
        task.status = "running"
        task.started_at = task.started_at or _utcnow()
        task.finished_at = None
        task.error_message = None
    _persist_task_meta(task, meta)
    db.session.commit()


@celery.task(name="app.tasks.video.recover_stale_video_recording_jobs")
def recover_stale_video_recording_jobs():
    """Reconcile the durable outbox and recover only expired execution leases."""
    from flask import current_app

    cfg = get_effective_config(current_app.config)
    parent_dispatch_result = _dispatch_pending_video_sync_tasks(cfg)
    stale_seconds = _config_int(cfg, "VIDEO_RECORDING_JOB_STALE_SECONDS", 7200, minimum=600)
    max_recoveries = _config_int(cfg, "VIDEO_RECORDING_JOB_MAX_RECOVERIES", 5)
    queued_lease_seconds = _config_int(
        cfg,
        "VIDEO_RECORDING_QUEUED_LEASE_SECONDS",
        DEFAULT_VIDEO_QUEUED_LEASE_SECONDS,
        minimum=3600,
    )
    now = _utcnow()
    candidate_ids = [
        row[0]
        for row in db.session.query(VideoRecordingJob.id).join(
            TaskLog, TaskLog.id == VideoRecordingJob.task_log_id
        ).filter(
            TaskLog.status.in_(ACTIVE_SYNC_STATUSES),
            ~VideoRecordingJob.status.in_(("success", "failed", "cancelled")),
            ~VideoRecordingJob.stage.in_(("awaiting_download", "awaiting_extract")),
            or_(
                VideoRecordingJob.lease_expires_at.is_(None),
                VideoRecordingJob.lease_expires_at <= now,
            ),
        ).all()
    ]

    requeued = []
    exhausted = []
    parent_task_ids: set[int] = {
        row[0]
        for row in db.session.query(VideoRecordingJob.task_log_id)
        .join(TaskLog, TaskLog.id == VideoRecordingJob.task_log_id)
        .filter(TaskLog.status.in_(ACTIVE_SYNC_STATUSES))
        .distinct()
        .all()
    }
    for job_id in candidate_ids:
        job = VideoRecordingJob.query.filter_by(id=job_id).with_for_update(skip_locked=True).first()
        if job is None or job.status in {"success", "failed", "cancelled"}:
            db.session.rollback()
            continue
        parent_task_ids.add(job.task_log_id)
        if job.stage in {"awaiting_download", "awaiting_extract"}:
            db.session.rollback()
            continue

        # NULL published_at means the broker publish was never confirmed. The
        # outbox dispatcher republishes the same task id, so it is not a job
        # recovery and does not consume the recovery budget.
        if job.stage in {"queued_download", "queued_extract"} and job.published_at is None:
            db.session.rollback()
            continue

        lease_expires_at = _as_utc_datetime(job.lease_expires_at)
        if lease_expires_at is None:
            reference_at = _as_utc_datetime(job.last_progress_at or job.queued_at) or now
            if job.stage in {"queued_download", "queued_extract"}:
                fallback_lease_seconds = queued_lease_seconds
            elif job.stage in {"downloading", "download_retry_wait"}:
                fallback_lease_seconds = max(
                    stale_seconds,
                    RECORDING_DOWNLOAD_TASK_TIME_LIMIT + DEFAULT_VIDEO_DISPATCH_LEASE_SECONDS,
                )
            elif job.stage in {"extracting", "extract_retry_wait"}:
                fallback_lease_seconds = max(
                    stale_seconds,
                    RECORDING_EXTRACT_TASK_TIME_LIMIT + DEFAULT_VIDEO_DISPATCH_LEASE_SECONDS,
                )
            else:
                fallback_lease_seconds = stale_seconds
            lease_expires_at = reference_at + timedelta(seconds=fallback_lease_seconds)
            if lease_expires_at > now:
                job.lease_expires_at = lease_expires_at
                db.session.commit()
                continue

        if lease_expires_at > now:
            db.session.rollback()
            continue

        details = dict(job.details or {})
        recovery_count = max(
            0,
            _nonnegative_int(job.recovery_count),
            _nonnegative_int(details.get("recovery_count")),
        )

        current_task_kind = _recording_job_task_kind(job)
        if (
            os.path.exists(job.video_path)
            and job.stage not in {"queued_download", "downloading", "download_retry_wait"}
        ):
            task_kind = "extract"
        else:
            task_kind = "download"

        if recovery_count >= max_recoveries:
            job.status = "failed"
            job.stage = "recovery_exhausted"
            job.error_code = "recovery_limit_exceeded"
            job.error_message = f"录像任务自动恢复已达到上限（{max_recoveries} 次）"
            _set_recording_job_failed_task_kind(job, task_kind)
            job.finished_at = now
            job.last_progress_at = now
            job.lease_expires_at = None
            job.next_dispatch_at = None
            db.session.commit()
            exhausted.append(job.id)
            continue

        next_recovery_count = recovery_count + 1
        if current_task_kind != task_kind:
            _await_phase_changed_recording_job_recovery(
                job,
                task_kind,
                next_recovery_count,
            )
        else:
            _queue_recording_job(
                job,
                task_kind,
                recovery_count=next_recovery_count,
                cfg=cfg,
            )
        requeued.append(job.id)

    dispatch_result = _dispatch_available_video_recording_jobs()
    for task_id in parent_task_ids:
        _refresh_distributed_sync_task(task_id)
    if requeued:
        logger.warning("Requeued stale video recording jobs: %s", requeued)
    if exhausted:
        logger.error("Video recording jobs exhausted automatic recovery: %s", exhausted)
    return {
        "checked": len(candidate_ids),
        "requeued": requeued,
        "exhausted": exhausted,
        "queued": dispatch_result["queued"],
        "published": dispatch_result["published"],
        "publish_failed": dispatch_result["publish_failed"],
        "parent_published": parent_dispatch_result["published"],
        "parent_deferred": parent_dispatch_result["deferred"],
    }


def _reset_failed_video_recording_jobs(
    task: TaskLog,
    jobs: list[VideoRecordingJob],
) -> None:
    task.status = "running"
    task.finished_at = None
    task.error_message = None
    task.error_count = 0
    for job in jobs:
        failed_task_kind = _recording_job_failed_task_kind(job)
        retry_extract = bool(
            os.path.exists(job.video_path)
            and (
                failed_task_kind == "extract"
                or (failed_task_kind is None and job.extract_attempt_count > 0)
            )
        )
        job.error_code = None
        job.error_message = None
        job.finished_at = None
        job.dispatch_attempt_count = 0
        job.recovery_count = 0
        job.published_at = None
        job.next_dispatch_at = None
        job.lease_expires_at = None
        job.progress_percent = 0.0
        details = dict(job.details or {})
        details.pop("recovery_count", None)
        details.pop("last_recovered_at", None)
        details.pop("failed_task_kind", None)
        job.details = details
        if retry_extract:
            job.status = "queued_for_extract"
            job.stage = "awaiting_extract"
            job.extract_task_id = None
        else:
            job.status = "pending"
            job.stage = "awaiting_download"
            job.download_task_id = None
            job.extract_task_id = None
            job.download_started_at = None
            job.download_finished_at = None
            job.extract_started_at = None
            job.extract_finished_at = None


def retry_video_source_sync_task(task_log_id: int, cfg: dict) -> dict:
    """Serialize a manual retry with new reservations for the same date."""
    _reconcile_active_sync_tasks()
    _acquire_video_sync_enqueue_lock()
    task = TaskLog.query.filter_by(id=task_log_id).with_for_update().first()
    if task is None:
        db.session.commit()
        return {"task": None, "conflict": None, "retried_count": 0}
    if task.status not in {"failed", "partial"}:
        # A concurrent retry already transitioned this same row. Treat it as
        # the idempotent winner instead of resetting it a second time.
        db.session.commit()
        return {"task": task, "conflict": task, "retried_count": 0}

    task_date = task.task_date
    conflict_date = task_date if _distributed_video_pipeline_enabled(cfg) else None
    conflict = _active_sync_task_query(
        conflict_date,
        exclude_task_id=task.id,
    ).order_by(TaskLog.id.desc()).first()
    if conflict is not None:
        db.session.commit()
        return {"task": task, "conflict": conflict, "retried_count": 0}

    jobs = VideoRecordingJob.query.filter(
        VideoRecordingJob.task_log_id == task_log_id,
        VideoRecordingJob.status.in_(("failed", "cancelled")),
    ).order_by(VideoRecordingJob.id).all()
    if jobs:
        _reset_failed_video_recording_jobs(task, jobs)
    else:
        # Legacy/inline tasks have no durable children. Reuse the failed parent
        # as a fresh outbox reservation while the advisory lock is held.
        task.status = "pending"
        task.finished_at = None
        task.error_message = None
        task.error_count = 0
        task.total_count = 0
        task.success_count = 0
        retry_meta = dict(task.meta or {})
        retry_meta.update({
            "recordings": [],
            "empty_windows": [],
            "image_ids": [],
            "primary_count": 0,
            "candidate_count": 0,
        })
        task.meta = retry_meta
        _prepare_video_sync_dispatch(task)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        conflict = _active_sync_task_query(task_date).order_by(TaskLog.id.desc()).first()
        current = TaskLog.query.get(task_log_id)
        return {"task": current, "conflict": conflict, "retried_count": 0}

    if jobs:
        _dispatch_available_video_recording_jobs()
        _refresh_distributed_sync_task(task_log_id)
    else:
        _publish_prepared_video_sync_task(task_log_id, cfg)
    current = TaskLog.query.get(task_log_id)
    return {"task": current, "conflict": None, "retried_count": len(jobs)}


def retry_failed_video_recording_jobs(task_log_id: int) -> int:
    """Backward-compatible wrapper for callers that only need the job count."""
    from flask import current_app

    cfg = get_effective_config(current_app.config)
    result = retry_video_source_sync_task(task_log_id, cfg)
    if result["conflict"] is not None:
        return -1
    return int(result["retried_count"])


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
    if (
        os.path.splitext(video_path)[1].lower() == ".ps"
        and str(cfg.get("VIDEO_EXTRACT_DECODE_BACKEND", "opencv")).strip().lower() == "opencv"
    ):
        # Hikvision exports MPEG Program Stream files. Prefer FFmpeg for this
        # container because OpenCV may open it without yielding valid frames.
        cfg = {**cfg, "VIDEO_EXTRACT_DECODE_BACKEND": "ffmpeg_cpu"}
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
            "extract_strategy": progress.get("extract_strategy"),
            "decode_backend": progress.get("decode_backend"),
            "decode_fallback_reason": progress.get("decode_fallback_reason"),
            "analysis_width": progress.get("analysis_width"),
            "analysis_height": progress.get("analysis_height"),
            "elapsed_seconds": progress.get("elapsed_seconds"),
            "processing_fps": progress.get("processing_fps"),
            "realtime_factor": progress.get("realtime_factor"),
            "stage_timings": progress.get("stage_timings"),
        })
        _persist_task_meta(task_log, progress_meta)
        db.session.commit()

    try:
        frames = _extract_frames_for_recording(
            cfg,
            video_path,
            output_dir,
            video_start_time,
            channel_id,
            persist_progress,
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

        if primary_image_ids:
            from app.tasks.recognition import enqueue_recognition_images

            recognition_task = enqueue_recognition_images(
                primary_image_ids,
                target_date=capture_date,
            )
            if recognition_task:
                task_meta = dict(task_log.meta or {})
                task_meta["recognition_task_log_id"] = recognition_task.id
                task_meta["recognition_queued_count"] = recognition_task.total_count
                _persist_task_meta(task_log, task_meta)
                db.session.commit()

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
    _dispatch_pending_video_sync_tasks(cfg)
    try:
        target_date = _get_scheduled_sync_target_date(cfg)
    except VideoSourceConfigError as e:
        logger.info("Skip scheduled video source sync: %s", e)
        return {"scheduled": False, "reason": str(e)}

    if target_date is None:
        return {"scheduled": False}

    menu = DailyMenu.query.filter_by(menu_date=target_date).first()
    if _requires_configured_menu_for_recognition(cfg) and not is_menu_configured(menu, cfg):
        task_log = _record_menu_not_configured_sync_alert(target_date)
        return {
            "scheduled": False,
            "reason": MENU_NOT_CONFIGURED_ALERT_TYPE,
            "task_id": task_log.id,
            "date": target_date.isoformat(),
        }

    task_log, created = enqueue_video_source_sync(target_date, cfg)
    if not created:
        logger.info(
            "Skip duplicate scheduled video source sync for %s: task %s is already %s",
            target_date,
            task_log.id,
            task_log.status,
        )
        return {
            "scheduled": False,
            "reason": "already_scheduled",
            "active_task_id": task_log.id,
            "date": target_date.isoformat(),
        }

    logger.info(
        "Scheduled video source sync dispatched for %s as task %s",
        target_date.isoformat(),
        task_log.id,
    )
    return {"scheduled": True, "date": target_date.isoformat(), "task_id": task_log.id}


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


_RECORDING_FILENAME_TIME_PATTERN = re.compile(
    r"(?:^|_)(?P<date>\d{4}-\d{2}-\d{2})_(?P<time>\d{2}-\d{2}-\d{2})(?:_|\.|$)"
)


def _parse_recording_datetime(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _parse_recording_start_from_filename(filename: str, cfg):
    basename = os.path.basename(str(filename or "").strip())
    match = _RECORDING_FILENAME_TIME_PATTERN.search(basename)
    if match is None:
        return None
    try:
        local_start = datetime.strptime(
            f"{match.group('date')}_{match.group('time')}",
            "%Y-%m-%d_%H-%M-%S",
        )
    except ValueError:
        return None
    return local_start.replace(tzinfo=_resolve_video_timezone(cfg))


def _resolve_recording_source_start(recording: dict, cfg):
    reported_value = recording.get("source_start_time")
    reported_start = _parse_recording_datetime(reported_value)
    if reported_start is not None:
        return reported_start, "isapi_source_start"

    if reported_value not in (None, ""):
        logger.warning(
            "Invalid source_start_time=%r; trying recording filename=%r",
            reported_value,
            recording.get("filename"),
        )
    filename_start = _parse_recording_start_from_filename(recording.get("filename"), cfg)
    if filename_start is not None:
        return filename_start, "recording_filename"

    raise ValueError(
        "录像缺少有效的 source_start_time，且无法从文件名解析录像起始时间: "
        f"{recording.get('filename') or '<missing>'}"
    )


def _resolve_recording_job_source_start(job: VideoRecordingJob, cfg):
    details = dict(job.details or {})

    # New jobs retain the exact ISAPI value separately from the resolved
    # source_start.  Legacy jobs stored that raw value under source_start.
    reported_key = None
    if "source_start_reported" in details:
        reported_key = "source_start_reported"
    elif "source_start" in details:
        reported_key = "source_start"

    if reported_key is not None:
        reported_start = _parse_recording_datetime(details.get(reported_key))
        if reported_start is not None:
            return reported_start, "isapi_source_start"
        filename_start = _parse_recording_start_from_filename(job.filename, cfg)
        if filename_start is not None:
            return filename_start, "recording_filename"
        raise ValueError(
            "录像任务缺少有效的 ISAPI 起始时间，且无法从文件名解析录像起始时间: "
            f"{job.filename or '<missing>'}"
        )

    if details.get("time_basis") in {"recording_filename", "recording_start"}:
        filename_start = _parse_recording_start_from_filename(job.filename, cfg)
        if filename_start is not None:
            return filename_start, "recording_filename"
        raise ValueError(
            "录像任务不能使用查询区间作为时间原点，且无法从文件名解析录像起始时间: "
            f"{job.filename or '<missing>'}"
        )

    if job.source_start is not None:
        return job.source_start, "isapi_source_start"

    filename_start = _parse_recording_start_from_filename(job.filename, cfg)
    if filename_start is not None:
        return filename_start, "recording_filename"
    raise ValueError(
        "录像任务缺少 source_start，且无法从文件名解析录像起始时间: "
        f"{job.filename or '<missing>'}"
    )


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


def _cleanup_video_recordings_preserving_active_dates(
    storage_path: str,
    retention_days: int,
    cfg: dict,
    target_date: date,
) -> dict:
    """Clean storage without racing active sync reservations or retries."""
    # The same transaction-scoped lock is used by reservation and manual retry.
    # Holding it through the filesystem cleanup prevents a new backfill from
    # appearing after the protected-date snapshot was taken.
    _acquire_video_sync_enqueue_lock()
    active_sync_dates = {
        row[0]
        for row in db.session.query(TaskLog.task_date).filter(
            TaskLog.task_type.in_(LEGACY_SYNC_TASK_TYPES),
            TaskLog.status.in_(ACTIVE_SYNC_STATUSES),
            TaskLog.task_date.is_not(None),
        ).all()
    }
    try:
        return _cleanup_expired_video_recordings(
            storage_path,
            retention_days,
            cfg,
            keep_dates=active_sync_dates | {target_date},
        )
    finally:
        # Release pg_advisory_xact_lock even if an unexpected cleanup error
        # escapes the per-file error handling.
        db.session.commit()


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
    slots = get_meal_slots(cfg)
    normalized = []
    for slot in slots:
        start = str(slot.get("start") or "").strip()
        end = str(slot.get("end") or "").strip()
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


def _resolve_analysis_max_pending(cfg, concurrency: int) -> int:
    raw = cfg.get("VIDEO_ANALYSIS_MAX_PENDING", 0)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    return max(concurrency, value if value > 0 else concurrency * 2)


def _distributed_video_pipeline_enabled(cfg) -> bool:
    value = cfg.get("VIDEO_DISTRIBUTED_PIPELINE", False)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _round_robin_recording_jobs(jobs: list[dict]) -> list[dict]:
    """Interleave channels so one channel cannot monopolize the extract pool."""
    channel_order: list[str] = []
    buckets: dict[str, list[dict]] = {}
    for job in jobs:
        channel_id = str(job.get("channel_id") or "")
        if channel_id not in buckets:
            channel_order.append(channel_id)
            buckets[channel_id] = []
        buckets[channel_id].append(job)

    scheduled: list[dict] = []
    offsets = {channel_id: 0 for channel_id in channel_order}
    while len(scheduled) < len(jobs):
        for channel_id in channel_order:
            offset = offsets[channel_id]
            bucket = buckets[channel_id]
            if offset >= len(bucket):
                continue
            scheduled.append(bucket[offset])
            offsets[channel_id] = offset + 1
    return scheduled


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
        TaskLog.status.in_(("pending", "running", "success", "partial")),
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
    next_meta = {
        **dict(task_log.meta or {}),
        "status_text": reason,
    }
    _mark_incomplete_recordings_failed(next_meta, reason, resolved_now)
    recording_jobs = VideoRecordingJob.query.filter(
        VideoRecordingJob.task_log_id == task_log.id,
        ~VideoRecordingJob.status.in_(("success", "failed", "cancelled")),
    ).all()
    for job in recording_jobs:
        _set_recording_job_failed_task_kind(job, _recording_job_task_kind(job))
        job.status = "cancelled"
        job.stage = "cancelled"
        job.error_code = "parent_task_terminated"
        job.error_message = reason
        job.last_progress_at = resolved_now
        job.finished_at = resolved_now
        job.lease_expires_at = None
        job.next_dispatch_at = None
    all_recording_jobs = VideoRecordingJob.query.filter_by(task_log_id=task_log.id).order_by(
        VideoRecordingJob.id
    ).all()
    if all_recording_jobs:
        failed_count = len([
            job for job in all_recording_jobs if job.status in {"failed", "cancelled"}
        ])
        success_count = len([job for job in all_recording_jobs if job.status == "success"])
        next_meta.update({
            "recordings": [job.to_recording_meta() for job in all_recording_jobs],
            "recording_count": len(all_recording_jobs),
            "completed_recording_count": success_count,
            "failed_recording_count": failed_count,
            "active_recording_count": len(all_recording_jobs) - success_count - failed_count,
        })
    _persist_task_meta(task_log, next_meta)
    return task_log


def _mark_stalled_extract_recordings(
    task_meta: dict,
    jobs: list[dict],
    now: datetime,
    stall_seconds: int,
) -> list[str]:
    if stall_seconds <= 0:
        return []

    stalled_filenames: list[str] = []
    for job in jobs:
        recording = job.get("recording_meta")
        if not isinstance(recording, dict):
            continue
        if recording.get("download_status") != "extracting":
            continue

        reference_at = (
            _as_utc_datetime(recording.get("last_progress_at"))
            or _as_utc_datetime(recording.get("extract_started_at"))
        )
        if reference_at is None:
            continue

        elapsed_seconds = int((now - reference_at).total_seconds())
        if elapsed_seconds < stall_seconds:
            continue

        recording["download_status"] = "extract_stalled"
        recording["stalled_at"] = now.isoformat()
        recording["stall_seconds"] = elapsed_seconds
        stalled_filenames.append(str(job.get("video_filename") or recording.get("filename") or ""))

    if stalled_filenames:
        task_meta["recordings"] = list(task_meta.get("recordings") or [])
    return stalled_filenames


def _mark_incomplete_recordings_failed(task_meta: dict, reason: str, now: datetime) -> None:
    finished_at = now.isoformat()
    recordings = task_meta.get("recordings")
    if not isinstance(recordings, list):
        return
    for recording in recordings:
        if not isinstance(recording, dict):
            continue
        status = recording.get("download_status")
        if status not in ("pending", "downloaded", "queued_for_extract", "extracting", "extract_stalled"):
            continue
        recording["download_status"] = "failed" if status == "pending" else "frame_extract_failed"
        recording["error"] = reason
        recording["extract_finished_at"] = finished_at


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
    stale_ids: list[int] = []
    for task in candidate_tasks:
        has_recording_jobs = VideoRecordingJob.query.filter_by(task_log_id=task.id).first() is not None
        if has_recording_jobs:
            # Distributed parents are derived state. Per-recording leases own
            # recovery, and aggregation repairs the parent after restarts.
            _refresh_distributed_sync_task(task.id)
            continue
        reference_at = _sync_task_stale_reference_at(task)
        if reference_at is None or reference_at >= cutoff:
            continue
        stale_ids.append(task.id)
        mark_sync_task_failed(task, "同步任务长时间未完成，系统已自动标记为失败", now=resolved_now)

    if not stale_ids:
        return []

    db.session.commit()
    logger.warning("Marked stale video sync tasks as failed: %s", stale_ids)
    return stale_ids


def _reconcile_active_sync_tasks() -> None:
    """Repair parent state and recover due child work before enforcing a lock."""
    _dispatch_pending_video_sync_tasks()
    _mark_stale_active_sync_tasks()
    now = _utcnow()
    recoverable_job = (
        VideoRecordingJob.query.join(TaskLog, TaskLog.id == VideoRecordingJob.task_log_id)
        .filter(
            TaskLog.status.in_(ACTIVE_SYNC_STATUSES),
            ~VideoRecordingJob.status.in_(("success", "failed", "cancelled")),
            or_(
                VideoRecordingJob.stage.in_(("awaiting_download", "awaiting_extract")),
                VideoRecordingJob.lease_expires_at.is_(None),
                VideoRecordingJob.lease_expires_at <= now,
                and_(
                    VideoRecordingJob.published_at.is_(None),
                    or_(
                        VideoRecordingJob.next_dispatch_at.is_(None),
                        VideoRecordingJob.next_dispatch_at <= now,
                    ),
                ),
            ),
        )
        .first()
    )
    if recoverable_job is not None:
        # Run the same durable reconciler inline. This makes the HTTP trigger
        # self-healing even when Celery Beat or the maintenance queue was down.
        recover_stale_video_recording_jobs.run()
        _mark_stale_active_sync_tasks()


def _active_sync_task_query(target_date: date | None = None, exclude_task_id: int | None = None):
    query = TaskLog.query.filter(
        TaskLog.task_type.in_(LEGACY_SYNC_TASK_TYPES),
        TaskLog.status.in_(ACTIVE_SYNC_STATUSES),
    )
    if target_date is not None:
        query = query.filter(TaskLog.task_date == target_date)
    if exclude_task_id is not None:
        query = query.filter(TaskLog.id != exclude_task_id)
    return query


def _find_active_sync_task(
    target_date: date | None = None,
    *,
    exclude_task_id: int | None = None,
    reconcile: bool = True,
) -> TaskLog | None:
    if reconcile:
        _reconcile_active_sync_tasks()
    return _active_sync_task_query(target_date, exclude_task_id).order_by(TaskLog.id.desc()).first()


def _acquire_video_sync_enqueue_lock() -> None:
    if db.engine.dialect.name == "postgresql":
        db.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": VIDEO_SYNC_ENQUEUE_ADVISORY_LOCK_ID},
        )


def _prepare_video_sync_dispatch(task_log: TaskLog, *, now: datetime | None = None) -> str:
    """Persist a parent-task outbox entry before publishing it to Celery."""
    resolved_now = now or _utcnow()
    task_id = str(uuid.uuid4())
    meta = dict(task_log.meta or {})
    meta.update({
        "sync_dispatch_task_id": task_id,
        "sync_dispatch_attempt_count": 0,
        "sync_published_at": None,
        "sync_next_dispatch_at": resolved_now.isoformat(),
        "sync_dispatch_error": None,
        "status_text": "任务已提交，等待视频同步 Worker",
    })
    task_log.meta = meta
    return task_id


def _publish_prepared_video_sync_task(
    task_log_id: int,
    cfg: dict,
) -> bool:
    """Publish one durable parent outbox entry; duplicate delivery is harmless."""
    _acquire_video_sync_enqueue_lock()
    task_log = TaskLog.query.get(task_log_id)
    if task_log is None or task_log.status != "pending" or task_log.task_date is None:
        db.session.commit()
        return False

    now = _utcnow()
    meta = dict(task_log.meta or {})
    task_id = str(meta.get("sync_dispatch_task_id") or "").strip()
    if not task_id:
        task_id = _prepare_video_sync_dispatch(task_log, now=now)
        meta = dict(task_log.meta or {})
    if meta.get("sync_published_at"):
        db.session.commit()
        return True

    next_dispatch_at = _as_utc_datetime(meta.get("sync_next_dispatch_at"))
    if next_dispatch_at is not None and next_dispatch_at > now:
        db.session.commit()
        return False

    attempt = _nonnegative_int(meta.get("sync_dispatch_attempt_count")) + 1
    lease_seconds = _config_int(
        cfg,
        "VIDEO_RECORDING_DISPATCH_LEASE_SECONDS",
        DEFAULT_VIDEO_DISPATCH_LEASE_SECONDS,
        minimum=60,
    )
    meta.update({
        "sync_dispatch_attempt_count": attempt,
        "sync_next_dispatch_at": (now + timedelta(seconds=lease_seconds)).isoformat(),
        "sync_dispatch_error": None,
    })
    task_log.meta = meta
    db.session.commit()

    try:
        sync_video_source_media.apply_async(
            args=[task_log.task_date.isoformat(), task_log.id],
            task_id=task_id,
            queue="video",
        )
    except Exception as exc:
        db.session.rollback()
        current = TaskLog.query.get(task_log_id)
        if current is None or current.status != "pending":
            return False
        current_meta = dict(current.meta or {})
        if str(current_meta.get("sync_dispatch_task_id") or "") != task_id:
            return False
        max_attempts = _config_int(
            cfg,
            "VIDEO_RECORDING_DISPATCH_MAX_ATTEMPTS",
            DEFAULT_VIDEO_DISPATCH_MAX_ATTEMPTS,
        )
        error_text = _format_task_error(exc)
        if attempt >= max_attempts:
            mark_sync_task_failed(current, f"视频同步任务提交失败：{error_text}")
            terminal_meta = dict(current.meta or {})
            terminal_meta.update({
                "sync_dispatch_attempt_count": attempt,
                "sync_dispatch_error": error_text,
                "sync_next_dispatch_at": None,
            })
            current.meta = terminal_meta
        else:
            backoff_seconds = min(300, 15 * (2 ** min(attempt - 1, 5)))
            current_meta.update({
                "sync_dispatch_attempt_count": attempt,
                "sync_dispatch_error": error_text,
                "sync_next_dispatch_at": (
                    _utcnow() + timedelta(seconds=backoff_seconds)
                ).isoformat(),
                "status_text": "视频同步任务提交暂时失败，等待自动补发",
            })
            current.meta = current_meta
        db.session.commit()
        return False

    published_at = _utcnow()
    db.session.expire_all()
    current = TaskLog.query.get(task_log_id)
    if current is not None and current.status == "pending":
        current_meta = dict(current.meta or {})
        if str(current_meta.get("sync_dispatch_task_id") or "") == task_id:
            current_meta.update({
                "sync_published_at": published_at.isoformat(),
                "sync_next_dispatch_at": None,
                "sync_dispatch_error": None,
                "status_text": "任务已提交，等待视频同步 Worker",
            })
            current.meta = current_meta
            db.session.commit()
    return True


def _dispatch_pending_video_sync_tasks(cfg: dict | None = None) -> dict:
    """Recover parent reservations that were committed before broker publish."""
    if cfg is None:
        from flask import current_app

        cfg = get_effective_config(current_app.config)
    now = _utcnow()
    pending_tasks = TaskLog.query.filter(
        TaskLog.task_type.in_(LEGACY_SYNC_TASK_TYPES),
        TaskLog.status == "pending",
    ).order_by(TaskLog.id.asc()).limit(100).all()
    published: list[int] = []
    deferred: list[int] = []
    for task_log in pending_tasks:
        meta = dict(task_log.meta or {})
        if meta.get("sync_published_at"):
            continue
        next_dispatch_at = _as_utc_datetime(meta.get("sync_next_dispatch_at"))
        if next_dispatch_at is not None and next_dispatch_at > now:
            deferred.append(task_log.id)
            continue
        if _publish_prepared_video_sync_task(task_log.id, cfg):
            published.append(task_log.id)
        else:
            deferred.append(task_log.id)
    return {"checked": len(pending_tasks), "published": published, "deferred": deferred}


def _reserve_video_source_sync_task(target_date: date, cfg: dict) -> tuple[TaskLog, bool]:
    """Atomically reserve one pending sync row before publishing to Celery."""
    _reconcile_active_sync_tasks()
    _acquire_video_sync_enqueue_lock()

    allow_parallel_dates = _distributed_video_pipeline_enabled(cfg)
    conflict_date = target_date if allow_parallel_dates else None
    existing = _active_sync_task_query(conflict_date).order_by(TaskLog.id.desc()).first()
    if existing is not None:
        # Release the transaction-scoped PostgreSQL advisory lock.
        db.session.commit()
        return existing, False

    task_log = TaskLog(
        task_type="video_source_sync",
        task_date=target_date,
        status="pending",
        meta={
            "status_text": "任务已提交，等待视频同步 Worker",
            "recordings": [],
            "empty_windows": [],
            "image_ids": [],
            "primary_count": 0,
            "candidate_count": 0,
            "last_progress_at": _utcnow().isoformat(),
        },
    )
    _prepare_video_sync_dispatch(task_log)
    db.session.add(task_log)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        # The partial unique index is the final guard if another API process
        # won the same-date race before this transaction committed.
        existing = _active_sync_task_query(target_date).order_by(TaskLog.id.desc()).first()
        if existing is None:
            raise
        return existing, False
    return task_log, True


def enqueue_video_source_sync(target_date: date, cfg: dict | None = None) -> tuple[TaskLog, bool]:
    """Reserve first, then publish; duplicate submissions return the same row."""
    if cfg is None:
        from flask import current_app

        cfg = get_effective_config(current_app.config)
    task_log, created = _reserve_video_source_sync_task(target_date, cfg)
    if not created:
        return task_log, False
    _publish_prepared_video_sync_task(task_log.id, cfg)
    return task_log, True


def has_active_sync_task(
    target_date: date | None = None,
    *,
    exclude_task_id: int | None = None,
) -> bool:
    return _find_active_sync_task(target_date, exclude_task_id=exclude_task_id) is not None


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


def _resolve_recording_window(
    cfg,
    source_start,
    window_start,
    window_end,
) -> tuple[datetime, float, float | None]:
    """Resolve a requested window as offsets from the ISAPI recording start."""
    source_at = _coerce_extract_start_datetime(source_start)
    if not window_start or not window_end:
        return source_at, 0.0, None

    tz = _resolve_video_timezone(cfg)

    def localize(value) -> datetime:
        parsed = _coerce_extract_start_datetime(value)
        return parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed.astimezone(tz)

    source_local = localize(source_at)
    window_start_local = localize(window_start)
    window_end_local = localize(window_end)
    analysis_start = max(source_local, window_start_local)
    seek_offset = (analysis_start - source_local).total_seconds()
    duration_seconds = (window_end_local - analysis_start).total_seconds()
    if duration_seconds <= 0:
        raise ValueError("录像源与分析窗口没有可抽帧的时间交集")
    return source_local, seek_offset, duration_seconds


def _extract_frames_for_recording(
    cfg,
    video_save_path: str,
    output_dir: str,
    video_start,
    channel_id: str,
    progress_callback=None,
    *,
    cancel_event=None,
    primary_deadline_monotonic: float | None = None,
    window_start=None,
    window_end=None,
):
    source_at, window_offset_seconds, window_duration_seconds = _resolve_recording_window(
        cfg,
        video_start,
        window_start,
        window_end,
    )
    return _extract_frames_from_input(
        cfg,
        video_save_path,
        output_dir,
        source_at,
        channel_id,
        progress_callback,
        cancel_event=cancel_event,
        primary_deadline_monotonic=primary_deadline_monotonic,
        window_offset_seconds=window_offset_seconds,
        window_duration_seconds=window_duration_seconds,
    )


def _extract_frames_from_input(
    cfg,
    video_save_path: str,
    output_dir: str,
    video_start,
    channel_id: str,
    progress_callback=None,
    *,
    cancel_event=None,
    primary_deadline_monotonic: float | None = None,
    window_offset_seconds: float = 0.0,
    window_duration_seconds: float | None = None,
):
    errors: list[str] = []
    fallback_inputs = [video_save_path]
    attempt = 1
    primary_strategy = _configured_extract_strategy(cfg)

    if _cancel_requested(cancel_event):
        raise InterruptedError("录像抽帧已取消")
    if primary_deadline_monotonic is not None and time.monotonic() >= primary_deadline_monotonic:
        errors.append("接近视频同步软超时，跳过主分析")
        return _extract_fallback_after_failure(
            cfg,
            fallback_inputs,
            output_dir,
            video_start,
            channel_id,
            errors,
            attempt,
            progress_callback,
            cancel_event,
            window_offset_seconds,
            window_duration_seconds,
        )

    try:
        return _run_extract_attempt(
            cfg,
            video_save_path,
            output_dir,
            video_start,
            channel_id,
            strategy=primary_strategy,
            attempt=attempt,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
            window_offset_seconds=window_offset_seconds,
            window_duration_seconds=window_duration_seconds,
        )
    except InterruptedError:
        raise
    except TimeoutError as exc:
        errors.append(f"{primary_strategy}: {_compact_error(exc)}")
        # A timeout means the expensive full decode is not viable. Remuxing and
        # transcoding before repeating the same analysis multiplies wall time.
        return _extract_fallback_after_failure(
            cfg,
            fallback_inputs,
            output_dir,
            video_start,
            channel_id,
            errors,
            attempt,
            progress_callback,
            cancel_event,
            window_offset_seconds,
            window_duration_seconds,
        )
    except Exception as exc:
        errors.append(f"{primary_strategy}: {_compact_error(exc)}")

    for repair_strategy in ("remux", "transcode"):
        _report_extract_recovery(
            progress_callback,
            strategy=repair_strategy,
            attempt=attempt + 1,
            status="repairing",
            error=errors[-1] if errors else None,
        )
        try:
            repaired_path = _repair_video_for_extract(
                cfg,
                video_save_path,
                repair_strategy,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
            )
        except InterruptedError:
            raise
        except Exception as exc:
            errors.append(f"{repair_strategy}: {_compact_error(exc)}")
            continue
        fallback_inputs.append(repaired_path)

        attempt += 1
        try:
            return _run_extract_attempt(
                cfg,
                repaired_path,
                output_dir,
                video_start,
                channel_id,
                strategy=f"{repair_strategy}_{primary_strategy}",
                attempt=attempt,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
                window_offset_seconds=window_offset_seconds,
                window_duration_seconds=window_duration_seconds,
            )
        except InterruptedError:
            raise
        except TimeoutError as exc:
            errors.append(f"{repair_strategy}_{primary_strategy}: {_compact_error(exc)}")
            break
        except Exception as exc:
            errors.append(f"{repair_strategy}_{primary_strategy}: {_compact_error(exc)}")

    return _extract_fallback_after_failure(
        cfg,
        fallback_inputs,
        output_dir,
        video_start,
        channel_id,
        errors,
        attempt,
        progress_callback,
        cancel_event,
        window_offset_seconds,
        window_duration_seconds,
    )


def _extract_fallback_after_failure(
    cfg,
    fallback_inputs: list[str],
    output_dir: str,
    video_start,
    channel_id: str,
    errors: list[str],
    attempt: int,
    progress_callback=None,
    cancel_event=None,
    window_offset_seconds: float = 0.0,
    window_duration_seconds: float | None = None,
) -> list[dict]:
    _report_extract_recovery(
        progress_callback,
        strategy="ffmpeg_interval_fallback",
        attempt=attempt + 1,
        status="fallback",
        error=errors[-1] if errors else None,
    )
    seen_fallback_inputs = set()
    for fallback_input in reversed(fallback_inputs):
        if fallback_input in seen_fallback_inputs:
            continue
        seen_fallback_inputs.add(fallback_input)
        try:
            return _extract_frames_with_ffmpeg_fallback(
                cfg,
                fallback_input,
                output_dir,
                video_start,
                channel_id,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
                window_offset_seconds=window_offset_seconds,
                window_duration_seconds=window_duration_seconds,
            )
        except InterruptedError:
            raise
        except Exception as exc:
            errors.append(f"ffmpeg_interval_fallback({os.path.basename(fallback_input)}): {_compact_error(exc)}")

    raise RuntimeError("所有抽帧策略均失败：" + " | ".join(errors[-6:]))


def _extract_uses_subprocess(cfg: dict) -> bool:
    value = cfg.get("VIDEO_EXTRACT_USE_SUBPROCESS", True)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _configured_extract_strategy(cfg: dict) -> str:
    backend = str(cfg.get("VIDEO_EXTRACT_DECODE_BACKEND", "opencv") or "opencv").strip().lower()
    return {
        "auto": "auto",
        "nvdec": "ffmpeg_nvdec",
        "ffmpeg_cpu": "ffmpeg_cpu",
        "opencv": "opencv",
    }.get(backend, "opencv")


def _is_recovery_strategy(strategy: str) -> bool:
    normalized = str(strategy or "").lower()
    return any(marker in normalized for marker in ("fallback", "remux", "transcode"))


def _extract_progress_stall_seconds(cfg: dict) -> int:
    try:
        return int(cfg.get("VIDEO_EXTRACT_PROGRESS_STALL_SECONDS", EXTRACT_PROGRESS_STALL_SECONDS))
    except (TypeError, ValueError):
        return EXTRACT_PROGRESS_STALL_SECONDS


def _extract_max_runtime_seconds(cfg: dict) -> int:
    try:
        return max(60, int(cfg.get("VIDEO_EXTRACT_MAX_RUNTIME_SECONDS", EXTRACT_MAX_RUNTIME_SECONDS)))
    except (TypeError, ValueError):
        return EXTRACT_MAX_RUNTIME_SECONDS


def _extract_degrade_before_sync_timeout_seconds(cfg: dict) -> int:
    try:
        value = int(cfg.get("VIDEO_EXTRACT_DEGRADE_BEFORE_SYNC_TIMEOUT_SECONDS", 1800))
    except (TypeError, ValueError):
        value = 1800
    return max(60, min(value, max(60, VIDEO_SYNC_TASK_SOFT_TIME_LIMIT - 60)))


def _extract_cpu_threads_per_job(cfg: dict) -> int:
    try:
        return max(1, int(cfg.get("VIDEO_EXTRACT_CPU_THREADS_PER_JOB", 2)))
    except (TypeError, ValueError):
        return 2


def _cancel_requested(cancel_event) -> bool:
    return bool(cancel_event is not None and cancel_event.is_set())


def _extract_ffmpeg_timeout_seconds(cfg: dict) -> int:
    try:
        return max(30, int(cfg.get("VIDEO_EXTRACT_FFMPEG_TIMEOUT_SECONDS", EXTRACT_FFMPEG_TIMEOUT_SECONDS)))
    except (TypeError, ValueError):
        return EXTRACT_FFMPEG_TIMEOUT_SECONDS


def _extract_fallback_interval_seconds(cfg: dict) -> int:
    try:
        return max(1, int(cfg.get("VIDEO_EXTRACT_FALLBACK_INTERVAL_SECONDS", EXTRACT_FALLBACK_INTERVAL_SECONDS)))
    except (TypeError, ValueError):
        return EXTRACT_FALLBACK_INTERVAL_SECONDS


def _extract_fallback_max_frames(cfg: dict) -> int:
    try:
        return max(0, int(cfg.get("VIDEO_EXTRACT_FALLBACK_MAX_FRAMES", EXTRACT_FALLBACK_MAX_FRAMES)))
    except (TypeError, ValueError):
        return EXTRACT_FALLBACK_MAX_FRAMES


def _run_extract_attempt(
    cfg,
    video_save_path: str,
    output_dir: str,
    video_start,
    channel_id: str,
    *,
    strategy: str,
    attempt: int,
    progress_callback=None,
    cancel_event=None,
    window_offset_seconds: float = 0.0,
    window_duration_seconds: float | None = None,
) -> list[dict]:
    if _cancel_requested(cancel_event):
        raise InterruptedError("录像抽帧已取消")
    _report_extract_recovery(
        progress_callback,
        strategy=strategy,
        attempt=attempt,
        status="retrying" if attempt > 1 else "running",
    )

    def attempt_progress(progress: dict) -> None:
        if progress_callback is None:
            return
        next_progress = dict(progress or {})
        next_progress.setdefault("extract_strategy", strategy)
        next_progress.setdefault("extract_attempt", attempt)
        progress_callback(next_progress)

    if _extract_uses_subprocess(cfg):
        frames = _extract_frames_for_recording_subprocess(
            cfg,
            video_save_path,
            output_dir,
            video_start,
            channel_id,
            progress_callback=attempt_progress,
            cancel_event=cancel_event,
            window_offset_seconds=window_offset_seconds,
            window_duration_seconds=window_duration_seconds,
        )
    else:
        from app.services.video_analyzer import VideoAnalyzer

        analyzer = VideoAnalyzer(cfg)
        frames = analyzer.extract_frames(
            video_save_path,
            output_dir,
            video_start,
            channel_id,
            progress_callback=attempt_progress,
            start_offset_seconds=window_offset_seconds,
            duration_seconds=window_duration_seconds,
        )

    for frame in frames:
        # The outer strategy records source repair provenance (for example
        # remux_auto); decoder_strategy/decode_backend record how frames were
        # actually decoded inside the analyzer. Keep both dimensions.
        frame["extraction_strategy"] = strategy

    _report_extract_recovery(
        progress_callback,
        strategy=strategy,
        attempt=attempt,
        status="complete",
    )
    return frames


def _report_extract_recovery(
    progress_callback,
    *,
    strategy: str,
    attempt: int,
    status: str,
    error: str | None = None,
) -> None:
    if progress_callback is None:
        return
    progress = {
        "extract_strategy": strategy,
        "extract_attempt": attempt,
        "recovery_status": status,
    }
    if error:
        progress["recovery_error"] = _truncate_text(error, 500)
    progress_callback(progress)


def _repair_video_for_extract(
    cfg,
    video_path: str,
    strategy: str,
    progress_callback=None,
    cancel_event=None,
) -> str:
    if not os.path.exists(video_path):
        raise FileNotFoundError(video_path)

    ffmpeg_bin = str(cfg.get("FFMPEG_BIN") or "ffmpeg")
    base_path, _ = os.path.splitext(video_path)
    output_path = f"{base_path}.extract-{strategy}.mp4"
    temp_path = f"{base_path}.extract-{strategy}.{uuid.uuid4().hex}.tmp.mp4"
    common = [
        ffmpeg_bin,
        "-y",
        "-v",
        "warning",
        "-fflags",
        "+genpts+discardcorrupt",
        "-err_detect",
        "ignore_err",
        "-i",
        video_path,
        "-map",
        "0:v:0",
        "-an",
    ]
    if strategy == "remux":
        command = common + [
            "-c:v",
            "copy",
            "-movflags",
            "+faststart",
            temp_path,
        ]
    elif strategy == "transcode":
        command = common + [
            "-vsync",
            "0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            temp_path,
        ]
    else:
        raise ValueError(f"Unknown video extract repair strategy: {strategy}")

    try:
        _run_ffmpeg_command(
            command,
            cfg,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )
        if not os.path.exists(temp_path) or os.path.getsize(temp_path) <= 0:
            raise RuntimeError(f"ffmpeg {strategy} produced empty output")
        os.replace(temp_path, output_path)
        return output_path
    except Exception:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise


def _extract_frames_with_ffmpeg_fallback(
    cfg,
    video_path: str,
    output_dir: str,
    video_start,
    channel_id: str,
    progress_callback=None,
    cancel_event=None,
    window_offset_seconds: float = 0.0,
    window_duration_seconds: float | None = None,
) -> list[dict]:
    if not os.path.exists(video_path):
        raise FileNotFoundError(video_path)

    os.makedirs(output_dir, exist_ok=True)
    ffmpeg_bin = str(cfg.get("FFMPEG_BIN") or "ffmpeg")
    interval_seconds = _extract_fallback_interval_seconds(cfg)
    max_frames = _extract_fallback_max_frames(cfg)
    token = uuid.uuid4().hex[:10]
    video_part = _safe_filename_part(os.path.splitext(os.path.basename(video_path))[0], "video")[:80]
    channel_part = _safe_filename_part(channel_id, "channel")
    prefix = f"{channel_part}_{video_part}_fallback_{token}_"
    pattern = os.path.join(output_dir, f"{prefix}%06d.jpg")
    command = [
        ffmpeg_bin,
        "-y",
        "-v",
        "warning",
        "-fflags",
        "+genpts+discardcorrupt",
        "-err_detect",
        "ignore_err",
    ]
    if window_offset_seconds > 0:
        command.extend(["-ss", f"{window_offset_seconds:.3f}"])
    command.extend([
        "-i",
        video_path,
    ])
    if window_duration_seconds is not None:
        command.extend(["-t", f"{window_duration_seconds:.3f}"])
    command.extend([
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        f"fps=1/{interval_seconds}",
        "-q:v",
        "2",
    ])
    if max_frames > 0:
        command.extend(["-frames:v", str(max_frames)])
    command.append(pattern)

    _run_ffmpeg_command(
        command,
        cfg,
        progress_callback=progress_callback,
        cancel_event=cancel_event,
    )

    frame_paths = sorted(
        os.path.join(output_dir, name)
        for name in os.listdir(output_dir)
        if name.startswith(prefix) and name.lower().endswith(".jpg")
    )
    if not frame_paths:
        raise RuntimeError("ffmpeg fallback did not produce frames")

    source_start_at = _coerce_extract_start_datetime(video_start)
    start_at = source_start_at + timedelta(seconds=window_offset_seconds)
    frames: list[dict] = []
    event_record_path = os.path.join(output_dir, str(cfg.get("EVENT_RECORD_FILENAME") or "event_records.jsonl"))
    with open(event_record_path, "a", encoding="utf-8") as fp:
        for idx, image_path in enumerate(frame_paths):
            captured_at = start_at + timedelta(seconds=idx * interval_seconds)
            record = {
                "timestamp": captured_at.isoformat(),
                "image_path": image_path,
                "extraction_strategy": "ffmpeg_interval_fallback",
                "fallback": True,
                "interval_seconds": interval_seconds,
                "source_video": video_path,
                "source_start": source_start_at.isoformat(),
                "source_offset_seconds": window_offset_seconds + (idx * interval_seconds),
            }
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")
            frames.append({
                "channel_id": channel_id,
                "captured_at": captured_at,
                "image_path": image_path,
                "is_candidate": False,
                "diff_score": None,
                "extraction_strategy": "ffmpeg_interval_fallback",
                "low_quality": True,
                "quality_note": "ffmpeg_interval_fallback",
                "source_start": source_start_at,
                "source_offset_seconds": window_offset_seconds + (idx * interval_seconds),
            })

    if progress_callback is not None:
        progress_callback({
            "progress_percent": 100.0,
            "extracted_count": len(frames),
            "extract_strategy": "ffmpeg_interval_fallback",
            "recovery_status": "complete",
        })
    return frames


def _run_ffmpeg_command(
    command: list[str],
    cfg,
    progress_callback=None,
    cancel_event=None,
) -> subprocess.CompletedProcess:
    """Run FFmpeg with an inactivity timeout instead of an absolute timeout."""
    if not command:
        raise ValueError("ffmpeg command cannot be empty")
    runtime_command = command[:1] + [
        "-nostdin",
        "-nostats",
        "-progress",
        "pipe:1",
        "-threads",
        str(_extract_cpu_threads_per_job(cfg)),
    ] + command[1:]
    try:
        proc = subprocess.Popen(
            runtime_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("未找到 ffmpeg，请先在后端运行环境安装 ffmpeg") from exc

    assert proc.stdout is not None
    stall_seconds = _extract_ffmpeg_timeout_seconds(cfg)
    last_progress_at = time.monotonic()
    output_tail: list[str] = []
    progress_state: dict[str, str] = {}

    while True:
        if _cancel_requested(cancel_event):
            _terminate_extract_worker(proc)
            raise InterruptedError("FFmpeg 处理已取消")

        ready, _, _ = select.select([proc.stdout], [], [], EXTRACT_PROGRESS_POLL_SECONDS)
        if ready:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            text = line.strip()
            output_tail.append(text)
            output_tail = output_tail[-30:]
            if "=" in text:
                key, value = text.split("=", 1)
                if key in {"frame", "fps", "out_time", "out_time_ms", "out_time_us", "speed", "progress"}:
                    progress_state[key] = value
                    last_progress_at = time.monotonic()
                    if key == "progress" and progress_callback is not None:
                        progress_callback({
                            "recovery_status": "ffmpeg_running" if value != "end" else "complete",
                            "ffmpeg_frame": progress_state.get("frame"),
                            "ffmpeg_out_time": progress_state.get("out_time"),
                            "ffmpeg_speed": progress_state.get("speed"),
                        })
        if time.monotonic() - last_progress_at >= stall_seconds and proc.poll() is None:
            _terminate_extract_worker(proc)
            raise TimeoutError(f"ffmpeg 无有效进度超过 {stall_seconds} 秒")

        if proc.poll() is not None:
            for line in proc.stdout.readlines():
                output_tail.append(line.strip())
                output_tail = output_tail[-30:]
            break

    return_code = proc.wait()
    output = "\n".join(output_tail)
    if return_code != 0:
        raise RuntimeError(_truncate_text(output or f"ffmpeg exited with {return_code}", 1000))
    return subprocess.CompletedProcess(command, return_code, stdout=output, stderr="")


def _coerce_extract_start_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _compact_error(exc: Exception) -> str:
    return _truncate_text(_format_task_error(exc), 500)


def _truncate_text(value, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _extract_frames_for_recording_subprocess(
    cfg,
    video_save_path: str,
    output_dir: str,
    video_start,
    channel_id: str,
    progress_callback=None,
    cancel_event=None,
    window_offset_seconds: float = 0.0,
    window_duration_seconds: float | None = None,
) -> list[dict]:
    payload = {
        "cfg": _json_safe_config(cfg),
        "video_path": video_save_path,
        "output_dir": output_dir,
        "video_start": video_start.isoformat() if hasattr(video_start, "isoformat") else str(video_start),
        "channel_id": channel_id,
        "start_offset_seconds": window_offset_seconds,
        "duration_seconds": window_duration_seconds,
    }
    cmd = [sys.executable, "-u", "-m", "app.tasks.video_extract_worker"]
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    thread_budget = str(_extract_cpu_threads_per_job(cfg))
    for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[variable] = thread_budget
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    proc.stdin.write(json.dumps(payload, ensure_ascii=False))
    proc.stdin.close()

    stall_seconds = max(1, _extract_progress_stall_seconds(cfg))
    max_runtime_seconds = _extract_max_runtime_seconds(cfg)
    started_at = time.monotonic()
    last_progress_at = started_at
    result_frames: list[dict] | None = None
    child_error = ""
    child_traceback = ""
    output_tail: list[str] = []
    unstructured_output_count = 0

    while True:
        if _cancel_requested(cancel_event):
            _terminate_extract_worker(proc)
            raise InterruptedError("录像抽帧已取消")
        if time.monotonic() - started_at >= max_runtime_seconds:
            _terminate_extract_worker(proc)
            raise TimeoutError(
                f"单段录像主分析超过 {max_runtime_seconds} 秒，已终止并进入兜底策略"
            )

        ready, _, _ = select.select([proc.stdout], [], [], EXTRACT_PROGRESS_POLL_SECONDS)
        if ready:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            output_tail.append(line.strip())
            output_tail = output_tail[-10:]
            message = _parse_extract_worker_message(line)
            if message is None:
                unstructured_output_count += 1
                if unstructured_output_count <= 10 or unstructured_output_count % 100 == 0:
                    logger.warning(
                        "Video decoder output for %s (messages=%s): %s",
                        os.path.basename(video_save_path),
                        unstructured_output_count,
                        line.strip(),
                    )
            else:
                message_type = message.get("type")
                if message_type == "progress":
                    last_progress_at = time.monotonic()
                    if progress_callback is not None:
                        progress_callback(message.get("progress") or {})
                elif message_type == "result":
                    last_progress_at = time.monotonic()
                    result_frames = _deserialize_extracted_frames(message.get("frames") or [])
                elif message_type == "error":
                    last_progress_at = time.monotonic()
                    child_error = str(message.get("error") or "")
                    child_traceback = str(message.get("traceback") or "")

        silent_seconds = time.monotonic() - last_progress_at
        if silent_seconds >= stall_seconds:
            _terminate_extract_worker(proc)
            tail_text = "\n".join(output_tail[-5:])
            raise TimeoutError(
                "单段录像抽帧无有效进度，已终止当前尝试并进入兜底策略。"
                f"超过 {stall_seconds} 秒帧处理进度没有增长；"
                f"文件：{video_save_path}；最后输出：{tail_text or '无'}"
            )

        if proc.poll() is not None:
            for line in proc.stdout.readlines():
                output_tail.append(line.strip())
                output_tail = output_tail[-10:]
                message = _parse_extract_worker_message(line)
                if message and message.get("type") == "result":
                    result_frames = _deserialize_extracted_frames(message.get("frames") or [])
                elif message and message.get("type") == "error":
                    child_error = str(message.get("error") or "")
                    child_traceback = str(message.get("traceback") or "")
            break

    return_code = proc.wait()
    if return_code != 0:
        detail = child_traceback or child_error or "\n".join(output_tail[-5:])
        raise RuntimeError(f"单段录像抽帧子进程失败，退出码 {return_code}: {detail}")
    if result_frames is None:
        raise RuntimeError(f"单段录像抽帧子进程未返回结果: {video_save_path}")
    return result_frames


def _parse_extract_worker_message(line: str) -> dict | None:
    text = (line or "").strip()
    if not text:
        return None
    try:
        message = json.loads(text)
    except json.JSONDecodeError:
        return None
    return message if isinstance(message, dict) else None


def _deserialize_extracted_frames(frames: list[dict]) -> list[dict]:
    result = []
    for frame in frames:
        item = dict(frame)
        captured_at = item.get("captured_at")
        if isinstance(captured_at, str):
            item["captured_at"] = datetime.fromisoformat(captured_at)
        result.append(item)
    return result


def _terminate_extract_worker(proc) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def _json_safe_config(value):
    if isinstance(value, dict):
        return {str(key): _json_safe_config(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_config(item) for item in value]
    if isinstance(value, timedelta):
        return value.total_seconds()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


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
