"""Local student maintenance, strict import, and promotion regression tests."""

import io
import os
import sys
import types
import unittest
from datetime import timedelta

from flask import Flask
from openpyxl import Workbook


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

if "flask_migrate" not in sys.modules:
    flask_migrate = types.ModuleType("flask_migrate")

    class _Migrate:
        def init_app(self, *args, **kwargs):
            return None

    flask_migrate.Migrate = _Migrate
    sys.modules["flask_migrate"] = flask_migrate

if "pythonjsonlogger" not in sys.modules:
    pythonjsonlogger = types.ModuleType("pythonjsonlogger")
    jsonlogger = types.ModuleType("jsonlogger")

    class _JsonFormatter:
        def __init__(self, *args, **kwargs):
            pass

    jsonlogger.JsonFormatter = _JsonFormatter
    pythonjsonlogger.jsonlogger = jsonlogger
    sys.modules["pythonjsonlogger"] = pythonjsonlogger

if "redis" not in sys.modules:
    redis = types.ModuleType("redis")
    redis.from_url = lambda *args, **kwargs: object()
    sys.modules["redis"] = redis

if "chardet" not in sys.modules:
    chardet = types.ModuleType("chardet")
    chardet.detect = lambda content: {"encoding": "utf-8"}
    sys.modules["chardet"] = chardet

from app import db  # noqa: E402
import app.models  # noqa: F401,E402
from app.models import User, RoleEnum  # noqa: E402
from app.modules.students.models.guardian import Guardian  # noqa: E402
from app.modules.students.models.organization import School, Campus, Stage, Grade, Class  # noqa: E402
from app.modules.students.models.student import Student, EnrollmentStatusEnum  # noqa: E402
from app.modules.students.services.organization_service import (  # noqa: E402
    OrganizationError,
    create_node,
    set_archived,
)
from app.modules.students.services.promotion_service import (  # noqa: E402
    PromotionError,
    apply_promotion,
    preview_promotion,
)
from app.modules.students.services.student_import_service import (  # noqa: E402
    StudentImportError,
    StudentImportService,
)
from app.modules.students.services.student_service import (  # noqa: E402
    create_guardian,
    create_student,
    delete_guardian,
    update_student,
)
from app.modules.students.api.organization import bp as organization_bp  # noqa: E402
from app.modules.students.api.students import bp as students_bp  # noqa: E402
from app.utils.jwt_utils import generate_token  # noqa: E402


class StudentManagementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask(__name__)
        cls.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SECRET_KEY="student-management-test-secret-key",
            JWT_ALGORITHM="HS256",
            JWT_ACCESS_TOKEN_EXPIRES=timedelta(hours=1),
        )
        db.init_app(cls.app)
        cls.app.register_blueprint(organization_bp, url_prefix="/api/v1/org")
        cls.app.register_blueprint(students_bp, url_prefix="/api/v1/students")
        cls.client = cls.app.test_client()
        cls.context = cls.app.app_context()
        cls.context.push()
        db.create_all()

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        db.drop_all()
        cls.context.pop()

    def setUp(self):
        for model in (Guardian, Student, Class, Grade, Stage, Campus, School, User):
            model.query.delete()
        db.session.commit()
        self.school = School(name="示范学校")
        db.session.add(self.school)
        db.session.flush()
        self.campus = Campus(name="本部", school_id=self.school.id)
        db.session.add(self.campus)
        db.session.flush()
        self.stage = Stage(name="初中部", campus_id=self.campus.id)
        db.session.add(self.stage)
        db.session.flush()
        self.grade7 = Grade(name="七年级", stage_id=self.stage.id)
        self.grade8 = Grade(name="八年级", stage_id=self.stage.id)
        db.session.add_all([self.grade7, self.grade8])
        db.session.flush()
        self.class7 = Class(name="七年级（1）班", grade_id=self.grade7.id)
        self.class8 = Class(name="八年级（1）班", grade_id=self.grade8.id)
        db.session.add_all([self.class7, self.class8])
        db.session.commit()

    def tearDown(self):
        db.session.rollback()

    def test_organization_archive_is_guarded_and_restorable(self):
        student = create_student({"student_no": "S001", "name": "学生甲", "class_id": self.class7.id})
        with self.assertRaisesRegex(OrganizationError, "在校学生"):
            set_archived("classes", self.class7.id, True)

        update_student(student.id, {"enrollment_status": "graduated"})
        archived = set_archived("classes", self.class7.id, True)
        self.assertFalse(archived.is_active)
        restored = set_archived("classes", self.class7.id, False)
        self.assertTrue(restored.is_active)

        with self.assertRaisesRegex(OrganizationError, "同名"):
            create_node("classes", {"grade_id": self.grade7.id, "name": self.class7.name})

    def test_maintenance_endpoints_require_admin_role(self):
        admin = User(name="管理员", role=RoleEnum.admin, is_active=True)
        teacher = User(name="教师", role=RoleEnum.teacher, is_active=True)
        db.session.add_all([admin, teacher])
        db.session.commit()
        payload = {"student_no": "S001", "name": "学生甲", "class_id": self.class7.id}

        denied = self.client.post(
            "/api/v1/students/",
            json=payload,
            headers=self._headers(teacher),
        )
        self.assertEqual(denied.status_code, 403)
        created = self.client.post(
            "/api/v1/students/",
            json=payload,
            headers=self._headers(admin),
        )
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.get_json()["data"]["enrollment_status"], "enrolled")

    def test_student_graduation_restore_and_guardian_parent_link(self):
        student = create_student({"student_no": "S001", "name": "学生甲", "class_id": self.class7.id})
        update_student(student.id, {"enrollment_status": "graduated"})
        self.assertEqual(student.enrollment_status, EnrollmentStatusEnum.graduated)
        self.assertFalse(student.is_active)

        update_student(student.id, {"enrollment_status": "enrolled", "class_id": self.class8.id})
        self.assertEqual(student.class_id, self.class8.id)
        self.assertTrue(student.is_active)

        parent = User(name="家长甲", role=RoleEnum.parent, student_ids=[])
        db.session.add(parent)
        db.session.commit()
        guardian = create_guardian(student.id, {"name": "家长甲", "relation": "父", "user_id": parent.id})
        self.assertIn(student.id, parent.student_ids)
        delete_guardian(student.id, guardian.id)
        self.assertNotIn(student.id, parent.student_ids)

    def test_import_requires_existing_full_organization_path_and_is_atomic(self):
        content = self._workbook_bytes([
            ["S001", "学生甲", "示范学校", "本部", "初中部", "七年级", "七年级（1）班", "C001", "女"],
            ["S002", "学生乙", "示范学校", "本部", "初中部", "九年级", "九年级（1）班", "", "男"],
        ])
        service = StudentImportService()
        preview = service.preview_file(content, "xlsx")
        self.assertEqual(preview["summary"], {"total": 2, "creates": 1, "updates": 0, "errors": 1})
        with self.assertRaises(StudentImportError):
            service.import_file(content, "xlsx")
        self.assertEqual(Student.query.count(), 0)

        valid = self._workbook_bytes([
            ["S001", "学生甲", "示范学校", "本部", "初中部", "七年级", "七年级（1）班", "C001", "女"],
        ])
        result = service.import_file(valid, "xlsx")
        self.assertEqual(result["imported"], 1)
        self.assertEqual(Student.query.one().class_id, self.class7.id)

    def test_promotion_supports_overrides_and_rejects_stale_preview(self):
        first = create_student({"student_no": "S001", "name": "学生甲", "class_id": self.class7.id})
        second = create_student({"student_no": "S002", "name": "学生乙", "class_id": self.class7.id})
        payload = {
            "mappings": [{"source_class_id": self.class7.id, "action": "promote", "target_class_id": self.class8.id}],
            "overrides": [{"student_id": second.id, "action": "graduate"}],
        }
        preview = preview_promotion(payload)
        self.assertEqual(preview["summary"]["promoted"], 1)
        self.assertEqual(preview["summary"]["graduated"], 1)
        result = apply_promotion(payload, preview["preview_token"])
        self.assertEqual(result["promoted"], 1)
        self.assertEqual(first.class_id, self.class8.id)
        self.assertEqual(second.enrollment_status, EnrollmentStatusEnum.graduated)

        third = create_student({"student_no": "S003", "name": "学生丙", "class_id": self.class8.id})
        stale_payload = {"mappings": [{"source_class_id": self.class8.id, "action": "graduate"}]}
        stale_preview = preview_promotion(stale_payload)
        create_student({"student_no": "S004", "name": "学生丁", "class_id": self.class8.id})
        with self.assertRaisesRegex(PromotionError, "重新预览"):
            apply_promotion(stale_payload, stale_preview["preview_token"])
        db.session.refresh(third)
        self.assertEqual(third.enrollment_status, EnrollmentStatusEnum.enrolled)

    @staticmethod
    def _workbook_bytes(rows):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["学号", "姓名", "学校", "校区", "学段", "年级", "班级", "消费卡号", "性别"])
        for row in rows:
            sheet.append(row)
        output = io.BytesIO()
        workbook.save(output)
        return output.getvalue()

    @staticmethod
    def _headers(user):
        token = generate_token(user.id, user.role.value)
        return {"Authorization": f"Bearer {token}"}


if __name__ == "__main__":
    unittest.main()
