import logging
import io
import chardet
from datetime import datetime
import pandas as pd
from app import db
from app.models import ConsumptionRecord, Student

logger = logging.getLogger(__name__)

STANDARD_FIELDS = {
    "student_id": ["student_id", "学号", "消费卡号", "card_no", "cardno"],
    "student_name": ["student_name", "姓名", "name"],
    "transaction_time": ["transaction_time", "消费时间", "time", "datetime", "交易时间"],
    "amount": ["amount", "金额", "消费金额", "price"],
    "transaction_id": ["transaction_id", "流水号", "serial_no", "serialno", "交易流水号"],
}


class ConsumptionImportService:
    def preview(self, content: bytes, ext: str) -> dict:
        df = self._read_file(content, ext)
        return {
            "columns": list(df.columns),
            "preview_rows": df.head(10).fillna("").to_dict(orient="records"),
            "suggested_mapping": self._suggest_mapping(list(df.columns)),
            "total_rows": len(df),
        }

    def import_file(self, content: bytes, ext: str, batch_id: str, field_mapping: dict = None) -> dict:
        df = self._read_file(content, ext)
        mapping = field_mapping or self._suggest_mapping(list(df.columns))

        errors = []
        imported = 0
        skipped_dup = 0

        for idx, row in df.iterrows():
            row_num = idx + 2  # 1-indexed + header
            try:
                record = self._map_row(row, mapping, batch_id)
                if record is None:
                    errors.append({"row": row_num, "error": "必填字段缺失"})
                    continue

                # Dedup by transaction_id
                exists = ConsumptionRecord.query.filter_by(
                    transaction_id=record.transaction_id
                ).first()
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

    def _map_row(self, row, mapping: dict, batch_id: str) -> ConsumptionRecord:
        def get(field):
            col = mapping.get(field)
            if col and col in row and pd.notna(row[col]):
                return str(row[col]).strip()
            return None

        student_no = get("student_id")
        transaction_id = get("transaction_id")
        time_str = get("transaction_time")
        amount_str = get("amount")

        if not all([student_no, transaction_id, time_str, amount_str]):
            return None

        # Parse time
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"]:
            try:
                tx_time = datetime.strptime(time_str, fmt)
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"无法解析时间: {time_str}")

        amount = float(amount_str.replace("¥", "").replace(",", ""))

        return ConsumptionRecord(
            student_no=student_no,
            student_name=get("student_name"),
            transaction_time=tx_time,
            amount=amount,
            transaction_id=transaction_id,
            import_batch=batch_id,
        )


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
