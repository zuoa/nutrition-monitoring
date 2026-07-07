import logging
from datetime import date, datetime, timedelta
from sqlalchemy import select
from celery_app import celery
from app import db
from app.models import (
    CapturedImage, ConsumptionRecord, MatchResult, DishRecognition, Dish,
    ImageStatusEnum, MatchStatusEnum, VideoSource, VideoSourceType,
)
from app.services.consumption_location_filter import (
    apply_enabled_transaction_location_filter,
    get_enabled_transaction_location_ids,
)

logger = logging.getLogger(__name__)

MATCHABLE_IMAGE_STATUSES = (
    ImageStatusEnum.pending,
    ImageStatusEnum.identified,
)
AUTOMATIC_CANDIDATE_IMAGE_STATUSES = (
    ImageStatusEnum.pending,
    ImageStatusEnum.identified,
    ImageStatusEnum.matched,
)
OCCUPYING_MATCH_STATUSES = (
    MatchStatusEnum.matched,
    MatchStatusEnum.time_matched_only,
    MatchStatusEnum.confirmed,
)

PRIMARY_MATCH_WINDOW_SECONDS = 1
FALLBACK_LOOKBACK_SECONDS = 3


@celery.task(name="app.tasks.matching.run_matching_for_date")
def run_matching_for_date(date_str: str):
    from flask import current_app
    cfg = current_app.config
    target_date = date.fromisoformat(date_str)
    tolerance_s = int(cfg.get("TIME_OFFSET_TOLERANCE", 1))
    price_tol = float(cfg.get("PRICE_TOLERANCE", 0.5))
    time_offset = float(cfg.get("TIME_OFFSET_CALIBRATION", 0.0))

    # Get all consumption records for this date
    day_start = datetime.combine(target_date, datetime.min.time())
    day_end = datetime.combine(target_date, datetime.max.time())

    records_query = ConsumptionRecord.query.filter(
        ConsumptionRecord.transaction_time >= day_start,
        ConsumptionRecord.transaction_time <= day_end,
        ConsumptionRecord.amount < 0,
    )
    records = apply_enabled_transaction_location_filter(records_query).order_by(
        ConsumptionRecord.transaction_time.asc(),
        ConsumptionRecord.id.asc(),
    ).all()

    logger.info(f"Matching {len(records)} records for {target_date}")

    channel_aliases = _configured_channel_aliases()
    for record in records:
        _match_record(record, tolerance_s, price_tol, target_date, channel_aliases=channel_aliases, time_offset=time_offset)

    # Mark unmatched images
    matched_image_ids = _occupied_image_ids_select(target_date)
    unmatched_images = CapturedImage.query.filter(
        CapturedImage.capture_date == target_date,
        CapturedImage.status.in_(MATCHABLE_IMAGE_STATUSES),
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


def _match_record(
    record: ConsumptionRecord,
    tolerance_s: int,
    price_tol: float,
    target_date: date,
    *,
    channel_aliases: dict[str, list[str]] | None = None,
    time_offset: float = 0.0,
):
    existing = MatchResult.query.filter_by(
        consumption_record_id=record.id
    ).order_by(MatchResult.id.asc()).first()
    if existing and (existing.is_manual or existing.status == MatchStatusEnum.confirmed):
        return

    # Apply the calibration offset to the consumption time so it lines up with
    # the video clock before searching/scoring candidates.
    aligned_tx = _aligned_consumption_time(record.transaction_time, time_offset)
    candidate_channel_ids = _resolve_record_channel_ids(record.channel_id, channel_aliases=channel_aliases)
    best_img = None
    best_diff = None
    windows = _matching_windows(aligned_tx)

    for lower, upper, include_upper in windows:
        candidates_query = CapturedImage.query.filter(
            CapturedImage.captured_at >= lower,
            CapturedImage.status.in_(AUTOMATIC_CANDIDATE_IMAGE_STATUSES),
            CapturedImage.is_candidate.is_(False),
            ~CapturedImage.id.in_(_occupied_image_ids_select(target_date, exclude_match_id=existing.id if existing else None)),
        )
        if include_upper:
            candidates_query = candidates_query.filter(CapturedImage.captured_at <= upper)
        else:
            candidates_query = candidates_query.filter(CapturedImage.captured_at < upper)
        if candidate_channel_ids:
            candidates_query = candidates_query.filter(CapturedImage.channel_id.in_(candidate_channel_ids))

        candidates = candidates_query.all()
        if not candidates:
            continue

        best_img, best_diff = _choose_best_candidate(record, candidates, aligned_tx)
        break

    if not best_img:
        # No image match
        if existing:
            previous_image_id = existing.image_id
            existing.image_id = None
            existing.status = MatchStatusEnum.unmatched_record
            existing.time_diff_seconds = None
            existing.price_diff = None
            existing.student_id = record.student_id
            existing.match_date = target_date
            _release_image_if_unoccupied(previous_image_id, target_date, exclude_match_id=existing.id)
        else:
            m = MatchResult(
                consumption_record_id=record.id,
                student_id=record.student_id,
                status=MatchStatusEnum.unmatched_record,
                match_date=target_date,
            )
            db.session.add(m)
        db.session.commit()
        return

    best_status = (
        MatchStatusEnum.matched if best_diff <= price_tol else MatchStatusEnum.time_matched_only
    )
    time_diff = abs((aligned_tx - best_img.captured_at).total_seconds())

    if existing:
        previous_image_id = existing.image_id if existing.image_id != best_img.id else None
        existing.image_id = best_img.id
        existing.status = best_status
        existing.time_diff_seconds = time_diff
        existing.price_diff = best_diff
        existing.student_id = record.student_id
        existing.match_date = target_date
        _release_image_if_unoccupied(previous_image_id, target_date, exclude_match_id=existing.id)
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

    _delete_unmatched_image_marker(best_img.id, target_date)
    if best_status == MatchStatusEnum.matched:
        best_img.status = ImageStatusEnum.matched

    db.session.commit()


def _matching_windows(tx_time: datetime):
    primary_delta = timedelta(seconds=PRIMARY_MATCH_WINDOW_SECONDS)
    windows = [(tx_time - primary_delta, tx_time + primary_delta, True)]
    for seconds in range(PRIMARY_MATCH_WINDOW_SECONDS + 1, FALLBACK_LOOKBACK_SECONDS + 1):
        windows.append((
            tx_time - timedelta(seconds=seconds),
            tx_time - timedelta(seconds=seconds - 1),
            False,
        ))
    return windows


def _aligned_consumption_time(tx_time: datetime, offset: float) -> datetime:
    """Apply the calibration offset (seconds) to a consumption transaction_time
    so it lines up with the video clock before matching."""
    return tx_time + timedelta(seconds=offset)


def _choose_best_candidate(
    record: ConsumptionRecord,
    candidates: list[CapturedImage],
    aligned_tx: datetime,
) -> tuple[CapturedImage, float]:
    scored = []
    record_amount = abs(float(record.amount))
    for img in candidates:
        dish_total = _calc_dish_price(img.id)
        price_diff = abs(record_amount - dish_total)
        time_diff = abs((aligned_tx - img.captured_at).total_seconds())
        scored.append((time_diff, price_diff, img.id, img, price_diff))

    _, _, _, best_img, best_diff = min(scored, key=lambda item: (item[0], item[1], item[2]))
    return best_img, best_diff


def _occupied_image_ids_select(target_date: date, *, exclude_match_id: int | None = None):
    stmt = select(MatchResult.image_id).join(
        ConsumptionRecord,
        MatchResult.consumption_record_id == ConsumptionRecord.id,
    ).where(
        MatchResult.match_date == target_date,
        MatchResult.image_id.isnot(None),
        MatchResult.consumption_record_id.isnot(None),
        MatchResult.status.in_(OCCUPYING_MATCH_STATUSES),
        ConsumptionRecord.amount < 0,
    )
    location_ids = get_enabled_transaction_location_ids()
    if location_ids:
        stmt = stmt.where(ConsumptionRecord.channel_id.in_(location_ids))
    if exclude_match_id:
        stmt = stmt.where(MatchResult.id != exclude_match_id)
    return stmt


def _delete_unmatched_image_marker(image_id: int | None, target_date: date):
    if not image_id:
        return
    MatchResult.query.filter(
        MatchResult.image_id == image_id,
        MatchResult.match_date == target_date,
        MatchResult.status == MatchStatusEnum.unmatched_image,
    ).delete(synchronize_session=False)


def _release_image_if_unoccupied(image_id: int | None, target_date: date, *, exclude_match_id: int | None = None):
    if not image_id:
        return

    still_occupied = db.session.query(MatchResult.id).join(
        ConsumptionRecord,
        MatchResult.consumption_record_id == ConsumptionRecord.id,
    ).filter(
        MatchResult.image_id == image_id,
        MatchResult.match_date == target_date,
        MatchResult.consumption_record_id.isnot(None),
        MatchResult.status.in_(OCCUPYING_MATCH_STATUSES),
        ConsumptionRecord.amount < 0,
    )
    still_occupied = apply_enabled_transaction_location_filter(still_occupied)
    if exclude_match_id:
        still_occupied = still_occupied.filter(MatchResult.id != exclude_match_id)
    if still_occupied.first():
        return

    image = db.session.get(CapturedImage, image_id)
    if image and image.status == ImageStatusEnum.matched:
        image.status = ImageStatusEnum.identified


def _calc_dish_price(image_id: int) -> float:
    recs = DishRecognition.query.filter_by(
        image_id=image_id, is_low_confidence=False
    ).all()
    total = 0.0
    for rec in recs:
        if rec.dish_id:
            dish = db.session.get(Dish, rec.dish_id)
            if dish and dish.price:
                total += float(dish.price)
    return total


@celery.task(name="app.tasks.matching.run_matching_for_batch")
def run_matching_for_batch(batch_id: str):
    from flask import current_app
    cfg = current_app.config
    tolerance_s = int(cfg.get("TIME_OFFSET_TOLERANCE", 1))
    price_tol = float(cfg.get("PRICE_TOLERANCE", 0.5))
    time_offset = float(cfg.get("TIME_OFFSET_CALIBRATION", 0.0))

    records_query = ConsumptionRecord.query.filter(
        ConsumptionRecord.import_batch == batch_id,
        ConsumptionRecord.amount < 0,
    )
    records = apply_enabled_transaction_location_filter(records_query).order_by(
        ConsumptionRecord.transaction_time.asc(),
        ConsumptionRecord.id.asc(),
    ).all()
    dates_seen = set()
    channel_aliases = _configured_channel_aliases()
    for record in records:
        target_date = record.transaction_time.date()
        dates_seen.add(target_date)
        _match_record(record, tolerance_s, price_tol, target_date, channel_aliases=channel_aliases, time_offset=time_offset)

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
    price_tol = float(cfg.get("PRICE_TOLERANCE", 0.5))
    time_offset = float(cfg.get("TIME_OFFSET_CALIBRATION", 0.0))

    img = db.session.get(CapturedImage, image_id)
    if not img:
        return

    # Reverse search: find consumption records whose transaction_time could
    # align with this image. Since _match_record matches on
    # aligned_tx = transaction_time + offset, the candidate transaction_times
    # sit around captured_at - offset.
    search_center = img.captured_at - timedelta(seconds=time_offset)
    lower = search_center - timedelta(seconds=PRIMARY_MATCH_WINDOW_SECONDS)
    upper = search_center + timedelta(seconds=FALLBACK_LOOKBACK_SECONDS)

    records_query = ConsumptionRecord.query.filter(
        ConsumptionRecord.transaction_time >= lower,
        ConsumptionRecord.transaction_time <= upper,
        ConsumptionRecord.amount < 0,
    )
    records = apply_enabled_transaction_location_filter(records_query).all()

    channel_aliases = _configured_channel_aliases()
    for record in records:
        _match_record(record, PRIMARY_MATCH_WINDOW_SECONDS, price_tol, img.capture_date, channel_aliases=channel_aliases, time_offset=time_offset)


def _resolve_record_channel_ids(value: object, *, channel_aliases: dict[str, list[str]] | None = None) -> list[str]:
    raw_text = normalize_location_text(value)
    if not raw_text:
        return []

    candidates = [raw_text]
    aliases = channel_aliases if channel_aliases is not None else _configured_channel_aliases()
    candidates.extend(aliases.get(raw_text, []))

    result = []
    seen = set()
    for item in candidates:
        channel_id = str(item or "").strip()
        if not channel_id or channel_id in seen:
            continue
        seen.add(channel_id)
        result.append(channel_id)
    return result


def _configured_channel_aliases() -> dict[str, list[str]]:
    aliases: dict[str, list[str]] = {}
    for source in VideoSource.query.all():
        config = source.config_json or {}
        if source.source_type == VideoSourceType.hikvision_camera.value:
            for camera in config.get("cameras", []):
                if not isinstance(camera, dict):
                    continue
                _add_channel_alias(
                    aliases,
                    camera.get("location_alias"),
                    camera.get("channel_id"),
                )
        else:
            channel_aliases = config.get("channel_location_aliases")
            if not isinstance(channel_aliases, dict):
                continue
            for channel_id, alias in channel_aliases.items():
                _add_channel_alias(aliases, alias, channel_id)
    return aliases


def _add_channel_alias(aliases: dict[str, list[str]], alias: object, channel_id: object):
    alias_text = normalize_location_text(alias)
    normalized_channel_id = str(channel_id or "").strip()
    if not alias_text or not normalized_channel_id:
        return
    aliases.setdefault(alias_text, [])
    if normalized_channel_id not in aliases[alias_text]:
        aliases[alias_text].append(normalized_channel_id)


def normalize_location_text(value: object) -> str:
    return " ".join(str(value or "").strip().split())
