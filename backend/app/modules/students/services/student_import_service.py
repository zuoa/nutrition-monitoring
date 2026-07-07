"""学生名单 CSV/Excel 导入（从 app.services.import_service 迁入并适配新组织模型）。

导入时会按「年级/班级」文本自动补建组织节点（默认挂到一所「默认学校→默认校区→
默认学段」下），并把学生挂到真实的 Class 外键上，避免出现 class_id 为空的孤儿
学生。重复导入按 ``student_no`` 更新。
"""
import io
import logging

import chardet
import pandas as pd

from app import db
from app.modules.students.models.student import Student, StudentSourceEnum
from app.modules.students.models.organization import (
    Campus,
    Class,
    Grade,
    School,
    Stage,
    StageTypeEnum,
)

logger = logging.getLogger(__name__)


class StudentImportService:
    def import_file(self, content: bytes, ext: str) -> dict:
        df = self._read_file(content, ext)
        imported = updated = errors = 0

        # 确保默认组织链（导入数据无校区/学校信息时统一挂这里）
        # _ensure_default_org 幂等：存在则复用，不存在则补建；始终返回 (school, campus, stage)
        school, campus, stage = self._ensure_default_org()

        for _, row in df.iterrows():
            try:
                student_no = str(row.get("student_no") or row.get("学号", "")).strip()
                name = str(row.get("name") or row.get("姓名", "")).strip()
                grade_label = str(row.get("grade_name") or row.get("年级") or row.get("grade_id") or "").strip()
                class_label = str(row.get("class_name") or row.get("班级") or row.get("class_id") or "").strip()
                # 班级必填：缺班级会产生 class_id 为空的孤儿学生，在班级报表/教师·年级组长
                # 作用域里永久不可见（与重构前的导入行为保持一致）
                if not student_no or not name or not class_label:
                    errors += 1
                    continue

                card_no = str(row.get("card_no") or row.get("消费卡号", "")).strip() or None

                grade = self._ensure_grade(stage, grade_label) if grade_label else None
                class_ = self._ensure_class(grade, class_label)

                student = Student.query.filter_by(student_no=student_no).first()
                if student:
                    student.name = name
                    student.card_no = card_no
                    student.class_id = class_.id
                    student.legacy_class_code = class_label or student.legacy_class_code
                    student.legacy_grade_code = grade_label or student.legacy_grade_code
                    updated += 1
                else:
                    student = Student(
                        student_no=student_no,
                        name=name,
                        card_no=card_no,
                        class_id=class_.id,
                        source=StudentSourceEnum.csv,
                        legacy_class_code=class_label or None,
                        legacy_grade_code=grade_label or None,
                        is_active=True,
                    )
                    db.session.add(student)
                    imported += 1
            except Exception as exc:
                logger.error("Student import row error: %s", exc)
                errors += 1

        db.session.commit()
        return {"imported": imported, "updated": updated, "errors": errors}

    # ---- 组织节点补建 ----
    def _ensure_default_org(self) -> tuple[School, Campus, Stage]:
        school = School.query.first()
        if not school:
            school = School(name="默认学校", is_active=True)
            db.session.add(school)
            db.session.flush()
        campus = Campus.query.filter_by(school_id=school.id).first()
        if not campus:
            campus = Campus(school_id=school.id, name="默认校区", is_active=True)
            db.session.add(campus)
            db.session.flush()
        stage = Stage.query.filter_by(campus_id=campus.id).first()
        if not stage:
            stage = Stage(campus_id=campus.id, name="默认学段", stage_type=StageTypeEnum.other, is_active=True)
            db.session.add(stage)
            db.session.flush()
        return school, campus, stage

    def _ensure_grade(self, stage: Stage, label: str) -> Grade:
        grade = Grade.query.filter_by(stage_id=stage.id, name=label).first()
        if not grade:
            grade = Grade(stage_id=stage.id, name=label, is_active=True)
            db.session.add(grade)
            db.session.flush()
        return grade

    def _ensure_class(self, grade: Grade | None, label: str) -> Class | None:
        if not label:
            return None
        if grade is None:
            _, _, stage = self._ensure_default_org()
            grade = self._ensure_grade(stage, "默认年级")
        cls = Class.query.filter_by(grade_id=grade.id, name=label).first()
        if not cls:
            cls = Class(grade_id=grade.id, name=label, is_active=True)
            db.session.add(cls)
            db.session.flush()
        return cls

    # ---- 文件读取（保留原编码探测逻辑）----
    def _read_file(self, content: bytes, ext: str) -> pd.DataFrame:
        if ext == "csv":
            detected = chardet.detect(content)
            encoding = detected.get("encoding", "utf-8") or "utf-8"
            df = pd.read_csv(io.BytesIO(content), encoding=encoding, dtype=str)
        else:
            df = pd.read_excel(io.BytesIO(content), dtype=str)
        df.columns = [str(c).strip() for c in df.columns]
        return df
