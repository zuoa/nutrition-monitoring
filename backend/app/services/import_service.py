import logging
import io
import json
import chardet
from datetime import datetime
import pandas as pd
from sqlalchemy import or_
from app import db
from app.models import ConsumptionRecord, Student

logger = logging.getLogger(__name__)

STANDARD_FIELDS = {
    "student_id": ["student_id", "学号", "消费卡号", "card_no", "cardno", "帐号", "账号", "个人编号"],
    "student_name": ["student_name", "姓名", "name"],
    "transaction_time": ["transaction_time", "消费时间", "time", "datetime", "交易时间"],
    "amount": ["amount", "金额", "消费金额", "price", "交易金额"],
    "transaction_id": ["transaction_id", "流水号", "serial_no", "serialno", "交易流水号", "钱包流水号"],
    "transaction_location": ["transaction_location", "交易地点", "消费地点", "交易场所", "商户", "商户名称", "终端名称"],
}

WEAK_TRANSACTION_ID_COLUMNS = {"钱包流水号"}


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
        normalized_allowed_locations = normalize_allowed_transaction_locations(allowed_locations)
        allowed_location_set = set(normalized_allowed_locations)

        if allowed_location_set and not mapping.get("transaction_location"):
            raise ValueError("已配置允许导入的交易地点，请先映射交易地点字段")

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
                record, transaction_location = mapped_row

                if allowed_location_set:
                    normalized_location = normalize_location_text(transaction_location)
                    if normalized_location not in allowed_location_set:
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
            encoding = detected.get("encoding", "utf-8") or "utf-8"
            df = pd.read_csv(io.BytesIO(content), encoding=encoding, dtype=str)
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

    def _map_row(self, row, mapping: dict, batch_id: str) -> tuple[ConsumptionRecord, str | None] | None:
        def get(field):
            col = mapping.get(field)
            if col and col in row and pd.notna(row[col]):
                return str(row[col]).strip()
            return None

        student_no = get("student_id")
        time_str = get("transaction_time")
        amount_str = get("amount")
        transaction_id = get("transaction_id")
        transaction_location = get("transaction_location")

        if not all([student_no, transaction_id, time_str, amount_str]):
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
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"无法解析时间: {time_str}")

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
                import_batch=batch_id,
            ),
            transaction_location,
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


class StudentImportService:
    def import_file(self, content: bytes, ext: str) -> dict:
        from app.models import Student
        if ext == "csv":
            detected = chardet.detect(content)
            encoding = detected.get("encoding", "utf-8") or "utf-8"
            df = pd.read_csv(io.BytesIO(content), encoding=encoding, dtype=str)
        else:
            df = pd.read_excel(io.BytesIO(content), dtype=str)
        df.columns = [c.strip() for c in df.columns]

        imported = updated = errors = 0
        for _, row in df.iterrows():
            try:
                student_no = str(row.get("student_no") or row.get("学号", "")).strip()
                name = str(row.get("name") or row.get("姓名", "")).strip()
                class_id = str(row.get("class_id") or row.get("班级", "")).strip()
                if not all([student_no, name, class_id]):
                    errors += 1
                    continue

                s = Student.query.filter_by(student_no=student_no).first()
                if s:
                    s.name = name
                    s.class_id = class_id
                    s.class_name = str(row.get("class_name") or row.get("班级名称", "")).strip() or None
                    s.grade_id = str(row.get("grade_id") or row.get("年级", "")).strip() or None
                    s.grade_name = str(row.get("grade_name") or row.get("年级名称", "")).strip() or None
                    s.card_no = str(row.get("card_no") or row.get("消费卡号", "")).strip() or None
                    updated += 1
                else:
                    s = Student(
                        student_no=student_no,
                        name=name,
                        class_id=class_id,
                        class_name=str(row.get("class_name") or row.get("班级名称", "")).strip() or None,
                        grade_id=str(row.get("grade_id") or row.get("年级", "")).strip() or None,
                        grade_name=str(row.get("grade_name") or row.get("年级名称", "")).strip() or None,
                        card_no=str(row.get("card_no") or row.get("消费卡号", "")).strip() or None,
                    )
                    db.session.add(s)
                    imported += 1
            except Exception as e:
                logger.error(f"Student import row error: {e}")
                errors += 1

        db.session.commit()
        return {"imported": imported, "updated": updated, "errors": errors}
