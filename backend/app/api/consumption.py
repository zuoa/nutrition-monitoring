import logging
import uuid
from datetime import date
from flask import Blueprint, current_app, request
from sqlalchemy import or_
from sqlalchemy.orm import joinedload
from app import db
from app.models import ConsumptionRecord, MatchResult, MatchStatusEnum, DishRecognition
from app.services.runtime_config import get_effective_config, persist_runtime_overrides
from app.utils.jwt_utils import login_required, role_required, api_ok, api_error
from app.utils.pagination import paginate, paginated_response
from app.services.import_service import ConsumptionImportService, normalize_allowed_transaction_locations

bp = Blueprint("consumption", __name__)
logger = logging.getLogger(__name__)

CONSUMPTION_ALLOWED_LOCATIONS_KEY = "CONSUMPTION_IMPORT_ALLOWED_LOCATIONS"


def _calc_image_price_total(image_id: int | None) -> float:
    if not image_id:
        return 0.0

    total = 0.0
    recognitions = DishRecognition.query.filter(
        DishRecognition.image_id == image_id,
        DishRecognition.is_low_confidence.is_(False),
    ).all()
    for recognition in recognitions:
        if recognition.dish_id and recognition.dish and recognition.dish.price is not None:
            total += float(recognition.dish.price)
    return total


def _get_allowed_transaction_locations() -> list[str]:
    cfg = get_effective_config(current_app.config)
    return normalize_allowed_transaction_locations(cfg.get(CONSUMPTION_ALLOWED_LOCATIONS_KEY, []))


@bp.route("/import-settings", methods=["GET"])
@role_required("admin")
def get_import_settings():
    return api_ok({
        "allowed_locations": _get_allowed_transaction_locations(),
    })


@bp.route("/import-settings", methods=["PUT"])
@role_required("admin")
def update_import_settings():
    data = request.get_json() or {}
    allowed_locations = normalize_allowed_transaction_locations(data.get("allowed_locations"))

    updates = {
        CONSUMPTION_ALLOWED_LOCATIONS_KEY: allowed_locations,
    }
    runtime_config_path = persist_runtime_overrides(current_app.config, updates)
    current_app.config.update(updates)
    current_app.config["LOCAL_RUNTIME_CONFIG_PATH"] = runtime_config_path

    return api_ok({
        "allowed_locations": allowed_locations,
        "runtime_config_path": runtime_config_path,
    })


@bp.route("/import", methods=["POST"])
@role_required("admin")
def import_records():
    if "file" not in request.files:
        return api_error("请上传文件")

    file = request.files["file"]
    if not file.filename:
        return api_error("文件名不能为空")

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ("csv", "xls", "xlsx"):
        return api_error("仅支持 CSV、XLS、XLSX 格式")

    field_mapping = request.form.get("field_mapping")
    import json
    mapping = json.loads(field_mapping) if field_mapping else {}

    content = file.read()
    batch_id = str(uuid.uuid4())[:8]

    try:
        svc = ConsumptionImportService()
        result = svc.import_file(
            content,
            ext,
            batch_id,
            mapping,
            allowed_locations=_get_allowed_transaction_locations(),
        )
    except Exception as e:
        logger.error(f"Import failed: {e}", exc_info=True)
        return api_error(f"导入失败：{str(e)}")

    # Trigger matching for imported records
    if result["imported"] > 0:
        from app.tasks.matching import run_matching_for_batch
        run_matching_for_batch.delay(batch_id)

    return api_ok(result)


@bp.route("/preview", methods=["POST"])
@role_required("admin")
def preview_import():
    """Preview first 10 rows of file before import."""
    if "file" not in request.files:
        return api_error("请上传文件")

    file = request.files["file"]
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ("csv", "xls", "xlsx"):
        return api_error("仅支持 CSV、XLS、XLSX 格式")

    content = file.read()
    svc = ConsumptionImportService()
    try:
        preview = svc.preview(content, ext)
    except Exception as e:
        return api_error(f"文件解析失败：{str(e)}")
    return api_ok(preview)


@bp.route("/records", methods=["GET"])
@login_required
def list_records():
    q = ConsumptionRecord.query.order_by(ConsumptionRecord.transaction_time.desc())
    if student_id := request.args.get("student_id"):
        q = q.filter(ConsumptionRecord.student_id == student_id)
    if date_str := request.args.get("date"):
        try:
            d = date.fromisoformat(date_str)
            q = q.filter(db.func.date(ConsumptionRecord.transaction_time) == d)
        except ValueError:
            return api_error("日期格式无效")
    if batch := request.args.get("batch"):
        q = q.filter(ConsumptionRecord.import_batch == batch)

    items, total, page, page_size = paginate(q)
    return api_ok(paginated_response([r.to_dict() for r in items], total, page, page_size))


@bp.route("/matches", methods=["GET"])
@login_required
def list_matches():
    q = ConsumptionRecord.query.options(
        joinedload(ConsumptionRecord.match_result),
        joinedload(ConsumptionRecord.match_result).joinedload(MatchResult.image),
        joinedload(ConsumptionRecord.student),
    ).outerjoin(
        MatchResult,
        MatchResult.consumption_record_id == ConsumptionRecord.id,
    ).order_by(ConsumptionRecord.transaction_time.desc())

    if date_str := request.args.get("date"):
        try:
            d = date.fromisoformat(date_str)
            q = q.filter(db.func.date(ConsumptionRecord.transaction_time) == d)
        except ValueError:
            return api_error("日期格式无效")
    if student_id := request.args.get("student_id"):
        q = q.filter(ConsumptionRecord.student_id == student_id)
    if status := request.args.get("status"):
        if status == MatchStatusEnum.unmatched_record.value:
            q = q.filter(or_(
                MatchResult.id.is_(None),
                MatchResult.status == MatchStatusEnum.unmatched_record,
            ))
        else:
            q = q.filter(MatchResult.status == status)

    items, total, page, page_size = paginate(q)
    result = []
    for record in items:
        match_items = list(record.match_result or [])
        match = match_items[0] if match_items else None
        if match:
            d = match.to_dict()
            d["image_price_total"] = _calc_image_price_total(match.image_id)
        else:
            d = {
                "id": record.id,
                "consumption_record_id": record.id,
                "image_id": None,
                "student_id": record.student_id,
                "status": MatchStatusEnum.unmatched_record.value,
                "time_diff_seconds": None,
                "price_diff": None,
                "image_price_total": None,
                "is_manual": False,
                "match_date": record.transaction_time.date().isoformat() if record.transaction_time else None,
                "created_at": record.created_at.isoformat() if record.created_at else None,
            }

        d["consumption_record"] = record.to_dict()
        if record.student:
            d["student"] = record.student.to_dict()
        if match and match.image:
            image = match.image.to_dict()
            recs = DishRecognition.query.filter_by(image_id=match.image.id).all()
            image["recognitions"] = [r.to_dict() for r in recs]
            d["image"] = image
        result.append(d)
    return api_ok(paginated_response(result, total, page, page_size))


@bp.route("/matches/unmatched-images", methods=["GET"])
@login_required
def list_unmatched_images():
    q = MatchResult.query.options(
        joinedload(MatchResult.image),
    ).filter(
        MatchResult.status == MatchStatusEnum.unmatched_image,
    ).order_by(MatchResult.created_at.desc())

    if date_str := request.args.get("date"):
        try:
            d = date.fromisoformat(date_str)
            q = q.filter(MatchResult.match_date == d)
        except ValueError:
            return api_error("日期格式无效")

    items, total, page, page_size = paginate(q)
    result = []
    for match in items:
        d = match.to_dict()
        d["image_price_total"] = _calc_image_price_total(match.image_id)
        if match.image:
            image = match.image.to_dict()
            recs = DishRecognition.query.filter_by(image_id=match.image.id).all()
            image["recognitions"] = [r.to_dict() for r in recs]
            d["image"] = image
        result.append(d)
    return api_ok(paginated_response(result, total, page, page_size))


@bp.route("/matches/<int:match_id>/confirm", methods=["PUT"])
@role_required("admin")
def confirm_match(match_id):
    m = MatchResult.query.get_or_404(match_id)
    data = request.get_json() or {}

    if data.get("image_id"):
        m.image_id = data["image_id"]

    m.status = MatchStatusEnum.confirmed
    m.is_manual = True
    m.confirmed_by = request.current_user.id
    from datetime import datetime, timezone
    m.confirmed_at = datetime.now(timezone.utc)
    db.session.commit()

    # Recompute nutrition log
    if m.student_id and m.match_date:
        from app.tasks.nutrition import compute_nutrition_log
        compute_nutrition_log.delay(m.student_id, m.match_date.isoformat())

    return api_ok(m.to_dict())


@bp.route("/matches/rematch", methods=["POST"])
@role_required("admin")
def rematch():
    """Batch re-trigger matching for a date."""
    data = request.get_json() or {}
    date_str = data.get("date", date.today().isoformat())
    from app.tasks.matching import run_matching_for_date
    run_matching_for_date.delay(date_str)
    return api_ok({"message": f"已触发 {date_str} 的重新匹配任务"})
