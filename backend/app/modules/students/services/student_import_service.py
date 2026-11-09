"""Strict CSV/Excel student import against an existing organization tree."""

import hashlib
import io
import json
import re

import chardet
import pandas as pd
from sqlalchemy import or_

from app import db
from app.modules.students.models.student import Student, StudentSourceEnum, EnrollmentStatusEnum
from app.modules.students.models.organization import School, Campus, Stage, Grade, Class
from app.modules.students.services.student_service import StudentManagementError, parse_age, parse_birth_date


class StudentImportError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class StudentImportService:
    OPTIONAL_COLUMNS = {
        "card_no": ("card_no", "消费卡号"),
        "gender": ("gender", "性别"),
        "age": ("age", "年龄"),
        "birth_date": ("birth_date", "出生年月日", "出生日期"),
    }

    def preview_file(self, content: bytes, ext: str, remove_missing: bool = False) -> dict:
        return self._build_preview(content, ext, remove_missing)

    def _build_preview(self, content: bytes, ext: str, remove_missing: bool, lock: bool = False) -> dict:
        df = self._read_file(content, ext)
        if df.empty:
            raise StudentImportError("导入文件不能为空，请至少填写一名学生")
        provided_fields = [field for field, columns in self.OPTIONAL_COLUMNS.items() if any(column in df.columns for column in columns)]
        rows = []
        seen_student_numbers = set()
        organization_cache = {}
        errors = 0

        for index, row in df.iterrows():
            row_number = int(index) + 2
            normalized = self._normalize_row(row)
            messages = []
            try:
                normalized["age"] = parse_age(normalized["age"])
                birthday = parse_birth_date(normalized["birth_date"])
                normalized["birth_date"] = birthday.isoformat() if birthday else None
            except StudentManagementError as exc:
                messages.append(str(exc))
            student_no = normalized["student_no"]
            if not student_no:
                messages.append("学号不能为空")
            elif student_no in seen_student_numbers:
                messages.append("文件内学号重复")
            else:
                seen_student_numbers.add(student_no)
            if not normalized["name"]:
                messages.append("姓名不能为空")

            class_ = None
            path = tuple(normalized[field] for field in ("school_name", "campus_name", "stage_name", "grade_name", "class_name"))
            if all(path):
                if path not in organization_cache:
                    organization_cache[path] = self._resolve_class(normalized, lock=lock)
                class_, org_error = organization_cache[path]
                if org_error:
                    messages.append(org_error)
            else:
                messages.append("学校、校区、学段、年级和班级均不能为空")

            if messages:
                errors += 1
            rows.append({
                "row_number": row_number,
                **normalized,
                "class_id": class_.id if class_ else None,
                "errors": messages,
            })

        class_names = {row["class_id"]: row["class_name"] for row in rows if row["class_id"] is not None}
        condition = Student.student_no.in_(seen_student_numbers)
        if remove_missing:
            condition = or_(condition, Student.class_id.in_(class_names))
        query = Student.query.filter(condition).order_by(Student.id).populate_existing()
        if lock:
            query = query.with_for_update()
        students = query.all()
        existing_by_number = {student.student_no: student for student in students}
        for row in rows:
            row["action"] = "error" if row["errors"] else "update" if row["student_no"] in existing_by_number else "create"

        missing_students = []
        if remove_missing and not errors:
            missing_students = [{
                "student_id": student.id,
                "student_no": student.student_no,
                "name": student.name,
                "class_id": student.class_id,
                "class_name": class_names[student.class_id],
            } for student in students if (
                student.class_id in class_names
                and student.student_no not in seen_student_numbers
                and student.enrollment_status == EnrollmentStatusEnum.enrolled
                and student.is_active
                and not student.is_locally_disabled
            )]

        token_payload = {
            "file_hash": hashlib.sha256(content).hexdigest(),
            "ext": ext,
            "remove_missing": remove_missing,
            "rows": rows,
            "students": [{
                "id": student.id,
                "student_no": student.student_no,
                "name": student.name,
                "class_id": student.class_id,
                "card_no": student.card_no,
                "gender": student.gender,
                "age": student.age,
                "birth_date": student.birth_date.isoformat() if student.birth_date else None,
                "enrollment_status": student.enrollment_status.value,
                "is_active": student.is_active,
                "is_locally_disabled": student.is_locally_disabled,
            } for student in students],
        }
        return {
            "remove_missing": remove_missing,
            "provided_fields": provided_fields,
            "preview_token": hashlib.sha256(json.dumps(token_payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
            "summary": {
                "total": len(rows),
                "creates": sum(row["action"] == "create" for row in rows),
                "updates": sum(row["action"] == "update" for row in rows),
                "errors": errors,
                "deactivates": len(missing_students),
            },
            "rows": rows,
            "missing_students": missing_students,
        }

    def import_file(self, content: bytes, ext: str, remove_missing: bool = False, preview_token: str | None = None) -> dict:
        try:
            if remove_missing and not preview_token:
                raise StudentImportError("清理名单外学生前请先校验并预览")
            preview = self._build_preview(content, ext, remove_missing, lock=remove_missing)
            if preview["summary"]["errors"]:
                raise StudentImportError("导入文件仍有校验错误，请修正后重新预览")
            if remove_missing and preview["preview_token"] != preview_token:
                raise StudentImportError("导入文件、选项或学生数据已发生变化，请重新预览后再执行", 409)

            imported = updated = 0
            provided_fields = set(preview["provided_fields"])
            student_numbers = [row["student_no"] for row in preview["rows"]]
            existing_by_number = {student.student_no: student for student in Student.query.filter(Student.student_no.in_(student_numbers)).all()}
            for row in preview["rows"]:
                student = existing_by_number.get(row["student_no"])
                if student:
                    student.name = row["name"]
                    for field in ("card_no", "gender", "age"):
                        if field in provided_fields:
                            setattr(student, field, row[field])
                    if "birth_date" in provided_fields:
                        student.birth_date = parse_birth_date(row["birth_date"])
                    student.class_id = row["class_id"]
                    updated += 1
                else:
                    student = Student(
                        student_no=row["student_no"],
                        name=row["name"],
                        card_no=row["card_no"],
                        gender=row["gender"],
                        age=row["age"],
                        birth_date=parse_birth_date(row["birth_date"]),
                        class_id=row["class_id"],
                        source=StudentSourceEnum.csv,
                        is_active=True,
                    )
                    db.session.add(student)
                    imported += 1
            for row in preview["missing_students"]:
                student = db.session.get(Student, row["student_id"])
                student.is_locally_disabled = True
                student.is_active = False
            db.session.commit()
            return {"imported": imported, "updated": updated, "deactivated": len(preview["missing_students"]), "errors": 0}
        except Exception:
            db.session.rollback()
            raise

    def _resolve_class(self, row: dict, lock: bool = False) -> tuple[Class | None, str | None]:
        school = self._single_active(School, lock=lock, name=row["school_name"])
        if isinstance(school, str):
            return None, f"学校{school}：{row['school_name']}"
        campus = self._single_active(Campus, lock=lock, school_id=school.id, name=row["campus_name"])
        if isinstance(campus, str):
            return None, f"校区{campus}：{row['campus_name']}"
        stage = self._single_active(Stage, lock=lock, campus_id=campus.id, name=row["stage_name"])
        if isinstance(stage, str):
            return None, f"学段{stage}：{row['stage_name']}"
        grade = self._single_active(Grade, lock=lock, stage_id=stage.id, name=row["grade_name"])
        if isinstance(grade, str):
            return None, f"年级{grade}：{row['grade_name']}"
        class_ = self._single_active(Class, lock=lock, grade_id=grade.id, name=row["class_name"])
        if isinstance(class_, str):
            return None, f"班级{class_}：{row['class_name']}"
        return class_, None

    @staticmethod
    def _single_active(model, lock: bool = False, **filters):
        query = model.query.filter_by(**filters, is_active=True)
        if lock:
            query = query.with_for_update()
        matches = query.limit(2).all()
        if not matches:
            return "不存在"
        if len(matches) > 1:
            return "名称不唯一"
        return matches[0]

    def _normalize_row(self, row) -> dict:
        return {
            "student_no": self._value(row, "student_no", "学号"),
            "name": self._value(row, "name", "姓名"),
            "school_name": self._value(row, "school_name", "学校"),
            "campus_name": self._value(row, "campus_name", "校区"),
            "stage_name": self._value(row, "stage_name", "学段"),
            "grade_name": self._value(row, "grade_name", "年级"),
            "class_name": self._value(row, "class_name", "班级"),
            "card_no": self._value(row, "card_no", "消费卡号") or None,
            "gender": self._value(row, "gender", "性别") or None,
            "age": self._value(row, "age", "年龄") or None,
            "birth_date": self._birth_date_value(row),
        }

    @classmethod
    def _birth_date_value(cls, row) -> str | None:
        value = cls._value(row, *cls.OPTIONAL_COLUMNS["birth_date"]).removesuffix(" 00:00:00")
        match = re.fullmatch(r"(\d{4})[年/.-](\d{1,2})[月/.-](\d{1,2})日?", value)
        if match:
            year, month, day = map(int, match.groups())
            return f"{year:04d}-{month:02d}-{day:02d}"
        return value or None

    @staticmethod
    def _value(row, *keys) -> str:
        for key in keys:
            if key in row and not pd.isna(row[key]):
                value = str(row[key]).strip()
                if value:
                    return value
        return ""

    @staticmethod
    def _read_file(content: bytes, ext: str) -> pd.DataFrame:
        if not content:
            raise StudentImportError("导入文件不能为空，请至少填写一名学生")
        if ext == "csv":
            detected = chardet.detect(content)
            encoding = detected.get("encoding", "utf-8") or "utf-8"
            try:
                df = pd.read_csv(io.BytesIO(content), encoding=encoding, dtype=str)
            except pd.errors.EmptyDataError as exc:
                raise StudentImportError("导入文件不能为空，请至少填写一名学生") from exc
        else:
            df = pd.read_excel(io.BytesIO(content), dtype=str)
        df.columns = [str(column).strip() for column in df.columns]
        return df
