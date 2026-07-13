import logging
import os
import time
import uuid
from datetime import date, datetime, timedelta, timezone

from billiard.exceptions import SoftTimeLimitExceeded
from celery_app import celery
from app import db
from app.models import (
    CapturedImage,
    DishRecognition,
    DailyMenu,
    Dish,
    MatchResult,
    MatchStatusEnum,
    TaskLog,
    ImageStatusEnum,
)
from app.models.menu import (
    MENU_NOT_CONFIGURED_ALERT_TYPE,
    RECOGNITION_MENU_SCOPE_ALL,
    is_menu_configured,
    menu_not_configured_message,
    normalize_recognition_menu_scope,
    resolve_meal_slot_for_datetime,
)
from app.services.region_candidates import create_region_candidates_from_recognition
from app.services.inference_client import InferenceServiceError
from app.services.runtime_config import get_effective_config

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


LOW_CONFIDENCE_THRESHOLD = 0.6
RECOGNITION_MAX_RETRIES = 3
RECOGNITION_TASK_SOFT_TIME_LIMIT = _env_int("RECOGNITION_TASK_SOFT_TIME_LIMIT", 150)
RECOGNITION_TASK_TIME_LIMIT = max(
    RECOGNITION_TASK_SOFT_TIME_LIMIT + 30,
    _env_int("RECOGNITION_TASK_TIME_LIMIT", 180),
)
RECOGNITION_LEASE_SECONDS = RECOGNITION_TASK_TIME_LIMIT + 120
RECOGNITION_DISPATCH_LEASE_SECONDS = _env_int("RECOGNITION_DISPATCH_LEASE_SECONDS", 300)
RECOGNITION_QUEUED_LEASE_SECONDS = _env_int("RECOGNITION_QUEUED_LEASE_SECONDS", 21600)
RETRYABLE_INFERENCE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
PENDING_DISPATCH_LIMIT = 500


def _elapsed_seconds(started: float) -> float:
    return round(max(0.0, time.perf_counter() - started), 3)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _build_duration_meta(batch_started: float, image_durations: list[float]) -> dict:
    processed_count = len(image_durations)
    total_image_seconds = round(sum(image_durations), 3)
    return {
        "analysis_duration_seconds": _elapsed_seconds(batch_started),
        "processed_image_count": processed_count,
        "image_processing_duration_seconds": total_image_seconds,
        "avg_image_duration_seconds": round(total_image_seconds / processed_count, 3) if processed_count else None,
        "min_image_duration_seconds": round(min(image_durations), 3) if image_durations else None,
        "max_image_duration_seconds": round(max(image_durations), 3) if image_durations else None,
    }


def _build_recognition_raw_response(result: dict, dish_info: dict) -> dict:
    return {
        "position": dish_info.get("position", ""),
        "bbox": dish_info.get("bbox"),
        "bbox_source": dish_info.get("bbox_source", ""),
        "notes": str(dish_info.get("notes") or result.get("notes") or "").strip(),
        "raw_response": result.get("raw_response"),
        "timings_ms": result.get("timings_ms"),
    }


def _create_region_candidates_safely(
    *,
    image: CapturedImage,
    recognition_result: dict,
    failure_message: str,
) -> None:
    """Keep optional region-candidate writes from poisoning the main transaction."""
    image_id = image.id
    try:
        with db.session.begin_nested():
            create_region_candidates_from_recognition(
                image=image,
                recognition_result=recognition_result,
            )
            db.session.flush()
    except Exception as region_error:
        logger.warning(
            failure_message,
            image_id,
            region_error,
            exc_info=True,
        )


def _ordered_active_dishes(dish_ids: list[int]) -> list[Dish]:
    if not dish_ids:
        return []

    dishes = Dish.query.filter(
        Dish.id.in_(dish_ids),
        Dish.is_active.is_(True),
    ).all()
    dish_by_id = {dish.id: dish for dish in dishes}
    return [dish_by_id[dish_id] for dish_id in dish_ids if dish_id in dish_by_id]


def _resolve_candidate_dishes_for_image(img: CapturedImage, cfg: dict) -> list[Dish]:
    cfg = get_effective_config(cfg)
    menu_scope = normalize_recognition_menu_scope(cfg.get("RECOGNITION_MENU_SCOPE", "all"))
    if menu_scope == RECOGNITION_MENU_SCOPE_ALL:
        return Dish.query.filter(Dish.is_active.is_(True)).all()

    menu = DailyMenu.query.filter_by(menu_date=img.capture_date).first()
    if not is_menu_configured(menu, cfg):
        raise RuntimeError(menu_not_configured_message(img.capture_date))

    meal_slot = resolve_meal_slot_for_datetime(
        img.captured_at,
        timezone_name=cfg.get("VIDEO_TIMEZONE") or cfg.get("APP_TIMEZONE", "Asia/Shanghai"),
        config=cfg,
    )
    return _ordered_active_dishes(menu.dish_ids_for_recognition(meal_slot, menu_scope, config=cfg))


def _config_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _promote_candidate_fallback(img: CapturedImage, cfg: dict, *, reason: str) -> int | None:
    """Promote the best standby frame from the same channel/second and queue it.

    The failed or weak primary is demoted first, so the existing recognition and
    matching pipeline still sees exactly one automatic primary frame.
    """
    effective_config = get_effective_config(cfg)
    if not _config_bool(effective_config.get("RECOGNITION_AUTO_CANDIDATE_FALLBACK"), True):
        return None

    second_start = img.captured_at.replace(microsecond=0)
    query = CapturedImage.query.filter(
        CapturedImage.id != img.id,
        CapturedImage.capture_date == img.capture_date,
        CapturedImage.channel_id == img.channel_id,
        CapturedImage.captured_at >= second_start,
        CapturedImage.captured_at < second_start + timedelta(seconds=1),
        CapturedImage.status == ImageStatusEnum.pending,
        CapturedImage.is_candidate.is_(True),
    )
    if img.video_recording_job_id is not None:
        query = query.filter(CapturedImage.video_recording_job_id == img.video_recording_job_id)
    else:
        query = query.filter(CapturedImage.video_recording_job_id.is_(None))
        if img.source_video:
            query = query.filter(CapturedImage.source_video == img.source_video)
        else:
            query = query.filter(CapturedImage.source_video.is_(None))

    fallback = query.order_by(
        CapturedImage.id.asc(),
        CapturedImage.captured_at.asc(),
    ).first()
    if fallback is None:
        return None

    fallback.is_candidate = False
    fallback.recognition_task_id = None
    fallback.recognition_task_log_id = None
    fallback.recognition_started_at = None
    fallback.recognition_finished_at = None
    fallback.recognition_lease_expires_at = None
    fallback.recognition_error_code = None
    fallback.recognition_error_message = None
    img.is_candidate = True
    MatchResult.query.filter(
        MatchResult.image_id.in_((img.id, fallback.id)),
        MatchResult.status == MatchStatusEnum.unmatched_image,
    ).delete(synchronize_session=False)
    db.session.commit()

    fallback_task = None
    try:
        fallback_task = enqueue_recognition_images(
            [fallback.id],
            target_date=fallback.capture_date,
        )
    except Exception as error:
        db.session.rollback()
        logger.error(
            "Failed to enqueue candidate fallback %s for image %s: %s",
            fallback.id,
            img.id,
            error,
            exc_info=True,
        )

    if fallback_task is not None:
        fallback_task.meta = {
            **(fallback_task.meta or {}),
            "fallback_from_image_id": img.id,
            "fallback_reason": reason,
            "status_text": f"主帧识别结果不可用，正在识别备用帧 {fallback.id}",
        }
        db.session.commit()

    logger.info(
        "Promoted candidate image %s after primary image %s (%s)",
        fallback.id,
        img.id,
        reason,
    )
    return fallback.id


def _mark_recognition_stopped_for_missing_menu(task_log: TaskLog, target_date: date, image_count: int = 0) -> None:
    message = menu_not_configured_message(target_date)
    task_log.status = "failed"
    task_log.total_count = image_count
    task_log.error_count = 1
    task_log.error_message = message
    task_log.finished_at = datetime.utcnow()
    task_log.meta = {
        **(task_log.meta or {}),
        "alert_type": MENU_NOT_CONFIGURED_ALERT_TYPE,
        "status_text": message,
    }
    db.session.commit()
    logger.warning(message)


def _load_images_for_rerun(target_date: date) -> list[CapturedImage]:
    """Load every non-candidate image for a date for a forced re-recognition.

    Previous non-manual recognitions are cleared and image status reset to
    pending so the standard batch loop reprocesses them. Images that carry a
    manual review are left untouched (their results are preserved).
    """
    all_images = (
        CapturedImage.query.filter(
            CapturedImage.capture_date == target_date,
            CapturedImage.is_candidate.is_(False),
        )
        .all()
    )
    image_ids = [img.id for img in all_images]
    if not image_ids:
        return []

    manual_image_ids = {
        row[0]
        for row in db.session.query(DishRecognition.image_id)
        .filter(
            DishRecognition.image_id.in_(image_ids),
            DishRecognition.is_manual.is_(True),
        )
        .all()
    }

    DishRecognition.query.filter(
        DishRecognition.image_id.in_(image_ids),
        DishRecognition.is_manual.is_(False),
    ).delete(synchronize_session=False)

    images = [img for img in all_images if img.id not in manual_image_ids]
    for img in images:
        img.status = ImageStatusEnum.pending
        img.recognition_attempt_count = 0
        img.recognition_task_id = None
        img.recognition_task_log_id = None
        img.recognition_started_at = None
        img.recognition_finished_at = None
        img.recognition_lease_expires_at = None
        img.recognition_error_code = None
        img.recognition_error_message = None
    db.session.commit()
    return images


def _publish_recognition_task(image_id: int, task_log_id: int | None) -> None:
    celery_task_id = str(uuid.uuid4())
    recognize_single_image.apply_async(
        args=[image_id, task_log_id],
        queue="recognition",
        task_id=celery_task_id,
    )
    # The task may start before this update. Only mark a still-queued image as
    # published; a processing/terminal state written by the worker wins.
    CapturedImage.query.filter(
        CapturedImage.id == image_id,
        CapturedImage.status == ImageStatusEnum.queued,
        CapturedImage.recognition_task_log_id == task_log_id,
        CapturedImage.recognition_task_id.is_(None),
    ).update({
        CapturedImage.recognition_task_id: celery_task_id,
        CapturedImage.recognition_lease_expires_at: (
            _utcnow() + timedelta(seconds=RECOGNITION_QUEUED_LEASE_SECONDS)
        ),
    }, synchronize_session=False)
    db.session.commit()


def enqueue_recognition_images(
    image_ids: list[int],
    *,
    target_date: date | None = None,
    force_rerun: bool = False,
) -> TaskLog | None:
    """Queue primary images as independent, idempotent recognition tasks.

    This helper is intentionally callable from video tasks after each recording
    commits its images, so recognition no longer waits for the full-day sync.
    """
    normalized_ids = sorted({int(image_id) for image_id in image_ids if image_id})
    if not normalized_ids:
        return None

    if target_date is None:
        first_image = (
            db.session.query(CapturedImage.capture_date)
            .filter(
                CapturedImage.id.in_(normalized_ids),
                CapturedImage.is_candidate.is_(False),
            )
            .order_by(CapturedImage.id.asc())
            .first()
        )
        if not first_image:
            return None
        target_date = first_image[0]

    task_log = TaskLog(
        task_type="ai_recognition",
        task_date=target_date,
        status="running",
        total_count=0,
        meta={
            "force_rerun": bool(force_rerun),
            "dispatch_mode": "per_image",
            "status_text": "正在认领待识别图片",
        },
    )
    db.session.add(task_log)
    db.session.flush()

    dispatch_deadline = _utcnow() + timedelta(seconds=RECOGNITION_DISPATCH_LEASE_SECONDS)
    claimed_count = CapturedImage.query.filter(
        CapturedImage.id.in_(normalized_ids),
        CapturedImage.status == ImageStatusEnum.pending,
        CapturedImage.is_candidate.is_(False),
    ).update({
        CapturedImage.status: ImageStatusEnum.queued,
        CapturedImage.recognition_task_log_id: task_log.id,
        CapturedImage.recognition_task_id: None,
        CapturedImage.recognition_started_at: None,
        CapturedImage.recognition_finished_at: None,
        CapturedImage.recognition_lease_expires_at: dispatch_deadline,
        CapturedImage.recognition_error_code: None,
        CapturedImage.recognition_error_message: None,
    }, synchronize_session=False)

    if claimed_count <= 0:
        db.session.delete(task_log)
        db.session.commit()
        return None

    task_log.total_count = claimed_count
    task_log.meta = {
        **(task_log.meta or {}),
        "status_text": f"已提交 {claimed_count} 张图片到识别队列",
    }
    db.session.commit()

    images = (
        CapturedImage.query.filter(
            CapturedImage.recognition_task_log_id == task_log.id,
            CapturedImage.status == ImageStatusEnum.queued,
        )
        .order_by(CapturedImage.id.asc())
        .all()
    )
    for img in images:
        try:
            _publish_recognition_task(img.id, task_log.id)
        except Exception as error:
            logger.error("Failed to queue recognition for image %s: %s", img.id, error)
            db.session.rollback()
            # Publishing is not transactional with PostgreSQL. Even when the
            # client raises, Redis may already have accepted the task. Keep the
            # queued dispatch lease intact so either that task claims the image
            # or the recovery job safely republishes it after the lease expires.

    db.session.expire_all()
    return TaskLog.query.get(task_log.id)


def _complete_task_log_image(
    task_log_id: int | None,
    *,
    invalid: bool = False,
    low_confidence_count: int = 0,
    error_message: str | None = None,
) -> None:
    if not task_log_id:
        return

    updates = {}
    if error_message:
        updates[TaskLog.error_count] = db.func.coalesce(TaskLog.error_count, 0) + 1
        updates[TaskLog.error_message] = error_message[:2000]
    else:
        updates[TaskLog.success_count] = db.func.coalesce(TaskLog.success_count, 0) + 1
        updates[TaskLog.low_confidence_count] = (
            db.func.coalesce(TaskLog.low_confidence_count, 0) + max(0, int(low_confidence_count))
        )
        if invalid:
            updates[TaskLog.invalid_count] = db.func.coalesce(TaskLog.invalid_count, 0) + 1

    TaskLog.query.filter(TaskLog.id == task_log_id).update(updates, synchronize_session=False)
    db.session.commit()
    db.session.expire_all()

    task_log = TaskLog.query.get(task_log_id)
    if not task_log:
        return
    processed = int(task_log.success_count or 0) + int(task_log.error_count or 0)
    total = int(task_log.total_count or 0)
    if total <= 0 or processed < total:
        return

    if int(task_log.error_count or 0) == 0:
        task_log.status = "success"
    elif int(task_log.success_count or 0) == 0:
        task_log.status = "failed"
    else:
        task_log.status = "partial"
    task_log.finished_at = _utcnow()
    elapsed = 0.0
    if task_log.started_at:
        started_at = task_log.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        elapsed = max(0.0, (_utcnow() - started_at).total_seconds())
    task_log.meta = {
        **(task_log.meta or {}),
        "processed_image_count": processed,
        "analysis_duration_seconds": round(elapsed, 3),
        "avg_image_duration_seconds": round(elapsed / processed, 3) if processed else None,
        "status_text": (
            f"识别完成：成功 {task_log.success_count or 0} 张"
            f"（无效 {task_log.invalid_count or 0} 张），失败 {task_log.error_count or 0} 张"
        ),
    }
    db.session.commit()


def _claim_image(
    image_id: int,
    task_id: str | None,
    task_log_id: int | None,
) -> CapturedImage | None:
    now = _utcnow()
    claim_query = CapturedImage.query.filter(
        CapturedImage.id == image_id,
        CapturedImage.status.in_((
            ImageStatusEnum.pending,
            ImageStatusEnum.queued,
            ImageStatusEnum.retry_wait,
        )),
    )
    if task_log_id is None:
        claim_query = claim_query.filter(CapturedImage.recognition_task_log_id.is_(None))
    else:
        claim_query = claim_query.filter(CapturedImage.recognition_task_log_id == task_log_id)
    claimed = claim_query.update({
        CapturedImage.status: ImageStatusEnum.processing,
        CapturedImage.recognition_task_id: task_id,
        CapturedImage.recognition_attempt_count: db.func.coalesce(CapturedImage.recognition_attempt_count, 0) + 1,
        CapturedImage.recognition_started_at: now,
        CapturedImage.recognition_finished_at: None,
        CapturedImage.recognition_lease_expires_at: now + timedelta(seconds=RECOGNITION_LEASE_SECONDS),
        CapturedImage.recognition_error_code: None,
        CapturedImage.recognition_error_message: None,
    }, synchronize_session=False)
    db.session.commit()
    if not claimed:
        return None
    return CapturedImage.query.get(image_id)


def _mark_image_retry(img: CapturedImage, error: Exception, countdown: int) -> None:
    now = _utcnow()
    img.status = ImageStatusEnum.retry_wait
    img.recognition_error_code = "inference_temporarily_unavailable"
    img.recognition_error_message = str(error)[:2000]
    img.recognition_lease_expires_at = now + timedelta(seconds=countdown + RECOGNITION_LEASE_SECONDS)
    db.session.commit()


def _mark_image_error(
    img: CapturedImage,
    error: Exception,
    *,
    error_code: str = "recognition_failed",
) -> None:
    img.status = ImageStatusEnum.error
    img.recognition_finished_at = _utcnow()
    img.recognition_lease_expires_at = None
    img.recognition_error_code = error_code
    img.recognition_error_message = str(error)[:2000]
    db.session.commit()


def _retry_countdown(retries: int) -> int:
    return (15, 60, 300)[min(max(int(retries), 0), 2)]


@celery.task(name="app.tasks.recognition.run_recognition_batch", bind=True)
def run_recognition_batch(self, date_str: str, force_rerun: bool = False):
    from flask import current_app

    cfg = current_app.config
    target_date = date.fromisoformat(date_str)

    if force_rerun:
        images = _load_images_for_rerun(target_date)
    else:
        images = (
            CapturedImage.query.filter_by(
                capture_date=target_date,
                status=ImageStatusEnum.pending,
            )
            .filter(CapturedImage.is_candidate.is_(False))
            .all()
        )

    menu_scope = normalize_recognition_menu_scope(cfg.get("RECOGNITION_MENU_SCOPE", "all"))
    menu = DailyMenu.query.filter_by(menu_date=target_date).first()
    if menu_scope != RECOGNITION_MENU_SCOPE_ALL and not is_menu_configured(menu, cfg):
        task_log = TaskLog(task_type="ai_recognition", task_date=target_date)
        db.session.add(task_log)
        db.session.commit()
        _mark_recognition_stopped_for_missing_menu(task_log, target_date, len(images))
        try:
            from app.tasks.video import _send_admin_alert
            _send_admin_alert(menu_not_configured_message(target_date))
        except Exception as e:
            logger.error("Failed to send missing menu alert: %s", e)
        return {
            "skipped": True,
            "reason": MENU_NOT_CONFIGURED_ALERT_TYPE,
            "date": date_str,
        }
    task_log = enqueue_recognition_images(
        [img.id for img in images],
        target_date=target_date,
        force_rerun=force_rerun,
    )
    return {
        "date": date_str,
        "queued": int(task_log.total_count or 0) if task_log else 0,
        "task_log_id": task_log.id if task_log else None,
    }


@celery.task(
    name="app.tasks.recognition.recognize_single_image",
    bind=True,
    max_retries=RECOGNITION_MAX_RETRIES,
    soft_time_limit=RECOGNITION_TASK_SOFT_TIME_LIMIT,
    time_limit=RECOGNITION_TASK_TIME_LIMIT,
    acks_late=True,
)
def recognize_single_image(self, image_id: int, task_log_id: int | None = None):
    from flask import current_app
    from app.services.dish_recognition import DishRecognitionService
    from app.tasks.matching import match_single_image

    cfg = current_app.config
    img = _claim_image(image_id, getattr(self.request, "id", None), task_log_id)
    if not img:
        return {"image_id": image_id, "skipped": True, "reason": "not_claimable"}

    task_log_id = task_log_id or img.recognition_task_log_id

    recognizer = DishRecognitionService(cfg)
    try:
        dishes = _resolve_candidate_dishes_for_image(img, cfg)
    except RuntimeError as e:
        logger.warning(str(e))
        _mark_image_error(img, e, error_code=MENU_NOT_CONFIGURED_ALERT_TYPE)
        _complete_task_log_image(task_log_id, error_message=str(e))
        try:
            from app.tasks.video import _send_admin_alert
            _send_admin_alert(str(e))
        except Exception as alert_error:
            logger.error("Failed to send missing menu alert: %s", alert_error)
        return {
            "skipped": True,
            "reason": MENU_NOT_CONFIGURED_ALERT_TYPE,
            "image_id": image_id,
        }

    candidate_dishes = [{"id": d.id, "name": d.name, "description": d.description or ""} for d in dishes]
    dish_name_map = {d.name.lower(): d for d in dishes}

    try:
        result = recognizer.recognize_dishes(img.image_path, candidate_dishes)
        DishRecognition.query.filter_by(image_id=image_id).delete()

        if result.get("valid_image") is False:
            invalid_reason = str(result.get("invalid_reason") or "invalid_image")
            MatchResult.query.filter_by(image_id=image_id).delete(synchronize_session=False)
            db.session.flush()
            _create_region_candidates_safely(
                image=img,
                recognition_result=result,
                failure_message="Failed to clear stale region candidates for invalid image %s: %s",
            )
            img.status = ImageStatusEnum.invalid
            img.recognition_finished_at = _utcnow()
            img.recognition_lease_expires_at = None
            img.recognition_error_code = invalid_reason
            img.recognition_error_message = str(result.get("notes") or "图片中未检测到餐盘")[:2000]
            db.session.commit()
            _complete_task_log_image(task_log_id, invalid=True)
            fallback_image_id = _promote_candidate_fallback(
                img,
                cfg,
                reason=invalid_reason,
            )
            logger.info("Image %s marked invalid: %s", image_id, invalid_reason)
            return {
                "image_id": image_id,
                "status": ImageStatusEnum.invalid.value,
                "reason": invalid_reason,
                "fallback_image_id": fallback_image_id,
            }

        low_confidence_count = 0
        recognized_dish_count = 0
        for dish_info in result.get("dishes", []):
            name_raw = dish_info.get("name", "")
            confidence = float(dish_info.get("confidence", 0))
            matched_dish = dish_name_map.get(name_raw.lower())
            rec = DishRecognition(
                image_id=image_id,
                dish_id=matched_dish.id if matched_dish else None,
                dish_name_raw=name_raw,
                confidence=confidence,
                is_low_confidence=confidence < LOW_CONFIDENCE_THRESHOLD,
                model_version=result.get("model_version") or cfg.get("QWEN_MODEL", "qwen-vl-max"),
                raw_response=_build_recognition_raw_response(result, dish_info),
            )
            db.session.add(rec)
            recognized_dish_count += 1
            if rec.is_low_confidence:
                low_confidence_count += 1

        # Persist the primary recognition rows before entering the optional
        # region-candidate savepoint. This keeps schema/data errors visible as
        # primary recognition failures instead of query-triggered autoflushes.
        db.session.flush()
        _create_region_candidates_safely(
            image=img,
            recognition_result=result,
            failure_message="Failed to create region candidates for image %s: %s",
        )

        img.status = ImageStatusEnum.identified
        img.recognition_finished_at = _utcnow()
        img.recognition_lease_expires_at = None
        img.recognition_error_code = None
        img.recognition_error_message = None
        db.session.commit()
        _complete_task_log_image(task_log_id, low_confidence_count=low_confidence_count)
        fallback_reason = None
        if recognized_dish_count == 0:
            fallback_reason = "no_dishes_recognized"
        elif low_confidence_count == recognized_dish_count:
            fallback_reason = "all_dishes_low_confidence"
        if fallback_reason:
            fallback_image_id = _promote_candidate_fallback(
                img,
                cfg,
                reason=fallback_reason,
            )
            if fallback_image_id is not None:
                return {
                    "image_id": image_id,
                    "status": ImageStatusEnum.identified.value,
                    "fallback_image_id": fallback_image_id,
                }
        match_single_image.delay(image_id)
        return {"image_id": image_id, "status": ImageStatusEnum.identified.value}
    except InferenceServiceError as e:
        db.session.rollback()
        img = CapturedImage.query.get(image_id)
        retryable = getattr(e, "status_code", 502) in RETRYABLE_INFERENCE_STATUS_CODES
        if retryable and int(self.request.retries or 0) < RECOGNITION_MAX_RETRIES:
            countdown = _retry_countdown(self.request.retries)
            _mark_image_retry(img, e, countdown)
            logger.warning(
                "Recognition retry for image %s in %ss (%s/%s): %s",
                image_id,
                countdown,
                int(self.request.retries or 0) + 1,
                RECOGNITION_MAX_RETRIES,
                e,
            )
            raise self.retry(exc=e, countdown=countdown)
        logger.error("Single recognition failed for image %s: %s", image_id, e)
        _mark_image_error(img, e, error_code="inference_failed")
        _complete_task_log_image(task_log_id, error_message=str(e))
        return {
            "image_id": image_id,
            "status": ImageStatusEnum.error.value,
            "error": str(e),
        }
    except SoftTimeLimitExceeded as e:
        db.session.rollback()
        img = CapturedImage.query.get(image_id)
        if int(self.request.retries or 0) < RECOGNITION_MAX_RETRIES:
            countdown = _retry_countdown(self.request.retries)
            _mark_image_retry(img, e, countdown)
            raise self.retry(exc=e, countdown=countdown)
        _mark_image_error(img, e, error_code="recognition_timeout")
        _complete_task_log_image(task_log_id, error_message="单图识别超时")
        return {"image_id": image_id, "status": ImageStatusEnum.error.value, "error": "timeout"}
    except Exception as e:
        db.session.rollback()
        logger.error("Single recognition failed for image %s: %s", image_id, e, exc_info=True)
        img = CapturedImage.query.get(image_id)
        _mark_image_error(img, e)
        _complete_task_log_image(task_log_id, error_message=str(e))
        return {"image_id": image_id, "status": ImageStatusEnum.error.value, "error": str(e)}


@celery.task(name="app.tasks.recognition.requeue_stale_recognition_images")
def requeue_stale_recognition_images():
    """Recover unpublished queued images and work abandoned after worker loss."""
    now = _utcnow()
    stale_ids = [
        row[0]
        for row in db.session.query(CapturedImage.id)
        .filter(
            CapturedImage.status.in_((ImageStatusEnum.processing, ImageStatusEnum.retry_wait)),
            CapturedImage.recognition_lease_expires_at.isnot(None),
            CapturedImage.recognition_lease_expires_at < now,
        )
        .order_by(CapturedImage.recognition_lease_expires_at.asc())
        .limit(500)
        .all()
    ]
    stale_queued_ids = [
        row[0]
        for row in db.session.query(CapturedImage.id)
        .filter(
            CapturedImage.status == ImageStatusEnum.queued,
            CapturedImage.recognition_lease_expires_at.isnot(None),
            CapturedImage.recognition_lease_expires_at < now,
        )
        .order_by(CapturedImage.recognition_lease_expires_at.asc())
        .limit(max(0, 500 - len(stale_ids)))
        .all()
    ]
    candidate_ids = stale_ids + stale_queued_ids
    if not candidate_ids:
        return {"requeued": 0}

    dispatch_deadline = now + timedelta(seconds=RECOGNITION_DISPATCH_LEASE_SECONDS)
    claimed_ids: list[int] = []
    for image_id in candidate_ids:
        claimed = CapturedImage.query.filter(
            CapturedImage.id == image_id,
            CapturedImage.status.in_((
                ImageStatusEnum.queued,
                ImageStatusEnum.processing,
                ImageStatusEnum.retry_wait,
            )),
            CapturedImage.recognition_lease_expires_at.isnot(None),
            CapturedImage.recognition_lease_expires_at < now,
        ).update({
            CapturedImage.status: ImageStatusEnum.queued,
            CapturedImage.recognition_task_id: None,
            CapturedImage.recognition_lease_expires_at: dispatch_deadline,
            CapturedImage.recognition_error_code: "stale_task_requeued",
            CapturedImage.recognition_error_message: "识别任务中断，系统已自动重新入队",
        }, synchronize_session=False)
        if claimed:
            claimed_ids.append(image_id)
    db.session.commit()

    published = 0
    for image_id in claimed_ids:
        img = CapturedImage.query.get(image_id)
        task_log_id = img.recognition_task_log_id if img else None
        try:
            _publish_recognition_task(image_id, task_log_id)
            published += 1
        except Exception as error:
            logger.error("Failed to republish recognition for image %s: %s", image_id, error)
            db.session.rollback()
    return {"requeued": published, "failed": len(claimed_ids) - published}


@celery.task(name="app.tasks.recognition.dispatch_pending_recognition_images")
def dispatch_pending_recognition_images():
    """Continuously drain old and newly-created pending images in bounded pages."""
    from flask import current_app

    cfg = current_app.config
    pending_dates = (
        db.session.query(CapturedImage.capture_date)
        .filter(
            CapturedImage.status == ImageStatusEnum.pending,
            CapturedImage.is_candidate.is_(False),
        )
        .distinct()
        .order_by(CapturedImage.capture_date.desc())
        .limit(14)
        .all()
    )
    if not pending_dates:
        return {"queued": 0, "skipped_dates": []}

    queued = 0
    skipped_dates: list[str] = []
    menu_scope = normalize_recognition_menu_scope(cfg.get("RECOGNITION_MENU_SCOPE", "all"))
    for (target_date,) in pending_dates:
        if queued >= PENDING_DISPATCH_LIMIT:
            break
        menu = DailyMenu.query.filter_by(menu_date=target_date).first()
        if menu_scope != RECOGNITION_MENU_SCOPE_ALL and not is_menu_configured(menu, cfg):
            skipped_dates.append(target_date.isoformat())
            continue
        image_ids = [
            row[0]
            for row in db.session.query(CapturedImage.id)
            .filter(
                CapturedImage.capture_date == target_date,
                CapturedImage.status == ImageStatusEnum.pending,
                CapturedImage.is_candidate.is_(False),
            )
            .order_by(CapturedImage.id.asc())
            .limit(PENDING_DISPATCH_LIMIT - queued)
            .all()
        ]
        task_log = enqueue_recognition_images(image_ids, target_date=target_date)
        if task_log:
            queued += int(task_log.total_count or 0)

    return {"queued": queued, "skipped_dates": skipped_dates}
