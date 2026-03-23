import logging
import uuid
from io import BytesIO
from datetime import date
from flask import Blueprint, request
from app import db
from app.models import ConsumptionRecord, MatchResult, MatchStatusEnum, Student
from app.utils.jwt_utils import login_required, role_required, api_ok, api_error
from app.utils.pagination import paginate, paginated_response
from app.services.import_service import ConsumptionImportService

bp = Blueprint("consumption", __name__)
logger = logging.getLogger(__name__)


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
        result = svc.import_file(content, ext, batch_id, mapping)
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
    q = MatchResult.query.order_by(MatchResult.created_at.desc())
    if status := request.args.get("status"):
        q = q.filter(MatchResult.status == status)
    if date_str := request.args.get("date"):
        try:
            d = date.fromisoformat(date_str)
            q = q.filter(MatchResult.match_date == d)
        except ValueError:
            return api_error("日期格式无效")
    if student_id := request.args.get("student_id"):
        q = q.filter(MatchResult.student_id == student_id)

    items, total, page, page_size = paginate(q)
    result = []
    for m in items:
        d = m.to_dict()
        if m.consumption_record:
            d["consumption_record"] = m.consumption_record.to_dict()
        if m.student:
            d["student"] = m.student.to_dict()
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
