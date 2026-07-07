import logging
from datetime import date, datetime
from celery_app import celery
from app import db
from app.models import CapturedImage, DishRecognition, DailyMenu, Dish, TaskLog, ImageStatusEnum
from app.models.menu import (
    MENU_NOT_CONFIGURED_ALERT_TYPE,
    RECOGNITION_MENU_SCOPE_ALL,
    is_menu_configured,
    menu_not_configured_message,
    normalize_recognition_menu_scope,
    resolve_meal_slot_for_datetime,
)
from app.services.region_candidates import create_region_candidates_from_recognition
from app.services.runtime_config import get_effective_config

logger = logging.getLogger(__name__)

LOW_CONFIDENCE_THRESHOLD = 0.6


def _build_recognition_raw_response(result: dict, dish_info: dict) -> dict:
    return {
        "position": dish_info.get("position", ""),
        "bbox": dish_info.get("bbox"),
        "bbox_source": dish_info.get("bbox_source", ""),
        "notes": str(dish_info.get("notes") or result.get("notes") or "").strip(),
        "raw_response": result.get("raw_response"),
    }


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
    db.session.commit()
    return images


@celery.task(name="app.tasks.recognition.run_recognition_batch", bind=True)
def run_recognition_batch(self, date_str: str, force_rerun: bool = False):
    from flask import current_app
    from app.services.dish_recognition import DishRecognitionService

    cfg = current_app.config
    target_date = date.fromisoformat(date_str)
    recognizer = DishRecognitionService(cfg)

    task_log = TaskLog(task_type="ai_recognition", task_date=target_date)
    if force_rerun:
        task_log.meta = {"force_rerun": True}
    db.session.add(task_log)
    db.session.commit()

    if force_rerun:
        # Re-recognize ALL non-candidate images for the date (manual reviews skipped)
        images = _load_images_for_rerun(target_date)
    else:
        # Get pending images
        images = (
            CapturedImage.query.filter_by(
                capture_date=target_date,
                status=ImageStatusEnum.pending,
            )
            .filter(CapturedImage.is_candidate.is_(False))
            .all()
        )

    task_log.total_count = len(images)
    db.session.commit()

    menu_scope = normalize_recognition_menu_scope(cfg.get("RECOGNITION_MENU_SCOPE", "all"))
    menu = DailyMenu.query.filter_by(menu_date=target_date).first()
    if menu_scope != RECOGNITION_MENU_SCOPE_ALL and not is_menu_configured(menu, cfg):
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

    success = low_conf = errors = 0

    for img in images:
        try:
            dishes = _resolve_candidate_dishes_for_image(img, cfg)
            candidate_dishes = [{"id": d.id, "name": d.name, "description": d.description or ""} for d in dishes]
            dish_name_map = {d.name.lower(): d for d in dishes}
            result = recognizer.recognize_dishes(img.image_path, candidate_dishes)

            # Delete old recognitions if any
            DishRecognition.query.filter_by(image_id=img.id).delete()

            for dish_info in result.get("dishes", []):
                name_raw = dish_info.get("name", "")
                confidence = float(dish_info.get("confidence", 0))
                is_low = confidence < LOW_CONFIDENCE_THRESHOLD

                # Try to match to dish in DB
                matched_dish = dish_name_map.get(name_raw.lower())

                rec = DishRecognition(
                    image_id=img.id,
                    dish_id=matched_dish.id if matched_dish else None,
                    dish_name_raw=name_raw,
                    confidence=confidence,
                    is_low_confidence=is_low,
                    model_version=result.get("model_version") or cfg.get("QWEN_MODEL", "qwen-vl-max"),
                    raw_response=_build_recognition_raw_response(result, dish_info),
                )
                db.session.add(rec)
                if is_low:
                    low_conf += 1

            try:
                create_region_candidates_from_recognition(image=img, recognition_result=result)
            except Exception as region_error:
                logger.warning("Failed to create region candidates for image %s: %s", img.id, region_error, exc_info=True)

            img.status = ImageStatusEnum.identified
            db.session.commit()
            success += 1

        except Exception as e:
            logger.error(f"Recognition failed for image {img.id}: {e}")
            img.status = ImageStatusEnum.error
            db.session.commit()
            errors += 1

    task_log.status = "success" if errors == 0 else "partial"
    task_log.success_count = success
    task_log.low_confidence_count = low_conf
    task_log.error_count = errors
    task_log.finished_at = datetime.utcnow()
    db.session.commit()

    logger.info(
        f"Recognition batch {date_str}: {success} ok, {low_conf} low-conf, {errors} errors"
    )

    # Trigger matching
    if success > 0:
        from app.tasks.matching import run_matching_for_date
        run_matching_for_date.delay(date_str)


@celery.task(
    name="app.tasks.recognition.recognize_single_image",
    soft_time_limit=900,
    time_limit=1200,
)
def recognize_single_image(image_id: int):
    from flask import current_app
    from app.services.dish_recognition import DishRecognitionService
    from app.tasks.matching import match_single_image

    cfg = current_app.config
    img = CapturedImage.query.get(image_id)
    if not img:
        return

    recognizer = DishRecognitionService(cfg)
    try:
        dishes = _resolve_candidate_dishes_for_image(img, cfg)
    except RuntimeError as e:
        logger.warning(str(e))
        img.status = ImageStatusEnum.error
        db.session.commit()
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

        try:
            create_region_candidates_from_recognition(image=img, recognition_result=result)
        except Exception as region_error:
            logger.warning("Failed to create region candidates for image %s: %s", img.id, region_error, exc_info=True)

        img.status = ImageStatusEnum.identified
        db.session.commit()
        match_single_image.delay(image_id)
    except Exception as e:
        logger.error(f"Single recognition failed for image {image_id}: {e}")
        img.status = ImageStatusEnum.error
        db.session.commit()
