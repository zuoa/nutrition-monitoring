import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import func, or_, select
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
    from app.services.date_matching import request_date_matching
    if request_date_matching(date.fromisoformat(date_str)):
        continue_date_matching.delay(date_str)
    return {"date": date_str, "scheduled": True}


@celery.task(name="app.tasks.matching.continue_date_matching")
def continue_date_matching(date_str: str):
    from app.services.date_matching import advance_date_matching
    try:
        pending = advance_date_matching(date.fromisoformat(date_str))
    except Exception:
        db.session.rollback()
        logger.exception("Date matching step failed for %s; durable progress will be retried", date_str)
        raise
    if pending:
        continue_date_matching.delay(date_str)
    return {"date": date_str, "completed": not pending}


@celery.task(name="app.tasks.matching.recover_matching_runs")
def recover_matching_runs():
    from app.models import MatchingRun
    pending = MatchingRun.query.filter(or_(
        MatchingRun.requested > MatchingRun.completed,
        MatchingRun.phase != "idle",
    )).all()
    for run in pending:
        continue_date_matching.delay(run.match_date.isoformat())
    return {"scheduled": len(pending)}


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
    # Keep price_tol in the signature for callers that still pass the legacy
    # setting. Automatic matching is intentionally exact-price only: even a
    # small difference must not reserve an image from a later, correct record.
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
    record_amount = _money_value(abs(record.amount))
    dish_totals = _calc_dish_prices([image.id for image in all_candidates])
    for round_index, (lower, upper, include_upper) in enumerate(windows):
        candidates = [
            image
            for image in all_candidates
            if image.captured_at >= lower
            and (image.captured_at <= upper if include_upper else image.captured_at < upper)
            and dish_totals.get(image.id, Decimal("0.00")) == record_amount
        ]
        if candidates:
            best_img = _choose_best_candidate(candidates, aligned_tx)
            best_diff = 0.0
            break

    if not best_img:
        # No image match
        if existing:
            previous_image_id = existing.image_id
            existing.image_id = None
            existing.captured_at = None
            existing.status = MatchStatusEnum.unmatched_record
            existing.time_diff_seconds = None
            existing.applied_time_offset_seconds = None
            existing.match_round = None
            existing.match_window_seconds = None
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

    best_status = MatchStatusEnum.matched
    time_diff = abs((aligned_tx - best_img.captured_at).total_seconds())

    if existing:
        previous_image_id = existing.image_id if existing.image_id != best_img.id else None
        existing.image_id = best_img.id
        existing.captured_at = best_img.captured_at
        existing.status = best_status
        existing.time_diff_seconds = time_diff
        existing.applied_time_offset_seconds = time_offset
        existing.match_round = round_index + 1
        existing.match_window_seconds = stages[round_index]
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
            applied_time_offset_seconds=time_offset,
            match_round=round_index + 1,
            match_window_seconds=stages[round_index],
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
    candidates: list[CapturedImage],
    aligned_tx: datetime,
) -> CapturedImage:
    return min(
        candidates,
        key=lambda image: (
            abs((aligned_tx - image.captured_at).total_seconds()),
            image.id,
        ),
    )


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


def _money_value(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _calc_dish_prices(image_ids: list[int]) -> dict[int, Decimal]:
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
    return {image_id: _money_value(total) for image_id, total in rows}


def _calc_dish_price(image_id: int) -> float:
    return float(_calc_dish_prices([image_id]).get(image_id, Decimal("0.00")))


@celery.task(name="app.tasks.matching.run_matching_for_batch")
def run_matching_for_batch(
    batch_id: str,
    cursor_time: str | None = None,
    cursor_id: int | None = None,
    dates_seen: list[str] | None = None,
    processed_count: int = 0,
):
    # Keep legacy continuation arguments accepted during worker upgrades. A
    # batch now schedules complete dates, never a private allocation pass.
    rows = apply_enabled_transaction_location_filter(ConsumptionRecord.query.filter(
        ConsumptionRecord.import_batch == batch_id,
        ConsumptionRecord.amount < 0,
    )).with_entities(func.date(ConsumptionRecord.transaction_time)).distinct().all()
    dates = {str(value) for (value,) in rows} | set(dates_seen or [])
    for date_str in sorted(dates):
        run_matching_for_date(date_str)
    return {"batch_id": batch_id, "dates": sorted(dates), "scheduled": True}


def match_single_image_now(image_id: int):
    """Queue date-wide matching; retained name for existing API callers."""
    from flask import current_app
    from app.models import TimeCalibrationSample
    from app.services.runtime_config import get_effective_config
    from app.services.date_matching import _timestamp

    img = db.session.get(CapturedImage, image_id)
    if not img:
        return
    cfg = get_effective_config(current_app.config)
    window = max_match_window_seconds(_configured_match_window_stages(cfg))
    # Bound the reverse search using ALL possible calibration offsets. Using
    # only the sample at image time misses records near an offset transition.
    min_offset, max_offset = db.session.query(
        func.min(TimeCalibrationSample.offset_seconds), func.max(TimeCalibrationSample.offset_seconds),
    ).one()
    offsets = [float(cfg.get("TIME_OFFSET_CALIBRATION", 0.0))]
    offsets.extend(-float(value) for value in (min_offset, max_offset) if value is not None)
    captured = _timestamp(img.captured_at)
    lower = (captured - timedelta(seconds=max(offsets) + window)).replace(tzinfo=timezone.utc)
    upper = (captured - timedelta(seconds=min(offsets) - window)).replace(tzinfo=timezone.utc)
    records = apply_enabled_transaction_location_filter(ConsumptionRecord.query.filter(
        ConsumptionRecord.transaction_time.between(lower, upper),
        ConsumptionRecord.amount < 0,
    ), cfg).all()
    resolver = _build_offset_resolver(cfg, lower, upper)
    dates = {img.capture_date}
    for record in records:
        aligned = _timestamp(record.transaction_time) + timedelta(seconds=resolver.offset_for(record.transaction_time))
        if abs((aligned - captured).total_seconds()) <= window:
            dates.add(record.transaction_time.date())
    # A recognition edit can invalidate an old match outside the new window.
    dates.update(match.match_date for match in MatchResult.query.filter_by(image_id=image_id).all() if match.match_date)
    for target_date in sorted(dates):
        run_matching_for_date(target_date.isoformat())
    return {"dates": sorted(day.isoformat() for day in dates), "scheduled": True}


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
