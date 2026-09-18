"""Validate and durably ingest the sport data v1.1 callback protocol."""

from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time

from flask import current_app

from app import db
from app.models.sport import SportFile, SportRecord


SPORT_UNITS = {
    1: "个", 2: "cm", 3: "cm", 4: "个", 5: "个", 6: "个", 7: "个", 8: "cm",
    9: "ml", 10: "kg/m²", 11: "s", 12: "s", 13: "s", 14: "s", 15: "s",
    16: "个", 17: "个", 18: "个", 19: "m", 20: "s", 21: "s", 22: "m", 23: "个",
}
OPTIONAL_NUMBERS = (
    "all_time", "interrupt_count", "jump_speed", "jump_height", "jump_angle",
    "arm_angle", "reaction_time", "weight", "height",
)


class SportIngestionError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def verify_signature(headers):
    secret = current_app.config.get("SPORTS_PUSH_CHECK_STRING", "")
    if not secret:
        raise SportIngestionError("运动数据推送尚未配置", 503)
    timestamp = headers.get("timestamp", "")
    signature = headers.get("sign", "")
    if not re.fullmatch(r"[0-9]{13}", timestamp) or not re.fullmatch(r"[0-9a-f]{32}", signature):
        raise SportIngestionError("timestamp 或 sign 格式错误", 401)
    expected = hashlib.md5((secret + timestamp).encode("utf-8")).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise SportIngestionError("签名校验失败", 401)
    max_age = current_app.config.get("SPORTS_PUSH_TIMESTAMP_TOLERANCE_SECONDS", 300)
    if max_age > 0 and abs(time.time() * 1000 - int(timestamp)) > max_age * 1000:
        raise SportIngestionError("请求时间戳已过期或超前", 401)


def _string(data, key, max_length, allow_empty=False):
    value = data.get(key)
    if not isinstance(value, str) or len(value) > max_length or (not allow_empty and not value.strip()) or "\x00" in value:
        raise SportIngestionError(f"{key} 必须是{'可为空的' if allow_empty else '非空'}字符串，长度不超过 {max_length}")
    return value


def _number(data, key, integer=False, minimum=None, maximum=None):
    value = data.get(key)
    try:
        valid = type(value) in (int, float) and math.isfinite(value)
        valid = valid and (not integer or int(value) == value)
        valid = valid and (minimum is None or value >= minimum) and (maximum is None or value <= maximum)
    except (OverflowError, ValueError):
        valid = False
    if not valid:
        raise SportIngestionError(f"{key} 必须是有效的{'整数' if integer else '数值'}且在允许范围内")
    return int(value) if integer else value


def _insert(model):
    if db.engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert(model)


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def validate_results(payload):
    if not isinstance(payload, dict):
        raise SportIngestionError("请求体必须是 JSON 对象")
    # JSON parsers may accept NaN; reject it even in future/unknown fields.
    try:
        _digest(payload)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise SportIngestionError("请求体包含无效的 JSON 值") from None
    if payload.get("version") != "v1.1":
        raise SportIngestionError("运动数据 version 必须为 v1.1")
    school_id = _string(payload, "school_id", 128)
    allowed = current_app.config.get("SPORTS_PUSH_ALLOWED_SCHOOL_IDS", [])
    if allowed and school_id not in allowed:
        raise SportIngestionError("school_id 未获授权", 403)
    product_type = _number(payload, "product_type", integer=True, minimum=1, maximum=3)
    sport_type = _number(payload, "sport_type", integer=True, minimum=1, maximum=2147483647)
    mode = _number(payload, "mode", integer=True, minimum=1, maximum=2)
    results = payload.get("sport_result")
    limit = current_app.config.get("SPORTS_PUSH_MAX_BATCH_SIZE", 1000)
    if not isinstance(results, list) or not 1 <= len(results) <= limit:
        raise SportIngestionError(f"sport_result 必须包含 1 到 {limit} 条数据")
    records = []
    for index, result in enumerate(results):
        try:
            if not isinstance(result, dict):
                raise SportIngestionError("必须是对象")
            person_id = _string(result, "person_id", 64, allow_empty=True)
            if person_id and person_id != person_id.strip():
                raise SportIngestionError("person_id 学号不能包含首尾空白")
            start_time = _number(result, "start_time", integer=True, minimum=1000000000000, maximum=9999999999999)
            score = _number(result, "score")  # Sit-and-reach scores may be negative.
            for key in OPTIONAL_NUMBERS:
                if key in result:
                    _number(result, key, integer=(key == "interrupt_count"), minimum=0)
            for key in ("video_file_id", "face_file_id"):
                if key in result:
                    _string(result, key, 256, allow_empty=True)
            if "sub_count_list" in result:
                if not isinstance(result["sub_count_list"], list):
                    raise SportIngestionError("sub_count_list 必须是数组")
                for segment in result["sub_count_list"]:
                    if not isinstance(segment, dict):
                        raise SportIngestionError("sub_count_list 分段必须是对象")
                    _number(segment, "sub_time", minimum=0)
                    _number(segment, "sub_count", integer=True, minimum=0)
            key = [school_id, product_type, sport_type, mode, person_id, start_time]
            # Anonymous people may exercise simultaneously. Only deduplicate
            # identical anonymous results, since the protocol has no event ID.
            if not person_id:
                key.append(result)
            records.append({
                "event_key": _digest(key), "version": "v1.1", "school_id": school_id,
                "product_type": product_type, "sport_type": sport_type, "mode": mode,
                "person_id": person_id, "start_time": start_time, "score": score,
                "score_unit": SPORT_UNITS.get(sport_type), "all_time": result.get("all_time"),
                "video_file_id": result.get("video_file_id"), "face_file_id": result.get("face_file_id"),
                "raw_result": result, "received_at": datetime.now(timezone.utc),
            })
        except SportIngestionError as exc:
            raise SportIngestionError(f"sport_result[{index}]: {exc}") from None
    return records


def ingest_results(payload):
    records = validate_results(payload)
    # Sort lock acquisition order across overlapping concurrent batches.
    for values in sorted(records, key=lambda item: item["event_key"]):
        statement = _insert(SportRecord).values(**values)
        db.session.execute(statement.on_conflict_do_update(
            index_elements=["event_key"],
            set_={key: statement.excluded[key] for key in values if key != "event_key"},
        ))
    db.session.commit()


def ingest_file(form, upload):
    if form.get("version") != "v1.0":
        raise SportIngestionError("文件上传 version 必须为 v1.0")
    file_id = _string(form, "file_id", 256)
    if form.get("file_type") not in ("1", "2"):
        raise SportIngestionError("file_type 必须为 1（jpg）或 2（mp4）")
    file_type = int(form["file_type"])
    if upload is None:
        raise SportIngestionError("缺少 file 文件")
    root = Path(current_app.config.get("SPORTS_PUSH_FILE_STORAGE_PATH", "/data/sports")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    max_bytes = current_app.config.get("SPORTS_PUSH_MAX_FILE_BYTES", 100 * 1024 * 1024)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix=".upload-", delete=False) as target:
            temporary_path = Path(target.name)
            digest = hashlib.sha256()
            size = 0
            header = b""
            while chunk := upload.stream.read(64 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise SportIngestionError("文件超过大小限制", 413)
                header = (header + chunk)[:16]
                digest.update(chunk)
                target.write(chunk)
            if not size:
                raise SportIngestionError("文件不能为空")
            if (file_type == 1 and not header.startswith(b"\xff\xd8\xff")) or (file_type == 2 and header[4:8] != b"ftyp"):
                raise SportIngestionError("文件内容与 file_type 不符")
            target.flush()
            os.fsync(target.fileno())
        checksum = digest.hexdigest()
        # Neither the supplied filename nor file_id becomes a filesystem path.
        destination = root / f"{checksum}.{('jpg' if file_type == 1 else 'mp4')}"
        db.session.execute(_insert(SportFile).values(
            file_id=file_id, version="v1.0", file_type=file_type, storage_path=str(destination),
            sha256=checksum, size_bytes=size, received_at=datetime.now(timezone.utc),
        ).on_conflict_do_nothing(index_elements=["file_id"]))
        stored = db.session.get(SportFile, file_id, populate_existing=True)
        if stored.sha256 != checksum or stored.file_type != file_type:
            raise SportIngestionError("file_id 已存在且文件内容不同", 409)
        try:
            os.link(temporary_path, Path(stored.storage_path))
        except FileExistsError:
            pass
        db.session.commit()
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
