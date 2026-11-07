"""Local student maintenance, strict import, and promotion regression tests."""

import io
import os
import sys
import types
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

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
from app.models import User, RoleEnum, ConsumptionRecord, Report, ReportTypeEnum  # noqa: E402
from app.modules.students.models.guardian import Guardian  # noqa: E402
from app.modules.students.models.organization import School, Campus, Stage, Grade, Class  # noqa: E402
from app.modules.students.models.student import Student, EnrollmentStatusEnum  # noqa: E402
from app.modules.students.services.organization_service import (  # noqa: E402
    OrganizationError,
    create_node,
    delete_node,
    preview_delete,
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
    delete_student,
    StudentManagementError,
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
        db.session.remove()
        for model in (ConsumptionRecord, Report, Guardian, Student, Class, Grade, Stage, Campus, School, User):
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

    def test_delete_organization_subtree_requires_student_confirmation(self):
        student = create_student({"student_no": "S001", "name": "学生甲", "class_id": self.class7.id})
        disabled = create_student({"student_no": "S002", "name": "学生乙", "class_id": self.class8.id})
        delete_student(disabled.id)
        history = ConsumptionRecord(student_id=student.id, student_no=student.student_no,
                                    transaction_time=datetime.now(timezone.utc), amount=12, transaction_id="org-delete-history")
        teacher = User(name="教师", role=RoleEnum.teacher, managed_class_ids=[self.class7.id, self.class8.id])
        leader = User(name="年级组长", role=RoleEnum.grade_leader, managed_grade_ids=[self.grade7.id, self.grade8.id])
        db.session.add_all([history, teacher, leader])
        db.session.commit()

        preview = preview_delete("stages", self.stage.id)
        self.assertEqual(preview, {"node_count": 5, "student_count": 2})
        with self.assertRaisesRegex(OrganizationError, "确认同步删除学生"):
            delete_node("stages", self.stage.id, {
                "expected_node_count": 5, "expected_student_count": 2, "delete_students": False,
            })
        self.assertIsNotNone(db.session.get(Stage, self.stage.id))
        self.assertEqual(db.session.get(Student, student.id).class_id, self.class7.id)

        result = delete_node("stages", self.stage.id, {
            "expected_node_count": 5, "expected_student_count": 2, "delete_students": True,
        })
        self.assertEqual(result, preview)
        self.assertIsNone(db.session.get(Stage, self.stage.id))
        self.assertEqual(Class.query.count(), 0)
        self.assertEqual(Grade.query.count(), 0)
        self.assertIsNotNone(db.session.get(Campus, self.campus.id))
        self.assertTrue(db.session.get(Student, student.id).is_locally_disabled)
        self.assertIsNone(db.session.get(Student, student.id).class_id)
        self.assertEqual(db.session.get(Student, student.id).class_name, "七年级（1）班")
        self.assertEqual(db.session.get(Student, student.id).grade_name, "七年级")
        self.assertEqual(db.session.get(Student, disabled.id).class_name, "八年级（1）班")
        self.assertEqual(db.session.get(ConsumptionRecord, history.id).student_id, student.id)
        self.assertEqual(db.session.get(User, teacher.id).managed_class_ids, [])
        self.assertEqual(db.session.get(User, leader.id).managed_grade_ids, [])

    def test_delete_organization_api_checks_role_and_changed_counts(self):
        admin = User(name="管理员", role=RoleEnum.admin, is_active=True)
        teacher = User(name="教师", role=RoleEnum.teacher, is_active=True)
        db.session.add_all([admin, teacher])
        db.session.commit()
        url = f"/api/v1/org/grades/{self.grade7.id}"
        self.assertEqual(self.client.get(f"{url}/delete-preview", headers=self._headers(teacher)).status_code, 403)
        self.assertEqual(self.client.delete(url, headers=self._headers(teacher)).status_code, 403)

        preview = self.client.get(f"{url}/delete-preview", headers=self._headers(admin))
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.get_json()["data"], {"node_count": 2, "student_count": 0})
        create_student({"student_no": "S001", "name": "学生甲", "class_id": self.class7.id})
        stale = self.client.delete(url, json={
            "expected_node_count": 2, "expected_student_count": 0, "delete_students": False,
        }, headers=self._headers(admin))
        self.assertEqual(stale.status_code, 409)
        self.assertIsNotNone(db.session.get(Grade, self.grade7.id))

        fresh = self.client.get(f"{url}/delete-preview", headers=self._headers(admin)).get_json()["data"]
        deleted = self.client.delete(url, json={**{
            "expected_node_count": fresh["node_count"], "expected_student_count": fresh["student_count"],
        }, "delete_students": True}, headers=self._headers(admin))
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.get_json()["data"], fresh)
        self.assertIsNone(db.session.get(Grade, self.grade7.id))

    def test_delete_class_with_student_keeps_parent_and_student_history(self):
        admin = User(name="管理员", role=RoleEnum.admin, is_active=True)
        db.session.add(admin)
        db.session.commit()
        student = create_student({"student_no": "S001", "name": "学生甲", "class_id": self.class7.id})
        url = f"/api/v1/org/classes/{self.class7.id}"
        preview = self.client.get(f"{url}/delete-preview", headers=self._headers(admin))
        self.assertEqual(preview.get_json()["data"], {"node_count": 1, "student_count": 1})

        response = self.client.delete(url, json={
            "expected_node_count": 1, "expected_student_count": 1, "delete_students": True,
        }, headers=self._headers(admin))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(db.session.get(Class, self.class7.id))
        self.assertIsNotNone(db.session.get(Class, self.class8.id))
        self.assertIsNotNone(db.session.get(Grade, self.grade7.id))
        retained = db.session.get(Student, student.id)
        self.assertTrue(retained.is_locally_disabled)
        self.assertIsNone(retained.class_id)
        self.assertEqual(retained.class_name, "七年级（1）班")
        self.assertEqual(retained.grade_name, "七年级")

    def test_delete_empty_school_removes_archived_descendants(self):
        set_archived("classes", self.class8.id, True)
        preview = preview_delete("schools", self.school.id)
        self.assertEqual(preview, {"node_count": 7, "student_count": 0})
        self.assertEqual(delete_node("schools", self.school.id, {
            "expected_node_count": 7, "expected_student_count": 0, "delete_students": False,
        }), preview)
        for model in (School, Campus, Stage, Grade, Class):
            self.assertEqual(model.query.count(), 0)

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

        student_id = created.get_json()["data"]["id"]
        denied_delete = self.client.delete(
            f"/api/v1/students/{student_id}",
            headers=self._headers(teacher),
        )
        self.assertEqual(denied_delete.status_code, 403)
        deleted = self.client.delete(
            f"/api/v1/students/{student_id}",
            headers=self._headers(admin),
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(deleted.get_json()["data"]["is_locally_disabled"])
        self.assertFalse(deleted.get_json()["data"]["is_active"])

    def test_delete_student_is_reversible_and_preserves_guardians(self):
        student = create_student({"student_no": "S001", "name": "学生甲", "class_id": self.class7.id})
        guardian = create_guardian(student.id, {"name": "家长甲", "relation": "父"})

        deleted = delete_student(student.id)
        self.assertIsNotNone(deleted)
        self.assertTrue(deleted.is_locally_disabled)
        self.assertFalse(deleted.is_active)
        self.assertIsNotNone(db.session.get(Guardian, guardian.id))

        restored = update_student(student.id, {"is_locally_disabled": False})
        self.assertFalse(restored.is_locally_disabled)
        self.assertTrue(restored.is_active)

        update_student(student.id, {"enrollment_status": "graduated"})
        delete_student(student.id)
        admin = User(name="管理员", role=RoleEnum.admin, is_active=True)
        db.session.add(admin)
        db.session.commit()
        headers = self._headers(admin)
        disabled = self.client.get("/api/v1/students/?status=disabled", headers=headers)
        graduated = self.client.get("/api/v1/students/?status=graduated", headers=headers)
        self.assertEqual([item["id"] for item in disabled.get_json()["data"]["items"]], [student.id])
        self.assertEqual(graduated.get_json()["data"]["items"], [])

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
        self.assertEqual(preview["summary"], {"total": 2, "creates": 1, "updates": 0, "errors": 1, "deactivates": 0})
        with self.assertRaises(StudentImportError):
            service.import_file(content, "xlsx")
        self.assertEqual(Student.query.count(), 0)

        valid = self._workbook_bytes([
            ["S001", "学生甲", "示范学校", "本部", "初中部", "七年级", "七年级（1）班", "C001", "女"],
        ])
        result = service.import_file(valid, "xlsx")
        self.assertEqual(result["imported"], 1)
        self.assertEqual(Student.query.one().class_id, self.class7.id)

    def test_student_demographics_round_trip_validation_and_clearing(self):
        student = create_student({"student_no": "S001", "name": "学生甲", "class_id": self.class7.id,
                                  "age": 12, "birth_date": "2014-01-02", "gender": "男"})
        self.assertEqual(student.to_dict()["birth_date"], "2014-01-02")
        self.assertEqual(student.to_dict()["age"], 12)
        for invalid in (0, -1, 15.5, True, "abc", 121):
            with self.subTest(age=invalid), self.assertRaises(StudentManagementError):
                update_student(student.id, {"name": "不应写入", "age": invalid})
        self.assertEqual(student.name, "学生甲")
        with self.assertRaises(StudentManagementError):
            update_student(student.id, {"birth_date": "2026-02-30"})
        with self.assertRaises(StudentManagementError):
            update_student(student.id, {"birth_date": "2999-01-01"})
        update_student(student.id, {"age": None, "birth_date": None})
        self.assertIsNone(student.age)
        self.assertIsNone(student.birth_date)

    def test_import_demographics_and_old_templates_preserve_existing_values(self):
        service = StudentImportService()
        headers = "学号,姓名,学校,校区,学段,年级,班级,性别,出生日期,年龄\n"
        row = "S001,学生甲,示范学校,本部,初中部,七年级,七年级（1）班,女,2014-01-02,12\n"
        service.import_file((headers + row).encode(), "csv")
        student = Student.query.one()
        self.assertEqual(student.birth_date, date(2014, 1, 2))
        self.assertEqual(student.age, 12)
        old = self._workbook_bytes([["S001", "学生甲", "示范学校", "本部", "初中部", "七年级", "七年级（1）班", "", "女"]])
        service.import_file(old, "xlsx")
        self.assertEqual(student.birth_date, date(2014, 1, 2))
        self.assertEqual(student.age, 12)
        invalid = (headers + row.replace(",12\n", ",-1\n")).encode()
        self.assertEqual(service.preview_file(invalid, "csv")["summary"]["errors"], 1)
        with self.assertRaises(StudentImportError):
            service.import_file(invalid, "csv")
        self.assertEqual(student.age, 12)

    def test_import_preserves_unlisted_students_by_default(self):
        missing = create_student({"student_no": "S001", "name": "原学生", "class_id": self.class7.id})
        content = self._workbook_bytes([self._import_row("S002")])
        service = StudentImportService()
        preview = service.preview_file(content, "xlsx")
        self.assertFalse(preview["remove_missing"])
        self.assertEqual(preview["missing_students"], [])
        self.assertEqual(service.import_file(content, "xlsx")["deactivated"], 0)
        self.assertTrue(missing.is_active)
        self.assertFalse(missing.is_locally_disabled)

    def test_import_cleanup_is_class_scoped_and_preserves_transfers_and_history(self):
        missing = create_student({"student_no": "S001", "name": "名单外学生", "class_id": self.class7.id})
        transfer = create_student({"student_no": "S002", "name": "转班学生", "class_id": self.class7.id})
        other_class = Class(name="八年级（2）班", grade_id=self.grade8.id)
        db.session.add(other_class)
        db.session.commit()
        unaffected = create_student({"student_no": "S003", "name": "其他班学生", "class_id": other_class.id})
        guardian = create_guardian(missing.id, {"name": "家长甲", "relation": "父"})
        consumption = ConsumptionRecord(student_id=missing.id, student_no=missing.student_no, transaction_id="history-1",
                                        transaction_time=datetime.now(timezone.utc), amount=12)
        report = Report(report_type=ReportTypeEnum.personal_weekly, target_id=str(missing.id),
                        period_start=date(2026, 1, 1), period_end=date(2026, 1, 7), content={"saved": True})
        db.session.add_all([consumption, report])
        db.session.commit()
        content = self._workbook_bytes([
            self._import_row("S002", grade="八年级", class_name="八年级（1）班"),
            self._import_row("S004"),
        ])
        service = StudentImportService()
        preview = service.preview_file(content, "xlsx", remove_missing=True)
        self.assertEqual(preview["summary"]["deactivates"], 1)
        self.assertEqual([row["student_id"] for row in preview["missing_students"]], [missing.id])
        self.assertTrue(missing.is_active)  # Preview must not mutate students.
        result = service.import_file(content, "xlsx", remove_missing=True, preview_token=preview["preview_token"])
        self.assertEqual(result, {"imported": 1, "updated": 1, "deactivated": 1, "errors": 0})
        self.assertFalse(missing.is_active)
        self.assertTrue(missing.is_locally_disabled)
        self.assertEqual(transfer.class_id, self.class8.id)
        self.assertTrue(transfer.is_active)
        self.assertTrue(unaffected.is_active)
        self.assertEqual(db.session.get(ConsumptionRecord, consumption.id).student_id, missing.id)
        self.assertEqual(db.session.get(Report, report.id).content, {"saved": True})
        self.assertEqual(db.session.get(Guardian, guardian.id).student_id, missing.id)

    def test_import_does_not_reactivate_disabled_or_graduated_students(self):
        disabled = create_student({"student_no": "S001", "name": "已停用", "class_id": self.class7.id})
        delete_student(disabled.id)
        graduated = create_student({"student_no": "S002", "name": "已毕业", "class_id": self.class7.id})
        update_student(graduated.id, {"enrollment_status": "graduated"})
        service = StudentImportService()
        for numbers in (("S003",), ("S001", "S002")):
            content = self._workbook_bytes([self._import_row(number) for number in numbers])
            preview = service.preview_file(content, "xlsx", remove_missing=True)
            self.assertNotIn(disabled.id, [row["student_id"] for row in preview["missing_students"]])
            self.assertNotIn(graduated.id, [row["student_id"] for row in preview["missing_students"]])
            service.import_file(content, "xlsx", remove_missing=True, preview_token=preview["preview_token"])
            self.assertFalse(disabled.is_active)
            self.assertTrue(disabled.is_locally_disabled)
            self.assertFalse(graduated.is_active)
            self.assertEqual(graduated.enrollment_status, EnrollmentStatusEnum.graduated)

    def test_import_cleanup_requires_preview_and_rejects_changed_file_or_option(self):
        missing = create_student({"student_no": "S001", "name": "名单外学生", "class_id": self.class7.id})
        content = self._workbook_bytes([self._import_row("S002")])
        changed = self._workbook_bytes([self._import_row("S003")])
        service = StudentImportService()
        with self.assertRaisesRegex(StudentImportError, "先校验并预览"):
            service.import_file(content, "xlsx", remove_missing=True)
        for file_content, option in ((changed, True), (content, False)):
            preview = service.preview_file(content, "xlsx", remove_missing=option)
            with self.assertRaises(StudentImportError) as caught:
                service.import_file(file_content, "xlsx", remove_missing=True, preview_token=preview["preview_token"])
            self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(Student.query.count(), 1)
        self.assertTrue(missing.is_active)

    def test_import_cleanup_rejects_changed_student_snapshot(self):
        missing = create_student({"student_no": "S001", "name": "名单外学生", "class_id": self.class7.id})
        content = self._workbook_bytes([self._import_row("S002")])
        service = StudentImportService()
        mutations = [
            lambda: create_student({"student_no": "S003", "name": "新增学生", "class_id": self.class7.id}),
            lambda: update_student(missing.id, {"class_id": self.class8.id}),
            lambda: update_student(missing.id, {"name": "新姓名"}),
            lambda: delete_student(missing.id),
        ]
        for mutate in mutations:
            update_student(missing.id, {"class_id": self.class7.id, "is_locally_disabled": False})
            preview = service.preview_file(content, "xlsx", remove_missing=True)
            mutate()
            with self.assertRaisesRegex(StudentImportError, "重新预览"):
                service.import_file(content, "xlsx", remove_missing=True, preview_token=preview["preview_token"])
            self.assertIsNone(Student.query.filter_by(student_no="S002").first())

    def test_import_cleanup_ignores_unrelated_student_changes(self):
        create_student({"student_no": "S001", "name": "名单外学生", "class_id": self.class7.id})
        content = self._workbook_bytes([self._import_row("S002")])
        service = StudentImportService()
        preview = service.preview_file(content, "xlsx", remove_missing=True)
        other = create_student({"student_no": "S003", "name": "其他班学生", "class_id": self.class8.id})
        result = service.import_file(content, "xlsx", remove_missing=True, preview_token=preview["preview_token"])
        self.assertEqual(result["deactivated"], 1)
        self.assertTrue(other.is_active)

    def test_import_cleanup_rejects_empty_and_invalid_files_without_writes(self):
        missing = create_student({"student_no": "S001", "name": "原学生", "class_id": self.class7.id})
        service = StudentImportService()
        for content, ext in ((b"", "csv"), (b"\n\n", "csv"), (self._workbook_bytes([]), "xlsx")):
            with self.subTest(ext=ext), self.assertRaises(StudentImportError):
                service.preview_file(content, ext, remove_missing=True)
        for rows in (
            [self._import_row("S002"), self._import_row("S002")],
            [self._import_row("S002"), self._import_row("S003", class_name="不存在的班级")],
            [self._import_row("S002"), self._import_row("")],
        ):
            content = self._workbook_bytes(rows)
            preview = service.preview_file(content, "xlsx", remove_missing=True)
            self.assertGreater(preview["summary"]["errors"], 0)
            self.assertEqual(preview["missing_students"], [])
            with self.assertRaises(StudentImportError):
                service.import_file(content, "xlsx", remove_missing=True, preview_token=preview["preview_token"])
            self.assertEqual(Student.query.count(), 1)
            self.assertTrue(missing.is_active)

    def test_import_cleanup_rolls_back_all_changes_on_commit_failure(self):
        listed = create_student({"student_no": "S001", "name": "原姓名", "class_id": self.class7.id})
        missing = create_student({"student_no": "S002", "name": "名单外学生", "class_id": self.class7.id})
        content = self._workbook_bytes([self._import_row("S001"), self._import_row("S003")])
        service = StudentImportService()
        preview = service.preview_file(content, "xlsx", remove_missing=True)
        with patch.object(db.session, "commit", side_effect=RuntimeError("commit failed")):
            with self.assertRaisesRegex(RuntimeError, "commit failed"):
                service.import_file(content, "xlsx", remove_missing=True, preview_token=preview["preview_token"])
        self.assertEqual(listed.name, "原姓名")
        self.assertTrue(missing.is_active)
        self.assertFalse(missing.is_locally_disabled)
        self.assertIsNone(Student.query.filter_by(student_no="S003").first())

    def test_import_api_cleanup_option_token_and_authorization(self):
        admin = User(name="管理员", role=RoleEnum.admin)
        teacher = User(name="教师", role=RoleEnum.teacher)
        db.session.add_all([admin, teacher])
        db.session.commit()
        missing = create_student({"student_no": "S001", "name": "名单外学生", "class_id": self.class7.id})
        content = self._workbook_bytes([self._import_row("S002")])

        def post(endpoint, user=admin, **options):
            return self.client.post(f"/api/v1/students/import/{endpoint}",
                                    data={"file": (io.BytesIO(content), "students.xlsx"), **options}, headers=self._headers(user))

        self.assertEqual(post("preview", user=teacher).status_code, 403)
        self.assertEqual(post("apply", user=teacher).status_code, 403)
        self.assertEqual(post("preview", remove_missing="invalid").status_code, 400)
        self.assertEqual(post("apply", remove_missing="true").status_code, 400)
        self.assertEqual(post("preview").get_json()["data"]["summary"]["deactivates"], 0)
        preview = post("preview", remove_missing="true").get_json()["data"]
        self.assertEqual(preview["summary"]["deactivates"], 1)
        self.assertEqual(post("apply", remove_missing="true", preview_token="stale").status_code, 409)
        result = post("apply", remove_missing="true", preview_token=preview["preview_token"])
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json()["data"]["deactivated"], 1)
        self.assertFalse(missing.is_active)

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
    def _import_row(student_no, grade="七年级", class_name="七年级（1）班"):
        return [student_no, "学生甲", "示范学校", "本部", "初中部", grade, class_name, "", "女"]

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
