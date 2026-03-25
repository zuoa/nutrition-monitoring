import logging
from datetime import date, datetime, timedelta
from celery_app import celery
from app import db
from app.models import (
    CapturedImage, ConsumptionRecord, MatchResult, DishRecognition, Dish,
    ImageStatusEnum, MatchStatusEnum,
)

logger = logging.getLogger(__name__)


@celery.task(name="app.tasks.matching.run_matching_for_date")
def run_matching_for_date(date_str: str):
    from flask import current_app
    cfg = current_app.config
    target_date = date.fromisoformat(date_str)
    tolerance_s = int(cfg.get("TIME_OFFSET_TOLERANCE", 1))
    price_tol = float(cfg.get("PRICE_TOLERANCE", 0.5))

    # Get all consumption records for this date
    day_start = datetime.combine(target_date, datetime.min.time())
    day_end = datetime.combine(target_date, datetime.max.time())

    records = ConsumptionRecord.query.filter(
        ConsumptionRecord.transaction_time >= day_start,
        ConsumptionRecord.transaction_time <= day_end,
    ).all()

    logger.info(f"Matching {len(records)} records for {target_date}")

    for record in records:
        _match_record(record, tolerance_s, price_tol, target_date)

    # Mark unmatched images
    matched_image_ids = db.session.query(MatchResult.image_id).filter(
        MatchResult.match_date == target_date,
        MatchResult.image_id.isnot(None),
    ).subquery()
    unmatched_images = CapturedImage.query.filter(
        CapturedImage.capture_date == target_date,
        CapturedImage.status == ImageStatusEnum.identified,
        ~CapturedImage.id.in_(matched_image_ids),
    ).all()

    for img in unmatched_images:
        existing = MatchResult.query.filter_by(
            image_id=img.id,
            status=MatchStatusEnum.unmatched_image,
        ).first()
        if not existing:
            m = MatchResult(
                image_id=img.id,
                status=MatchStatusEnum.unmatched_image,
                match_date=target_date,
            )
            db.session.add(m)

    db.session.commit()

    # Compute nutrition logs for matched students
    matched_students = db.session.query(MatchResult.student_id).filter(
        MatchResult.match_date == target_date,
        MatchResult.student_id.isnot(None),
        MatchResult.status.in_([MatchStatusEnum.matched]),
    ).distinct().all()

    for (student_id,) in matched_students:
        from app.tasks.nutrition import compute_nutrition_log
        compute_nutrition_log.delay(student_id, date_str)


def _match_record(record: ConsumptionRecord, tolerance_s: int, price_tol: float, target_date: date):
    tx_time = record.transaction_time
    lower = tx_time - timedelta(seconds=tolerance_s)
    upper = tx_time + timedelta(seconds=tolerance_s)

    candidates = CapturedImage.query.filter(
        CapturedImage.captured_at >= lower,
        CapturedImage.captured_at <= upper,
        CapturedImage.status == ImageStatusEnum.identified,
        CapturedImage.is_candidate.is_(False),
    ).all()

    if not candidates:
        # No image match
        existing = MatchResult.query.filter_by(
            consumption_record_id=record.id
        ).first()
        if not existing:
            m = MatchResult(
                consumption_record_id=record.id,
                student_id=record.student_id,
                status=MatchStatusEnum.unmatched_record,
                match_date=target_date,
            )
            db.session.add(m)
        return

    # Score each candidate by price proximity
    best_img = None
    best_diff = float("inf")
    best_status = MatchStatusEnum.time_matched_only

    for img in candidates:
        dish_total = _calc_dish_price(img.id)
        price_diff = abs(float(record.amount) - dish_total)

        if price_diff < best_diff:
            best_diff = price_diff
            best_img = img
            best_status = (
                MatchStatusEnum.matched if price_diff <= price_tol else MatchStatusEnum.time_matched_only
            )

    if best_img:
        existing = MatchResult.query.filter_by(
            consumption_record_id=record.id
        ).first()
        time_diff = abs((tx_time - best_img.captured_at).total_seconds())

        if existing:
            existing.image_id = best_img.id
            existing.status = best_status
            existing.time_diff_seconds = time_diff
            existing.price_diff = best_diff
            existing.student_id = record.student_id
        else:
            m = MatchResult(
                consumption_record_id=record.id,
                image_id=best_img.id,
                student_id=record.student_id,
                status=best_status,
                time_diff_seconds=time_diff,
                price_diff=best_diff,
                match_date=target_date,
            )
            db.session.add(m)

        if best_status == MatchStatusEnum.matched:
            best_img.status = ImageStatusEnum.matched

    db.session.commit()


def _calc_dish_price(image_id: int) -> float:
    recs = DishRecognition.query.filter_by(
        image_id=image_id, is_low_confidence=False
    ).all()
    total = 0.0
    for rec in recs:
        if rec.dish_id:
            dish = Dish.query.get(rec.dish_id)
            if dish and dish.price:
                total += float(dish.price)
    return total


@celery.task(name="app.tasks.matching.run_matching_for_batch")
def run_matching_for_batch(batch_id: str):
    from flask import current_app
    cfg = current_app.config
    tolerance_s = int(cfg.get("TIME_OFFSET_TOLERANCE", 1))
    price_tol = float(cfg.get("PRICE_TOLERANCE", 0.5))

    records = ConsumptionRecord.query.filter_by(import_batch=batch_id).all()
    dates_seen = set()
    for record in records:
        target_date = record.transaction_time.date()
        dates_seen.add(target_date)
        _match_record(record, tolerance_s, price_tol, target_date)

    for d in dates_seen:
        matched_students = db.session.query(MatchResult.student_id).filter(
            MatchResult.match_date == d,
            MatchResult.student_id.isnot(None),
        ).distinct().all()
        for (student_id,) in matched_students:
            from app.tasks.nutrition import compute_nutrition_log
            compute_nutrition_log.delay(student_id, d.isoformat())


@celery.task(name="app.tasks.matching.match_single_image")
def match_single_image(image_id: int):
    from flask import current_app
    cfg = current_app.config
    tolerance_s = int(cfg.get("TIME_OFFSET_TOLERANCE", 1))
    price_tol = float(cfg.get("PRICE_TOLERANCE", 0.5))

    img = CapturedImage.query.get(image_id)
    if not img:
        return

    tx_time = img.captured_at
    lower = tx_time - timedelta(seconds=tolerance_s)
    upper = tx_time + timedelta(seconds=tolerance_s)

    records = ConsumptionRecord.query.filter(
        ConsumptionRecord.transaction_time >= lower,
        ConsumptionRecord.transaction_time <= upper,
    ).all()

    for record in records:
        _match_record(record, tolerance_s, price_tol, img.capture_date)
