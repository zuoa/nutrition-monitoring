import logging
from datetime import date, datetime
from celery_app import celery
from app import db
from app.models import CapturedImage, DishRecognition, DailyMenu, Dish, TaskLog, ImageStatusEnum

logger = logging.getLogger(__name__)

LOW_CONFIDENCE_THRESHOLD = 0.6


@celery.task(name="app.tasks.recognition.run_recognition_batch", bind=True)
def run_recognition_batch(self, date_str: str):
    from flask import current_app
    from app.services.dish_recognition import DishRecognitionService

    cfg = current_app.config
    target_date = date.fromisoformat(date_str)
    recognizer = DishRecognitionService(cfg)

    task_log = TaskLog(task_type="ai_recognition", task_date=target_date)
    db.session.add(task_log)
    db.session.commit()

    # Get candidate dishes for the day with descriptions
    menu = DailyMenu.query.filter_by(menu_date=target_date).first()
    if menu and not menu.is_default and menu.dish_ids:
        dishes = Dish.query.filter(
            Dish.id.in_(menu.dish_ids), Dish.is_active.is_(True)
        ).all()
    else:
        dishes = Dish.query.filter_by(is_active=True).all()
    candidate_dishes = [{"id": d.id, "name": d.name, "description": d.description or ""} for d in dishes]
    dish_name_map = {d.name.lower(): d for d in dishes}

    # Get pending images
    images = CapturedImage.query.filter_by(
        capture_date=target_date,
        status=ImageStatusEnum.pending,
    ).filter(CapturedImage.is_candidate.is_(False)).all()

    task_log.total_count = len(images)
    db.session.commit()

    success = low_conf = errors = 0

    for img in images:
        try:
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
                    raw_response=result.get("raw_response"),
                )
                db.session.add(rec)
                if is_low:
                    low_conf += 1

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
    menu = DailyMenu.query.filter_by(menu_date=img.capture_date).first()
    if menu and not menu.is_default and menu.dish_ids:
        dishes = Dish.query.filter(Dish.id.in_(menu.dish_ids)).all()
    else:
        dishes = Dish.query.filter_by(is_active=True).all()

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
                raw_response=result.get("raw_response"),
            )
            db.session.add(rec)

        img.status = ImageStatusEnum.identified
        db.session.commit()
        match_single_image.delay(image_id)
    except Exception as e:
        logger.error(f"Single recognition failed for image {image_id}: {e}")
        img.status = ImageStatusEnum.error
        db.session.commit()
