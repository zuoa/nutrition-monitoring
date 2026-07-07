import logging
import io
import json
import chardet
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import pandas as pd
from sqlalchemy import or_
from flask import current_app, has_app_context
from app import db
from app.models import ConsumptionRecord
from app.modules.students.models.student import Student

logger = logging.getLogger(__name__)

STANDARD_FIELDS = {
    "student_id": ["student_id", "学号", "消费卡号", "学号/消费卡号", "学号/消费卡号 *", "card_no", "cardno", "帐号", "账号", "个人编号"],
    "student_name": ["student_name", "学生姓名", "姓名", "name"],
    "transaction_time": ["transaction_time", "消费时间", "消费时间 *", "time", "datetime", "交易时间"],
    "amount": ["amount", "金额", "消费金额", "消费金额 *", "price", "交易金额"],
    "transaction_id": ["transaction_id", "流水号", "流水号 *", "serial_no", "serialno", "交易流水号", "钱包流水号"],
    "channel_id": [
        "channel_id", "channel", "通道", "通道 *", "通道ID", "通道号",
        "摄像头通道", "摄像头通道ID", "相机通道", "视频通道",
        "transaction_location", "交易地点", "消费地点", "交易场所", "商户", "商户名称", "终端名称",
    ],
}

WEAK_TRANSACTION_ID_COLUMNS = {"钱包流水号"}


def _resolve_import_timezone() -> ZoneInfo:
    timezone_name = "Asia/Shanghai"
    if has_app_context():
        timezone_name = str(
            current_app.config.get("VIDEO_TIMEZONE")
            or current_app.config.get("APP_TIMEZONE")
            or timezone_name
        ).strip() or timezone_name

    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown import timezone=%s, fallback to Asia/Shanghai", timezone_name)
        return ZoneInfo("Asia/Shanghai")


def _normalize_transaction_time(value: datetime) -> datetime:
    tz = _resolve_import_timezone()
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)


def normalize_location_text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def normalize_allowed_transaction_locations(value: object) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                items = parsed
            else:
                items = []
                for line in raw.replace("\r", "\n").replace("，", ",").splitlines():
                    items.extend(line.split(","))
        else:
            items = []
            for line in raw.replace("\r", "\n").replace("，", ",").splitlines():
                items.extend(line.split(","))
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        return []

    normalized = []
    seen = set()
    for item in items:
        text = normalize_location_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


class ConsumptionImportService:
    def preview(self, content: bytes, ext: str) -> dict:
        df = self._read_file(content, ext)
        return {
            "columns": list(df.columns),
            "preview_rows": df.head(10).fillna("").to_dict(orient="records"),
            "suggested_mapping": self._suggest_mapping(list(df.columns)),
            "total_rows": len(df),
        }

    def import_file(
        self,
        content: bytes,
        ext: str,
        batch_id: str,
        field_mapping: dict = None,
        allowed_locations: list[str] | None = None,
    ) -> dict:
        df = self._read_file(content, ext)
        mapping = field_mapping or self._suggest_mapping(list(df.columns))
        allowed_channels = [
            normalize_location_text(item)
            for item in normalize_allowed_transaction_locations(allowed_locations)
        ]
        allowed_channel_set = set(allowed_channels)

        if allowed_channel_set and not mapping.get("channel_id"):
            raise ValueError("已配置允许导入的通道，请先映射通道字段")

        errors = []
        imported = 0
        skipped_dup = 0
        skipped_by_location = 0

        for idx, row in df.iterrows():
            row_num = idx + 2  # 1-indexed + header
            try:
                mapped_row = self._map_row(row, mapping, batch_id)
                if mapped_row is None:
                    errors.append({"row": row_num, "error": "必填字段缺失"})
                    continue
                record, channel_text = mapped_row

                if allowed_channel_set:
                    if channel_text not in allowed_channel_set:
                        skipped_by_location += 1
                        continue

                exists = self._find_existing_record(
                    record.transaction_id,
                    record.student_no,
                    mapping.get("transaction_id"),
                )
                if exists:
                    skipped_dup += 1
                    continue

                # Try to link student
                student = None
                if record.student_no:
                    student = Student.query.filter(
                        (Student.student_no == record.student_no)
                        | (Student.card_no == record.student_no)
                    ).first()
                if student:
                    record.student_id = student.id
                    record.student_name = record.student_name or student.name

                db.session.add(record)
                imported += 1
            except Exception as e:
                errors.append({"row": row_num, "error": str(e)})

        db.session.commit()
        logger.info(f"Import batch {batch_id}: {imported} imported, {skipped_dup} skipped, {len(errors)} errors")

        return {
            "batch_id": batch_id,
            "imported": imported,
            "skipped_duplicates": skipped_dup,
            "skipped_by_location": skipped_by_location,
            "errors": errors[:50],  # limit error list
            "total_rows": len(df),
        }

    def _read_file(self, content: bytes, ext: str) -> pd.DataFrame:
        if ext == "csv":
            detected = chardet.detect(content)
            candidates = [
                detected.get("encoding"),
                "utf-8-sig",
                "gb18030",
                "gbk",
                "utf-8",
            ]
            encodings = []
            for encoding in candidates:
                if not encoding:
                    continue
                normalized = str(encoding).strip()
                if normalized and normalized.lower() not in {item.lower() for item in encodings}:
                    encodings.append(normalized)

            last_error = None
            for encoding in encodings:
                try:
                    df = pd.read_csv(io.BytesIO(content), encoding=encoding, dtype=str)
                    break
                except UnicodeDecodeError as exc:
                    last_error = exc
            else:
                raise last_error or UnicodeDecodeError("utf-8", content, 0, 1, "无法解析 CSV 编码")
        else:
            df = pd.read_excel(io.BytesIO(content), dtype=str)
        df.columns = [str(c).strip() for c in df.columns]
        return df

    def _suggest_mapping(self, columns: list[str]) -> dict:
        mapping = {}
        cols_lower = {c.lower(): c for c in columns}
        for field, aliases in STANDARD_FIELDS.items():
            for alias in aliases:
                if alias.lower() in cols_lower:
                    mapping[field] = cols_lower[alias.lower()]
                    break
        return mapping

    def _map_row(self, row, mapping: dict, batch_id: str) -> tuple[ConsumptionRecord, str] | None:
        def get(field):
            col = mapping.get(field)
            if col and col in row and pd.notna(row[col]):
                return str(row[col]).strip()
            return None

        student_no = get("student_id")
        time_str = get("transaction_time")
        amount_str = get("amount")
        transaction_id = get("transaction_id")
        channel_text = normalize_location_text(get("channel_id"))

        if not all([student_no, transaction_id, time_str, amount_str, channel_text]):
            return None

        # Parse time
        for fmt in [
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y/%m/%d %H:%M",
        ]:
            try:
                tx_time = datetime.strptime(time_str, fmt)
                tx_time = _normalize_transaction_time(tx_time)
                break
            except ValueError:
                continue
        else:
            try:
                tx_time = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
                tx_time = _normalize_transaction_time(tx_time)
            except ValueError:
                raise ValueError(f"无法解析时间: {time_str}") from None

        amount = abs(float(amount_str.replace("¥", "").replace(",", "")))
        transaction_id = self._normalize_transaction_id(
            transaction_id,
            mapping.get("transaction_id"),
            student_no,
        )

        return (
            ConsumptionRecord(
                student_no=student_no,
                student_name=get("student_name"),
                transaction_time=tx_time,
                amount=amount,
                transaction_id=transaction_id,
                channel_id=channel_text,
                import_batch=batch_id,
            ),
            channel_text,
        )

    def _normalize_transaction_id(
        self,
        transaction_id: str,
        source_column: str | None,
        student_no: str,
    ) -> str:
        if source_column in WEAK_TRANSACTION_ID_COLUMNS:
            return f"wallet:{student_no}:{transaction_id}"
        return transaction_id

    def _find_existing_record(
        self,
        transaction_id: str,
        student_no: str,
        source_column: str | None,
    ) -> ConsumptionRecord | None:
        if source_column not in WEAK_TRANSACTION_ID_COLUMNS:
            return ConsumptionRecord.query.filter_by(transaction_id=transaction_id).first()

        legacy_raw_id = transaction_id.rsplit(":", 1)[-1]
        legacy_bad_prefix = f"wallet:{student_no}:"
        legacy_bad_suffix = f":{legacy_raw_id}"

        return ConsumptionRecord.query.filter(
            or_(
                ConsumptionRecord.transaction_id == transaction_id,
                ConsumptionRecord.transaction_id == legacy_raw_id,
                ConsumptionRecord.transaction_id.like(f"{legacy_bad_prefix}%{legacy_bad_suffix}"),
            )
        ).filter(
            or_(
                ConsumptionRecord.student_no == student_no,
                ConsumptionRecord.student_no.is_(None),
            )
        ).first()
