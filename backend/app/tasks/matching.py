import logging
import time
from datetime import date, datetime, timedelta
from sqlalchemy import and_, func, or_, select
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
from app.services.match_windows import (
    matching_windows,
    max_match_window_seconds,
    normalize_match_window_stages,
)
from app.services.time_calibration import TimeOffsetResolver, resolve_calibration_timezone

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

DEFAULT_MATCHING_BATCH_CHUNK_SIZE = 200
DEFAULT_MATCHING_BATCH_TIME_BUDGET_SECONDS = 240


def _build_offset_resolver(cfg, start: datetime | None, end: datetime | None) -> TimeOffsetResolver:
    """Per-record clock-offset lookup: same-minute sample, then nearest sample,
    then the manual TIME_OFFSET_CALIBRATION value when nothing is sampled."""
    return TimeOffsetResolver.for_time_range(
        start,
        end,
        fallback_offset=float(cfg.get("TIME_OFFSET_CALIBRATION", 0.0)),
        tz=resolve_calibration_timezone(cfg),
    )


@celery.task(name="app.tasks.matching.run_matching_for_date")
def run_matching_for_date(date_str: str):
    from flask import current_app
    cfg = current_app.config
    target_date = date.fromisoformat(date_str)
    window_stages = _configured_match_window_stages(cfg)
    price_tol = float(cfg.get("PRICE_TOLERANCE", 0.5))

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
    offset_resolver = _build_offset_resolver(cfg, day_start, day_end)
    for record in records:
        _match_record(
            record,
            price_tol,
            target_date,
            channel_aliases=channel_aliases,
            offset_resolver=offset_resolver,
            window_stages=window_stages,
        )

    # Mark unmatched images
    matched_image_ids = _occupied_image_ids_select(target_date)
    standby_image_ids = db.session.query(CapturedImage.id).filter(
        CapturedImage.capture_date == target_date,
        CapturedImage.is_candidate.is_(True),
    )
    MatchResult.query.filter(
        MatchResult.match_date == target_date,
        MatchResult.status == MatchStatusEnum.unmatched_image,
        MatchResult.image_id.in_(standby_image_ids),
    ).delete(synchronize_session=False)
    unmatched_images = CapturedImage.query.filter(
        CapturedImage.capture_date == target_date,
        CapturedImage.status.in_(MATCHABLE_IMAGE_STATUSES),
        CapturedImage.is_candidate.is_(False),
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
                captured_at=img.captured_at,
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
    price_tol: float,
    target_date: date,
    *,
    channel_aliases: dict[str, list[str]] | None = None,
    time_offset: float = 0.0,
    offset_resolver: TimeOffsetResolver | None = None,
    window_stages=None,
    commit: bool = True,
):
    existing = MatchResult.query.filter_by(
        consumption_record_id=record.id
    ).order_by(MatchResult.id.asc()).first()
    if existing and (existing.is_manual or existing.status == MatchStatusEnum.confirmed):
        return

    # Resolve the clock offset for this record's own moment: the calibration
    # sample from that minute, else the nearest sample, else the static
    # time_offset/manual fallback passed by the caller.
    if offset_resolver is not None:
        time_offset = offset_resolver.offset_for(record.transaction_time)

    # Apply the calibration offset to the consumption time so it lines up with
    # the video clock before searching/scoring candidates.
    aligned_tx = _aligned_consumption_time(record.transaction_time, time_offset)
    candidate_channel_ids = _resolve_record_channel_ids(record.channel_id, channel_aliases=channel_aliases)
    best_img = None
    best_diff = None
    stages = normalize_match_window_stages(window_stages)
    windows = matching_windows(aligned_tx, stages)
    search_lower = min(lower for lower, _, _ in windows)
    search_upper = max(upper for _, upper, _ in windows)
    candidates_query = CapturedImage.query.filter(
        CapturedImage.captured_at >= search_lower,
        CapturedImage.captured_at <= search_upper,
        CapturedImage.status.in_(AUTOMATIC_CANDIDATE_IMAGE_STATUSES),
        CapturedImage.is_candidate.is_(False),
        ~CapturedImage.id.in_(_occupied_image_ids_select(target_date, exclude_match_id=existing.id if existing else None)),
    )
    if candidate_channel_ids:
        candidates_query = candidates_query.filter(CapturedImage.channel_id.in_(candidate_channel_ids))

    all_candidates = candidates_query.all()
    for lower, upper, include_upper in windows:
        candidates = [
            image
            for image in all_candidates
            if image.captured_at >= lower
            and (image.captured_at <= upper if include_upper else image.captured_at < upper)
        ]
        if candidates:
            best_img, best_diff = _choose_best_candidate(record, candidates, aligned_tx)
            break

    if not best_img:
        # No image match
        if existing:
            previous_image_id = existing.image_id
            existing.image_id = None
            existing.captured_at = None
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
        _finish_match_transaction(commit)
        return

    best_status = (
        MatchStatusEnum.matched if best_diff <= price_tol else MatchStatusEnum.time_matched_only
    )
    time_diff = abs((aligned_tx - best_img.captured_at).total_seconds())

    if existing:
        previous_image_id = existing.image_id if existing.image_id != best_img.id else None
        existing.image_id = best_img.id
        existing.captured_at = best_img.captured_at
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
            captured_at=best_img.captured_at,
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

    _finish_match_transaction(commit)


def _finish_match_transaction(commit: bool):
    if commit:
        db.session.commit()
    else:
        # Make the result visible to subsequent matching queries in the same
        # chunk without expiring every ORM object in the session.
        db.session.flush()


def _configured_match_window_stages(cfg) -> tuple[int, ...]:
    return normalize_match_window_stages(cfg.get("TIME_MATCH_WINDOW_STAGES"))


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
    dish_totals = _calc_dish_prices([image.id for image in candidates])
    for img in candidates:
        dish_total = dish_totals.get(img.id, 0.0)
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


def _calc_dish_prices(image_ids: list[int]) -> dict[int, float]:
    if not image_ids:
        return {}

    rows = db.session.query(
        DishRecognition.image_id,
        func.coalesce(func.sum(Dish.price), 0),
    ).join(
        Dish,
        DishRecognition.dish_id == Dish.id,
    ).filter(
        DishRecognition.image_id.in_(image_ids),
        DishRecognition.is_low_confidence.is_(False),
    ).group_by(
        DishRecognition.image_id,
    ).all()
    return {image_id: float(total or 0) for image_id, total in rows}


def _calc_dish_price(image_id: int) -> float:
    return _calc_dish_prices([image_id]).get(image_id, 0.0)


@celery.task(name="app.tasks.matching.run_matching_for_batch")
def run_matching_for_batch(
    batch_id: str,
    cursor_time: str | None = None,
    cursor_id: int | None = None,
    dates_seen: list[str] | None = None,
    processed_count: int = 0,
):
    from flask import current_app
    cfg = current_app.config
    window_stages = _configured_match_window_stages(cfg)
    price_tol = float(cfg.get("PRICE_TOLERANCE", 0.5))
    chunk_size = max(1, int(cfg.get("MATCHING_BATCH_CHUNK_SIZE", DEFAULT_MATCHING_BATCH_CHUNK_SIZE)))
    time_budget_s = max(
        1,
        int(cfg.get("MATCHING_BATCH_TIME_BUDGET_SECONDS", DEFAULT_MATCHING_BATCH_TIME_BUDGET_SECONDS)),
    )
    chunk_started = time.monotonic()

    records_query = ConsumptionRecord.query.filter(
        ConsumptionRecord.import_batch == batch_id,
        ConsumptionRecord.amount < 0,
    )
    parsed_cursor_time = datetime.fromisoformat(cursor_time) if cursor_time else None
    if parsed_cursor_time is not None:
        records_query = records_query.filter(or_(
            ConsumptionRecord.transaction_time > parsed_cursor_time,
            and_(
                ConsumptionRecord.transaction_time == parsed_cursor_time,
                ConsumptionRecord.id > int(cursor_id or 0),
            ),
        ))
    records = apply_enabled_transaction_location_filter(records_query).order_by(
        ConsumptionRecord.transaction_time.asc(),
        ConsumptionRecord.id.asc(),
    ).limit(chunk_size).all()
    accumulated_dates = {
        date.fromisoformat(value)
        for value in (dates_seen or [])
    }

    if not records:
        _enqueue_nutrition_for_dates(accumulated_dates)
        logger.info(
            "Completed matching batch %s: %d records across %d dates",
            batch_id,
            processed_count,
            len(accumulated_dates),
        )
        return {
            "batch_id": batch_id,
            "processed": processed_count,
            "completed": True,
        }

    channel_aliases = _configured_channel_aliases()
    # Records are time-ordered, so the chunk's first/last transaction times
    # bound the calibration window for this chunk.
    offset_resolver = _build_offset_resolver(
        cfg,
        records[0].transaction_time,
        records[-1].transaction_time,
    )
    processed_records = []
    try:
        for record in records:
            target_date = record.transaction_time.date()
            accumulated_dates.add(target_date)
            _match_record(
                record,
                price_tol,
                target_date,
                channel_aliases=channel_aliases,
                offset_resolver=offset_resolver,
                window_stages=window_stages,
                commit=False,
            )
            processed_records.append(record)
            if time.monotonic() - chunk_started >= time_budget_s:
                break

        last_record = processed_records[-1]
        next_cursor_time = last_record.transaction_time.isoformat()
        next_cursor_id = last_record.id
        chunk_count = len(processed_records)
        total_processed = processed_count + chunk_count
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception(
            "Failed matching batch %s after cursor (%s, %s)",
            batch_id,
            cursor_time,
            cursor_id,
        )
        raise

    serialized_dates = sorted(value.isoformat() for value in accumulated_dates)
    has_more = chunk_count < len(records) or len(records) == chunk_size
    if has_more:
        run_matching_for_batch.delay(
            batch_id,
            next_cursor_time,
            next_cursor_id,
            serialized_dates,
            total_processed,
        )
        logger.info(
            "Matched %d records for batch %s; scheduled continuation after record %s",
            chunk_count,
            batch_id,
            next_cursor_id,
        )
        return {
            "batch_id": batch_id,
            "processed": total_processed,
            "completed": False,
            "continuation_scheduled": True,
        }

    _enqueue_nutrition_for_dates(accumulated_dates)
    logger.info(
        "Completed matching batch %s: %d records across %d dates",
        batch_id,
        total_processed,
        len(accumulated_dates),
    )
    return {
        "batch_id": batch_id,
        "processed": total_processed,
        "completed": True,
    }


def _enqueue_nutrition_for_dates(dates_seen: set[date]):
    for target_date in sorted(dates_seen):
        matched_students = db.session.query(MatchResult.student_id).filter(
            MatchResult.match_date == target_date,
            MatchResult.student_id.isnot(None),
        ).distinct().all()
        for (student_id,) in matched_students:
            from app.tasks.nutrition import compute_nutrition_log
            compute_nutrition_log.delay(student_id, target_date.isoformat())


def match_single_image_now(image_id: int):
    """Run the single-image matching pass immediately in the current process."""
    from flask import current_app
    cfg = current_app.config
    window_stages = _configured_match_window_stages(cfg)
    price_tol = float(cfg.get("PRICE_TOLERANCE", 0.5))

    img = db.session.get(CapturedImage, image_id)
    if not img:
        return

    # Reverse search: find consumption records whose transaction_time could
    # align with this image. Since _match_record matches on
    # aligned_tx = transaction_time + offset, the candidate transaction_times
    # sit around captured_at - offset. Resolve the offset at the image's own
    # moment (same-minute sample, then nearest, then manual fallback).
    offset_resolver = _build_offset_resolver(cfg, img.captured_at, img.captured_at)
    time_offset = offset_resolver.offset_for(img.captured_at)
    search_center = img.captured_at - timedelta(seconds=time_offset)
    max_window = max_match_window_seconds(window_stages)
    lower = search_center - timedelta(seconds=max_window)
    upper = search_center + timedelta(seconds=max_window)

    records_query = ConsumptionRecord.query.filter(
        ConsumptionRecord.transaction_time >= lower,
        ConsumptionRecord.transaction_time <= upper,
        ConsumptionRecord.amount < 0,
    )
    records = apply_enabled_transaction_location_filter(records_query).all()

    channel_aliases = _configured_channel_aliases()
    try:
        for record in records:
            _match_record(
                record,
                price_tol,
                img.capture_date,
                channel_aliases=channel_aliases,
                offset_resolver=offset_resolver,
                window_stages=window_stages,
                commit=False,
            )
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


@celery.task(name="app.tasks.matching.match_single_image")
def match_single_image(image_id: int):
    return match_single_image_now(image_id)


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
