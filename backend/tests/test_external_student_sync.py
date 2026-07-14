"""外部学生名单 Provider 与通用入库服务测试。"""

import os
import sys
import types
import unittest
from unittest import mock

import requests
from flask import Flask


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

from app import db  # noqa: E402
import app.models  # noqa: F401,E402
from app.modules.students.models.guardian import Guardian  # noqa: E402
from app.modules.students.models.organization import Campus, Class, Grade, School, Stage  # noqa: E402
from app.modules.students.models.student import Student, StudentSourceEnum  # noqa: E402
from app.modules.students.services.external_roster_sync_service import ExternalRosterSyncService  # noqa: E402
from app.modules.students.services.rest_student_provider import (  # noqa: E402
    RestStudentListProvider,
    StudentRosterProviderError,
)
from app.modules.students.services.sync_backends import get_student_sync_backend  # noqa: E402
from app.modules.students.services.sync_provider import StudentRosterEntry  # noqa: E402


class _Response:
    def __init__(self, payload, status_error=None):
        self.payload = payload
        self.status_error = status_error

    def raise_for_status(self):
        if self.status_error:
            raise self.status_error

    def json(self):
        return self.payload


class RestStudentListProviderTests(unittest.TestCase):
    def test_get_request_normalizes_customer_contract(self):
        provider = RestStudentListProvider({
            "STUDENT_SYNC_REST_URL": "http://school.example/rest?method=studentlist",
            "STUDENT_SYNC_REST_API_KEY": "test-secret",
            "STUDENT_SYNC_REST_HTTP_METHOD": "GET",
            "STUDENT_SYNC_REST_TIMEOUT_SECONDS": 8,
        })
        response = _Response({
            "code": 1,
            "msg": "成功",
            "data": [{
                "user_code": "S001",
                "user_account": "REG001",
                "user_name": "张三",
                "user_sex": "男",
                "id_number": "sensitive-id",
                "grade_name": "七年级",
                "class_name": "1班",
                "floor_name": "1号楼",
                "storey_name": "3层",
                "dorm_name": "301",
            }],
        })

        with mock.patch("requests.request", return_value=response) as request_mock:
            entries = provider.fetch_students()

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].external_id, "S001")
        self.assertEqual(entries[0].registration_no, "REG001")
        self.assertEqual(entries[0].grade_name, "七年级")
        self.assertEqual(entries[0].dorm_room, "301")
        request_mock.assert_called_once_with(
            "GET",
            "http://school.example/rest?method=studentlist",
            timeout=8,
            headers={"Accept": "application/json"},
            params={"apikey": "test-secret"},
        )

    def test_rejects_unsuccessful_business_response(self):
        provider = RestStudentListProvider({
            "STUDENT_SYNC_REST_URL": "http://school.example/rest",
            "STUDENT_SYNC_REST_API_KEY": "test-secret",
        })
        with mock.patch("requests.request", return_value=_Response({"code": 0, "msg": "无权限"})):
            with self.assertRaisesRegex(StudentRosterProviderError, "无权限"):
                provider.fetch_students()

    def test_request_error_does_not_leak_api_key(self):
        provider = RestStudentListProvider({
            "STUDENT_SYNC_REST_URL": "http://school.example/rest",
            "STUDENT_SYNC_REST_API_KEY": "must-not-leak",
        })
        request_error = requests.ConnectionError(
            "failed: http://school.example/rest?apikey=must-not-leak"
        )
        with mock.patch("requests.request", side_effect=request_error):
            with self.assertRaises(StudentRosterProviderError) as context:
                provider.fetch_students()
        self.assertNotIn("must-not-leak", str(context.exception))

    def test_factory_rejects_unknown_provider(self):
        with self.assertRaisesRegex(ValueError, "不支持"):
            get_student_sync_backend({"STUDENT_SYNC_PROVIDER": "customer_if_else"})


class _FakeProvider:
    key = "rest_student_list"
    label = "测试名单"
    configured = True

    def __init__(self, entries):
        self.entries = entries

    def fetch_students(self):
        return self.entries


class ExternalRosterSyncServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask(__name__)
        cls.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(cls.app)
        cls.app_context = cls.app.app_context()
        cls.app_context.push()
        db.create_all()

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        db.drop_all()
        cls.app_context.pop()

    def setUp(self):
        for model in (Guardian, Student, Class, Grade, Stage, Campus, School):
            model.query.delete()
        db.session.commit()

    def tearDown(self):
        db.session.rollback()

    @staticmethod
    def _entry(**overrides):
        values = {
            "external_id": "S001",
            "student_no": "S001",
            "name": "新姓名",
            "registration_no": "REG001",
            "gender": "女",
            "grade_name": "七年级",
            "class_name": "1班",
            "identity_number": "must-not-be-persisted",
            "dorm_room": "301",
        }
        values.update(overrides)
        return StudentRosterEntry(**values)

    def _service(self, entries, deactivate_missing=False):
        return ExternalRosterSyncService(
            _FakeProvider(entries),
            school_name="示范学校",
            campus_name="本部",
            stage_name="初中部",
            deactivate_missing=deactivate_missing,
        )

    def test_creates_org_and_student_from_normalized_entry(self):
        stats = self._service([self._entry()]).sync()

        student = Student.query.one()
        self.assertEqual(stats["students_created"], 1)
        self.assertEqual(student.student_no, "S001")
        self.assertEqual(student.registration_no, "REG001")
        self.assertEqual(student.sync_provider, "rest_student_list")
        self.assertEqual(student.external_id, "S001")
        self.assertEqual(student.source, StudentSourceEnum.api)
        self.assertEqual(student.class_.name, "1班")
        self.assertEqual(student.class_.grade.name, "七年级")
        self.assertFalse(hasattr(student, "identity_number"))
        self.assertFalse(hasattr(student, "dorm_room"))

    def test_updates_managed_fields_and_preserves_local_overrides(self):
        self._service([self._entry(name="旧姓名")]).sync()
        student = Student.query.one()
        student.card_no = "CARD-1"
        student.is_locally_disabled = True
        student.is_active = False
        db.session.commit()

        stats = self._service([
            self._entry(name="新姓名", class_name="2班", gender="女"),
        ]).sync()

        student = Student.query.one()
        self.assertEqual(stats["students_updated"], 1)
        self.assertEqual(student.name, "新姓名")
        self.assertEqual(student.class_.name, "2班")
        self.assertEqual(student.card_no, "CARD-1")
        self.assertTrue(student.is_locally_disabled)
        self.assertFalse(student.is_active)

    def test_does_not_take_over_local_student_with_same_number(self):
        db.session.add(Student(student_no="S001", name="本地学生", source=StudentSourceEnum.local))
        db.session.commit()

        stats = self._service([self._entry()]).sync()

        student = Student.query.one()
        self.assertEqual(stats["students"], 0)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(student.source, StudentSourceEnum.local)
        self.assertIsNone(student.sync_provider)

    def test_missing_students_are_only_deactivated_when_enabled_and_snapshot_nonempty(self):
        self._service([
            self._entry(),
            self._entry(external_id="S002", student_no="S002", name="李四"),
        ]).sync()

        stats = self._service([self._entry()], deactivate_missing=True).sync()
        self.assertEqual(stats["deactivated"], 1)
        self.assertFalse(Student.query.filter_by(external_id="S002").one().is_active)

        Student.query.filter_by(external_id="S002").one().is_active = True
        db.session.commit()
        empty_stats = self._service([], deactivate_missing=True).sync()
        self.assertEqual(empty_stats["deactivated"], 0)
        self.assertTrue(empty_stats["deactivation_suppressed"])
        self.assertTrue(Student.query.filter_by(external_id="S002").one().is_active)

    def test_invalid_row_suppresses_missing_student_deactivation(self):
        self._service([
            self._entry(),
            self._entry(external_id="S002", student_no="S002", name="李四"),
        ]).sync()

        stats = self._service([
            self._entry(),
            self._entry(external_id="", student_no="", name="坏数据"),
        ], deactivate_missing=True).sync()

        self.assertTrue(stats["deactivation_suppressed"])
        self.assertTrue(Student.query.filter_by(external_id="S002").one().is_active)


if __name__ == "__main__":
    unittest.main()
