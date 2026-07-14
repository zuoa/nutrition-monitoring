"""Strict CSV/Excel student import against an existing organization tree."""

import io

import chardet
import pandas as pd

from app import db
from app.modules.students.models.student import Student, StudentSourceEnum
from app.modules.students.models.organization import School, Campus, Stage, Grade, Class


class StudentImportError(ValueError):
    pass


class StudentImportService:
    def preview_file(self, content: bytes, ext: str) -> dict:
        df = self._read_file(content, ext)
        rows = []
        seen_student_numbers = set()
        creates = updates = errors = 0

        for index, row in df.iterrows():
            row_number = int(index) + 2
            normalized = self._normalize_row(row)
            messages = []
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
            if all(normalized[field] for field in ("school_name", "campus_name", "stage_name", "grade_name", "class_name")):
                class_, org_error = self._resolve_class(normalized)
                if org_error:
                    messages.append(org_error)
            else:
                messages.append("学校、校区、学段、年级和班级均不能为空")

            existing = Student.query.filter_by(student_no=student_no).first() if student_no else None
            action = "update" if existing else "create"
            if messages:
                errors += 1
                action = "error"
            elif existing:
                updates += 1
            else:
                creates += 1
            rows.append({
                "row_number": row_number,
                **normalized,
                "class_id": class_.id if class_ else None,
                "action": action,
                "errors": messages,
            })

        return {
            "summary": {
                "total": len(rows),
                "creates": creates,
                "updates": updates,
                "errors": errors,
            },
            "rows": rows,
        }

    def import_file(self, content: bytes, ext: str) -> dict:
        preview = self.preview_file(content, ext)
        if preview["summary"]["errors"]:
            raise StudentImportError("导入文件仍有校验错误，请修正后重新预览")

        imported = updated = 0
        for row in preview["rows"]:
            student = Student.query.filter_by(student_no=row["student_no"]).first()
            if student:
                student.name = row["name"]
                student.card_no = row["card_no"]
                student.gender = row["gender"]
                student.class_id = row["class_id"]
                updated += 1
            else:
                student = Student(
                    student_no=row["student_no"],
                    name=row["name"],
                    card_no=row["card_no"],
                    gender=row["gender"],
                    class_id=row["class_id"],
                    source=StudentSourceEnum.csv,
                    is_active=True,
                )
                db.session.add(student)
                imported += 1
        db.session.commit()
        return {"imported": imported, "updated": updated, "errors": 0}

    def _resolve_class(self, row: dict) -> tuple[Class | None, str | None]:
        school = self._single_active(School, name=row["school_name"])
        if isinstance(school, str):
            return None, f"学校{school}：{row['school_name']}"
        campus = self._single_active(Campus, school_id=school.id, name=row["campus_name"])
        if isinstance(campus, str):
            return None, f"校区{campus}：{row['campus_name']}"
        stage = self._single_active(Stage, campus_id=campus.id, name=row["stage_name"])
        if isinstance(stage, str):
            return None, f"学段{stage}：{row['stage_name']}"
        grade = self._single_active(Grade, stage_id=stage.id, name=row["grade_name"])
        if isinstance(grade, str):
            return None, f"年级{grade}：{row['grade_name']}"
        class_ = self._single_active(Class, grade_id=grade.id, name=row["class_name"])
        if isinstance(class_, str):
            return None, f"班级{class_}：{row['class_name']}"
        return class_, None

    @staticmethod
    def _single_active(model, **filters):
        matches = model.query.filter_by(**filters, is_active=True).limit(2).all()
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
        }

    @staticmethod
    def _value(row, *keys) -> str:
        for key in keys:
            if key in row and not pd.isna(row[key]):
                return str(row[key]).strip()
        return ""

    @staticmethod
    def _read_file(content: bytes, ext: str) -> pd.DataFrame:
        if ext == "csv":
            detected = chardet.detect(content)
            encoding = detected.get("encoding", "utf-8") or "utf-8"
            df = pd.read_csv(io.BytesIO(content), encoding=encoding, dtype=str)
        else:
            df = pd.read_excel(io.BytesIO(content), dtype=str)
        df.columns = [str(column).strip() for column in df.columns]
        return df
