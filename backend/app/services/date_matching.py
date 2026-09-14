"""Durable date-wide matching: collect a full round, then allocate nearest pairs.

Only publication changes live matches. Each advance is a bounded transaction
locked on the date's queue row, so duplicate deliveries safely resume progress.
"""

from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta, timezone
import time
import logging

from flask import current_app
from sqlalchemy import or_, text

from app import db
from app.models import CapturedImage, ConsumptionRecord, MatchResult, MatchStatusEnum, ImageStatusEnum, MatchingRun, MatchingCandidate
from app.services.consumption_location_filter import apply_enabled_transaction_location_filter
from app.services.match_windows import normalize_match_window_stages
from app.services.runtime_config import get_effective_config

logger = logging.getLogger(__name__)


def _timestamp(value):
    # PostgreSQL returns aware UTC timestamps; SQLite fixtures return naive ones.
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def _ensure_run(target_date):
    dialect = db.engine.dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    db.session.execute(insert(MatchingRun).values(
        match_date=target_date, requested=0, completed=0, generation=0,
        phase="idle", stage=0, cursor=0, assignments={}, notification_student_ids=[],
        updated_at=datetime.now(timezone.utc),
    ).on_conflict_do_nothing(index_elements=["match_date"]))
    return MatchingRun.query.filter_by(match_date=target_date).populate_existing().with_for_update().one()


def request_date_matching(target_date):
    """Persist before dispatch; the periodic recovery task covers broker failure."""
    run = _ensure_run(target_date)
    needs_dispatch = run.phase == "idle" and run.requested <= run.completed
    run.requested += 1
    run.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return needs_dispatch


def _input_snapshot(target_date, cfg, offsets=None):
    from app.tasks.matching import _build_offset_resolver, _configured_channel_aliases, _resolve_record_channel_ids, _calc_dish_prices, _money_value

    start = datetime.combine(target_date, datetime.min.time())
    end = start + timedelta(days=1)
    records = apply_enabled_transaction_location_filter(ConsumptionRecord.query.filter(
        ConsumptionRecord.transaction_time >= start,
        ConsumptionRecord.transaction_time < end,
        ConsumptionRecord.amount < 0,
    ), cfg).order_by(ConsumptionRecord.id).all()
    aliases = _configured_channel_aliases()
    resolver = _build_offset_resolver(cfg, start, end) if offsets is None else None
    record_data = []
    for record in records:
        key = str(record.id)
        offset = resolver.offset_for(record.transaction_time) if resolver else offsets.get(key, 0.0)
        tx = _timestamp(record.transaction_time)
        record_data.append({
            "id": record.id, "time": tx.isoformat(), "offset": offset,
            "aligned": (tx + timedelta(seconds=offset)).isoformat(),
            "amount": str(_money_value(abs(record.amount))), "student_id": record.student_id,
            "channels": _resolve_record_channel_ids(record.channel_id, channel_aliases=aliases),
        })
    stages = normalize_match_window_stages(cfg.get("TIME_MATCH_WINDOW_STAGES"))
    aligned = [datetime.fromisoformat(record["aligned"]) for record in record_data]
    lower = min(aligned) - timedelta(seconds=stages[-1]) if aligned else start
    upper = max(aligned) + timedelta(seconds=stages[-1]) if aligned else end
    # Include the date's images for unmatched markers, plus calibrated windows
    # that may cross midnight. Occupancy is checked across ALL dates.
    images = CapturedImage.query.filter(or_(
        CapturedImage.capture_date == target_date,
        CapturedImage.captured_at.between(lower.replace(tzinfo=timezone.utc), upper.replace(tzinfo=timezone.utc)),
    )).order_by(CapturedImage.id).all()
    prices = _calc_dish_prices([image.id for image in images])
    image_data = [{
        "id": image.id, "time": _timestamp(image.captured_at).isoformat(),
        "date": image.capture_date.isoformat(), "channel": image.channel_id,
        "status": image.status.value, "candidate": bool(image.is_candidate),
        "price": str(prices.get(image.id, _money_value(0))),
    } for image in images]
    record_ids = [record.id for record in records]
    all_date_ids = {record_id for (record_id,) in db.session.query(ConsumptionRecord.id).filter(
        ConsumptionRecord.transaction_time >= start,
        ConsumptionRecord.transaction_time < end,
    ).all()}
    excluded_record_ids = sorted(all_date_ids - set(record_ids))
    image_ids = [image.id for image in images]
    matches = MatchResult.query.filter(or_(
        MatchResult.match_date == target_date,
        MatchResult.consumption_record_id.in_(all_date_ids),
        MatchResult.image_id.in_(image_ids),
    )).order_by(MatchResult.id).all()
    match_data = [{
        "id": match.id, "record_id": match.consumption_record_id,
        "image_id": match.image_id, "manual": bool(match.is_manual),
        "status": match.status.value, "student_id": match.student_id,
        "date": match.match_date.isoformat() if match.match_date else None,
    } for match in matches]
    return {"records": record_data, "excluded_record_ids": excluded_record_ids, "images": image_data, "matches": match_data}


def _begin(run):
    cfg = get_effective_config(current_app.config)
    stages = list(normalize_match_window_stages(cfg.get("TIME_MATCH_WINDOW_STAGES")))
    # Only the relevant runtime settings are persisted, never credentials.
    from app.services.consumption_location_filter import get_enabled_transaction_location_ids
    settings = {
        "TIME_MATCH_WINDOW_STAGES": stages,
        "TIME_OFFSET_CALIBRATION": cfg.get("TIME_OFFSET_CALIBRATION", 0.0),
        "APP_TIMEZONE": cfg.get("APP_TIMEZONE", "Asia/Shanghai"),
        "VIDEO_TIMEZONE": cfg.get("VIDEO_TIMEZONE", "Asia/Shanghai"),
        "CONSUMPTION_ENABLED_TRANSACTION_LOCATION_IDS": get_enabled_transaction_location_ids(cfg),
    }
    run.snapshot = {"settings": settings, "data": _input_snapshot(run.match_date, settings)}
    run.generation = run.requested
    run.stage = 0
    run.cursor = 0
    run.assignments = {}
    run.phase = "collect"
    MatchingCandidate.query.filter_by(match_date=run.match_date).delete()
    logger.info("Date matching %s generation %s: %d records, windows %s", run.match_date, run.generation, len(run.snapshot["data"]["records"]), stages)


def _reserved(snapshot):
    record_ids = {record["id"] for record in snapshot["records"]} | set(snapshot["excluded_record_ids"])
    protected = set()
    occupied = set()
    for match in snapshot["matches"]:
        manual = match["manual"] or match["status"] == "confirmed"
        if manual and match["record_id"] is not None:
            protected.add(match["record_id"])
        if match["image_id"] is not None and (
            manual or (match["record_id"] not in record_ids and match["status"] in ("matched", "time_matched_only"))
        ):
            occupied.add(match["image_id"])
    return protected, occupied


def _collect(run, chunk_size, deadline):
    data = run.snapshot["data"]
    protected, occupied = _reserved(data)
    occupied.update(item["image_id"] for item in run.assignments.values())
    remaining = [r for r in data["records"] if r["id"] > run.cursor]
    # Index by exact price and time; do not scan the entire day's images for
    # every record. Channel filtering happens within the bounded window.
    by_price = {}
    for image in data["images"]:
        if image["id"] not in occupied and not image["candidate"] and image["status"] in ("pending", "identified", "matched"):
            by_price.setdefault(image["price"], []).append(image)
    indexes = {}
    for price, images in by_price.items():
        images.sort(key=lambda image: (image["time"], image["id"]))
        indexes[price] = ([datetime.fromisoformat(image["time"]) for image in images], images)
    window = run.snapshot["settings"]["TIME_MATCH_WINDOW_STAGES"][run.stage]
    count = 0
    for record in remaining[:chunk_size]:
        if record["id"] not in protected and str(record["id"]) not in run.assignments:
            times, images = indexes.get(record["amount"], ([], []))
            aligned = datetime.fromisoformat(record["aligned"])
            lower = bisect_left(times, aligned - timedelta(seconds=window))
            upper = bisect_right(times, aligned + timedelta(seconds=window))
            for image in images[lower:upper]:
                if record["channels"] and image["channel"] not in record["channels"]:
                    continue
                db.session.add(MatchingCandidate(
                    match_date=run.match_date, record_id=record["id"], image_id=image["id"],
                    time_diff_seconds=abs((aligned - datetime.fromisoformat(image["time"])).total_seconds()),
                ))
        run.cursor = record["id"]
        count += 1
        if time.monotonic() >= deadline:
            break
    if count == len(remaining):
        run.phase = "allocate"
        run.cursor = 0


def _allocate(run, chunk_size, deadline):
    # This query is only reached after ALL records have been collected. SQL
    # ordering remains global even when allocation itself spans task chunks.
    candidates = MatchingCandidate.query.filter_by(match_date=run.match_date).order_by(
        MatchingCandidate.time_diff_seconds, MatchingCandidate.record_id, MatchingCandidate.image_id,
    ).limit(chunk_size).all()
    assignments = dict(run.assignments)
    occupied = {item["image_id"] for item in assignments.values()}
    count = 0
    for candidate in candidates:
        key = str(candidate.record_id)
        if key not in assignments and candidate.image_id not in occupied:
            assignments[key] = {
                "image_id": candidate.image_id, "diff": candidate.time_diff_seconds,
                "round": run.stage + 1,
                "window": run.snapshot["settings"]["TIME_MATCH_WINDOW_STAGES"][run.stage],
            }
            occupied.add(candidate.image_id)
        db.session.delete(candidate)
        count += 1
        if time.monotonic() >= deadline:
            break
    run.assignments = assignments
    if count == len(candidates) and len(candidates) < chunk_size:
        run.stage += 1
        run.phase = "collect" if run.stage < len(run.snapshot["settings"]["TIME_MATCH_WINDOW_STAGES"]) else "publish"


def _publication_lock():
    if db.engine.dialect.name == "postgresql":
        # Short publication barrier: validation and replacement must see the
        # same inputs, including manual confirmations and recognition edits.
        # It also serializes dates whose calibrated windows share an image.
        db.session.execute(text(
            "LOCK TABLE consumption_records, captured_images, dishes, dish_recognitions, match_results, video_sources IN SHARE ROW EXCLUSIVE MODE"
        ))


def _publish(run):
    _publication_lock()
    db.session.expire_all()
    snapshot = run.snapshot
    data = snapshot["data"]
    offsets = {str(record["id"]): record["offset"] for record in data["records"]}
    # Calibration is intentionally frozen; changes in actual matching inputs
    # invalidate this draft. New samples alone must not cause endless restarts.
    if _input_snapshot(run.match_date, snapshot["settings"], offsets=offsets) != data:
        logger.info("Date matching %s: inputs changed before publication; rebuilding draft", run.match_date)
        run.phase = "idle"
        run.snapshot = None
        run.assignments = {}
        return False
    protected, _ = _reserved(data)
    image_data = {image["id"]: image for image in data["images"]}
    affected_students = {match["student_id"] for match in data["matches"] if match["student_id"] is not None}
    old_image_ids = set()
    record_ids = {record["id"] for record in data["records"]}
    matches_by_record = {}
    for match in MatchResult.query.filter(
        MatchResult.consumption_record_id.in_(record_ids | set(data["excluded_record_ids"])),
    ).order_by(MatchResult.id).all():
        matches_by_record.setdefault(match.consumption_record_id, []).append(match)
    # Disabled locations and reclassified deposits must not keep stale
    # automatic reservations. Preserve any manually protected result.
    excluded_matches = [match for record_id in data["excluded_record_ids"] for match in matches_by_record.get(record_id, [])]
    for match in excluded_matches:
        if match.is_manual or match.status == MatchStatusEnum.confirmed:
            continue
        if match.image_id:
            old_image_ids.add(match.image_id)
        match.image_id = None
        match.captured_at = None
        match.status = MatchStatusEnum.unmatched_record
        match.time_diff_seconds = None
        match.price_diff = None
        match.applied_time_offset_seconds = None
        match.match_round = None
        match.match_window_seconds = None
    for record in data["records"]:
        if record["id"] in protected:
            continue
        matches = matches_by_record.get(record["id"], [])
        match = matches[0] if matches else MatchResult(consumption_record_id=record["id"])
        for previous in matches:
            if previous.image_id:
                old_image_ids.add(previous.image_id)
        for duplicate in matches[1:]:
            db.session.delete(duplicate)
        assignment = run.assignments.get(str(record["id"]))
        match.student_id = record["student_id"]
        match.match_date = run.match_date
        match.image_id = assignment["image_id"] if assignment else None
        match.captured_at = datetime.fromisoformat(image_data[assignment["image_id"]]["time"]).replace(tzinfo=timezone.utc) if assignment else None
        match.status = MatchStatusEnum.matched if assignment else MatchStatusEnum.unmatched_record
        match.time_diff_seconds = assignment["diff"] if assignment else None
        match.price_diff = 0.0 if assignment else None
        match.applied_time_offset_seconds = record["offset"] if assignment else None
        match.match_round = assignment["round"] if assignment else None
        match.match_window_seconds = assignment["window"] if assignment else None
        db.session.add(match)
        if record["student_id"] is not None:
            affected_students.add(record["student_id"])
    db.session.flush()
    relevant_image_ids = set(image_data) | old_image_ids
    occupied = {image_id for (image_id,) in db.session.query(MatchResult.image_id).filter(
        MatchResult.image_id.in_(relevant_image_ids),
        or_(MatchResult.is_manual.is_(True), MatchResult.status.in_([
            MatchStatusEnum.matched, MatchStatusEnum.confirmed, MatchStatusEnum.time_matched_only,
        ])),
    ).all()}
    # Clear stale markers and rebuild only this date's eligible free images.
    MatchResult.query.filter(
        MatchResult.status == MatchStatusEnum.unmatched_image,
        MatchResult.is_manual.isnot(True),
        or_(MatchResult.match_date == run.match_date, MatchResult.image_id.in_(occupied)),
    ).delete(synchronize_session=False)
    for image in CapturedImage.query.filter(CapturedImage.id.in_(relevant_image_ids)).all():
        if image.id in occupied:
            if image.status in (ImageStatusEnum.pending, ImageStatusEnum.identified, ImageStatusEnum.matched):
                image.status = ImageStatusEnum.matched
        elif image.status == ImageStatusEnum.matched:
            image.status = ImageStatusEnum.identified
        if image.capture_date == run.match_date and image.id not in occupied and not image.is_candidate and image.status in (ImageStatusEnum.pending, ImageStatusEnum.identified):
            db.session.add(MatchResult(
                image_id=image.id, captured_at=image.captured_at,
                match_date=run.match_date, status=MatchStatusEnum.unmatched_image,
            ))
    run.notification_student_ids = sorted(affected_students | set(run.notification_student_ids))
    logger.info("Published date matching %s generation %s: %d automatic pairs", run.match_date, run.generation, len(run.assignments))
    run.completed = run.generation
    run.snapshot = None
    run.assignments = {}
    run.phase = "notify"
    return True


def advance_date_matching(target_date):
    """Perform one recoverable step. False means the durable queue is drained."""
    run = _ensure_run(target_date)
    cfg = current_app.config
    chunk_size = max(1, int(cfg.get("MATCHING_BATCH_CHUNK_SIZE", 200)))
    deadline = time.monotonic() + max(1, int(cfg.get("MATCHING_BATCH_TIME_BUDGET_SECONDS", 240)))
    if run.phase == "notify":
        from app.tasks.nutrition import compute_nutrition_log
        notified = 0
        for student_id in run.notification_student_ids[:chunk_size]:
            compute_nutrition_log.delay(student_id, target_date.isoformat())
            notified += 1
            if time.monotonic() >= deadline:
                break
        run.notification_student_ids = run.notification_student_ids[notified:]
        if run.notification_student_ids:
            run.updated_at = datetime.now(timezone.utc)
            db.session.commit()
            return True
        run.phase = "idle"
    if run.phase == "idle":
        if run.requested <= run.completed:
            db.session.commit()
            return False
        _begin(run)
    elif run.phase == "collect":
        _collect(run, chunk_size, deadline)
    elif run.phase == "allocate":
        _allocate(run, chunk_size, deadline)
    elif run.phase == "publish":
        _publish(run)
    run.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return True
