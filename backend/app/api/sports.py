"""Administrator configuration and browsing of vendor sport results."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, current_app, request
from sqlalchemy import or_
from sqlalchemy.orm import contains_eager, joinedload

from app.models import SportRecord, Student
from app.services.runtime_config import get_effective_config, persist_runtime_overrides
from app.services.sport_ingestion_service import (
    PRODUCT_NAMES, SCHOOL_ID_MAX_LENGTH, SPORT_NAMES, normalize_school_ids,
)
from app.utils.jwt_utils import api_error, api_ok, role_required
from app.utils.pagination import paginate, paginated_response

bp = Blueprint("sports", __name__)


def _record_timezone():
    try:
        return ZoneInfo(current_app.config.get("APP_TIMEZONE") or "Asia/Shanghai")
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return ZoneInfo("Asia/Shanghai")


def _serialize(record, include_raw=False):
    item = record.to_dict(include_raw=include_raw)
    item["student_name"] = record.student.name if record.student else None
    item["sport_name"] = SPORT_NAMES.get(record.sport_type)
    item["product_name"] = PRODUCT_NAMES.get(record.product_type)
    return item


@bp.get("/config")
@role_required("admin")
def get_config():
    cfg = get_effective_config(current_app.config)
    return api_ok({
        "school_ids": normalize_school_ids(cfg.get("SPORTS_PUSH_ALLOWED_SCHOOL_IDS", [])),
        "check_string_configured": bool(cfg.get("SPORTS_PUSH_CHECK_STRING")),
    })


@bp.put("/config")
@role_required("admin")
def update_config():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return api_error("配置必须是 JSON 对象")
    updates = {}
    if "school_ids" in data:
        value = data["school_ids"]
        if not isinstance(value, list):
            return api_error("school_ids 必须是字符串数组")
        schools = []
        for item in value:
            if not isinstance(item, str):
                return api_error("school_ids 每项必须是字符串")
            item = item.strip()
            if not item:
                continue
            if len(item) > SCHOOL_ID_MAX_LENGTH:
                return api_error(f"每个 school_id 长度不能超过 {SCHOOL_ID_MAX_LENGTH}")
            schools.append(item)
        updates["SPORTS_PUSH_ALLOWED_SCHOOL_IDS"] = list(dict.fromkeys(schools))
    if "check_string" in data:
        value = data["check_string"]
        if not isinstance(value, str):
            return api_error("check_string 必须是字符串")
        value = value.strip()
        if not value:
            # Empty clears the override and restores the env value: unset env
            # disables intake (503), giving admins a path back to "disabled".
            updates["SPORTS_PUSH_CHECK_STRING"] = None
        elif len(value) > 1024:
            return api_error("check_string 长度不能超过 1024")
        else:
            updates["SPORTS_PUSH_CHECK_STRING"] = value
    if not updates:
        return api_error("没有可更新的配置项")
    persist_runtime_overrides(current_app.config, updates)
    return api_ok(message="体育推送配置已保存")


@bp.get("/meta")
@role_required("admin")
def get_meta():
    return api_ok({
        "sports": [{"id": key, "name": name} for key, name in SPORT_NAMES.items()],
        "products": [{"id": key, "name": name} for key, name in PRODUCT_NAMES.items()],
        "modes": [{"id": 1, "name": "练习"}, {"id": 2, "name": "测试"}],
    })


@bp.get("/records")
@role_required("admin")
def list_records():
    query = SportRecord.query
    term = (request.args.get("student") or "").strip()
    if term:
        # One join serves both the name filter and the student_name projection;
        # without a term a plain joinedload keeps the join out of the count query.
        query = (
            query.outerjoin(SportRecord.student)
            .options(contains_eager(SportRecord.student))
            .filter(or_(
                SportRecord.person_id.contains(term, autoescape=True),
                Student.name.contains(term, autoescape=True),
            ))
        )
    else:
        query = query.options(joinedload(SportRecord.student))
    if request.args.get("school_id"):
        query = query.filter(SportRecord.school_id == request.args["school_id"])
    try:
        for field in ("sport_type", "mode"):
            if request.args.get(field):
                value = int(request.args[field])
                if value < 1 or (field == "mode" and value not in (1, 2)):
                    raise ValueError()
                query = query.filter(getattr(SportRecord, field) == value)
        tz = _record_timezone()
        dates = {}
        for field in ("date_from", "date_to"):
            if request.args.get(field):
                dates[field] = datetime.strptime(request.args[field], "%Y-%m-%d").replace(tzinfo=tz)
        if len(dates) == 2 and dates["date_from"] > dates["date_to"]:
            raise ValueError()
        if "date_from" in dates:
            query = query.filter(SportRecord.start_time >= int(dates["date_from"].timestamp() * 1000))
        if "date_to" in dates:
            query = query.filter(SportRecord.start_time < int((dates["date_to"] + timedelta(days=1)).timestamp() * 1000))
    except ValueError:
        return api_error("项目、模式或日期筛选条件无效")
    records, total, page, page_size = paginate(query.order_by(SportRecord.start_time.desc(), SportRecord.id.desc()))
    # raw_result stays out of the list projection (it is the full vendor push
    # payload); the detail endpoint serves it per record on demand.
    return api_ok(paginated_response([_serialize(record) for record in records], total, page, page_size))


@bp.get("/records/<int:record_id>")
@role_required("admin")
def get_record(record_id):
    record = (
        SportRecord.query.options(joinedload(SportRecord.student))
        .filter(SportRecord.id == record_id)
        .first()
    )
    if record is None:
        return api_error("运动记录不存在", 404)
    return api_ok(_serialize(record, include_raw=True))
