import os
import sys
import types
import unittest
from datetime import date, timedelta

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

from app import db  # noqa: E402
import app.models  # noqa: F401,E402
from app.api.reports import bp as reports_bp  # noqa: E402
from app.models import (  # noqa: E402
    Campus,
    Class,
    Grade,
    Report,
    ReportTypeEnum,
    RoleEnum,
    School,
    Stage,
    Student,
    User,
)
from app.utils.jwt_utils import generate_token  # noqa: E402


class ReportsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask(__name__)
        cls.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SECRET_KEY="test-secret",
            JWT_ALGORITHM="HS256",
            JWT_ACCESS_TOKEN_EXPIRES=timedelta(hours=1),
        )
        db.init_app(cls.app)
        cls.app.register_blueprint(reports_bp, url_prefix="/api/v1/reports")
        cls.app_context = cls.app.app_context()
        cls.app_context.push()
        db.create_all()
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        db.drop_all()
        cls.app_context.pop()

    def setUp(self):
        db.session.query(Report).delete()
        db.session.query(Student).delete()
        db.session.query(User).delete()
        db.session.query(Class).delete()
        db.session.query(Grade).delete()
        db.session.query(Stage).delete()
        db.session.query(Campus).delete()
        db.session.query(School).delete()

        school = School(name="测试学校")
        db.session.add(school)
        db.session.flush()
        self.campus = Campus(name="东校区", school_id=school.id)
        db.session.add(self.campus)
        db.session.flush()
        stage = Stage(name="初中部", campus_id=self.campus.id)
        db.session.add(stage)
        db.session.flush()
        self.grade = Grade(name="七年级", stage_id=stage.id)
        db.session.add(self.grade)
        db.session.flush()
        self.class_ = Class(name="七年级一班", grade_id=self.grade.id)
        db.session.add(self.class_)
        db.session.flush()
        self.student = Student(student_no="S001", name="测试学生", class_id=self.class_.id)
        db.session.add(self.student)

        self.admin = User(username="admin", name="管理员", role=RoleEnum.admin)
        self.teacher = User(
            username="teacher",
            name="教师",
            role=RoleEnum.teacher,
            managed_class_ids=[self.class_.id],
        )
        self.grade_leader = User(
            username="leader",
            name="年级负责人",
            role=RoleEnum.grade_leader,
            managed_grade_ids=[self.grade.id],
        )
        db.session.add_all([self.admin, self.teacher, self.grade_leader])
        db.session.commit()

    def _headers(self, user: User) -> dict[str, str]:
        token = generate_token(user.id, user.role.value)
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _group_content(scope_type: str, scope_id: int) -> dict:
        return {
            "scope_type": scope_type,
            "scope_id": scope_id,
            "analysis_basis": "student_period_average",
        }

    def _add_report(self, report_type: ReportTypeEnum, target_id: int, content: dict):
        db.session.add(Report(
            report_type=report_type,
            target_id=str(target_id),
            period_start=date(2026, 7, 6),
            period_end=date(2026, 7, 12),
            content=content,
        ))
        db.session.commit()

    def test_admin_can_read_latest_campus_group_report(self):
        self._add_report(
            ReportTypeEnum.campus_weekly,
            self.campus.id,
            self._group_content("campus", self.campus.id),
        )

        response = self.client.get(
            f"/api/v1/reports/campus/{self.campus.id}/latest",
            headers=self._headers(self.admin),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["content"]["scope_type"], "campus")

    def test_teacher_cannot_read_campus_group_report(self):
        response = self.client.get(
            f"/api/v1/reports/campus/{self.campus.id}/latest",
            headers=self._headers(self.teacher),
        )

        self.assertEqual(response.status_code, 403)

    def test_grade_leader_can_read_managed_grade_report(self):
        self._add_report(
            ReportTypeEnum.grade_weekly,
            self.grade.id,
            self._group_content("grade", self.grade.id),
        )

        response = self.client.get(
            f"/api/v1/reports/grade/{self.grade.id}/latest",
            headers=self._headers(self.grade_leader),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["content"]["scope_type"], "grade")

    def test_legacy_class_report_is_not_returned_as_group_analysis(self):
        self._add_report(
            ReportTypeEnum.class_weekly,
            self.class_.id,
            {"class_id": self.class_.id, "flagged_students": [{"name_masked": "测*"}]},
        )

        response = self.client.get(
            f"/api/v1/reports/class/{self.class_.id}/latest",
            headers=self._headers(self.teacher),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.get_json()["data"])


if __name__ == "__main__":
    unittest.main()
