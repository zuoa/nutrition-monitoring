import os
import sys
import types
import unittest
import tempfile
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

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
    ConsumptionRecord,
    Class,
    Grade,
    NutritionLog,
    Report,
    ReportTypeEnum,
    RoleEnum,
    School,
    Stage,
    Student,
    TaskLog,
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
        db.session.remove()
        self.export_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.export_directory.cleanup)
        self.app.config.update(
            REPORT_EXPORT_STORAGE_PATH=self.export_directory.name,
            LOCAL_RUNTIME_CONFIG_PATH=str(Path(self.export_directory.name) / "runtime.json"),
            APP_TIMEZONE="Asia/Shanghai",
            CONSUMPTION_ENABLED_TRANSACTION_LOCATION_IDS=[],
        )
        db.session.query(ConsumptionRecord).delete()
        db.session.query(NutritionLog).delete()
        db.session.query(TaskLog).delete()
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

    def tearDown(self):
        db.session.rollback()
        db.session.remove()

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
            period_end=date(2026, 7, 10),
            content=content,
        ))
        db.session.commit()

    def _personal_report(self, student=None):
        student = student or self.student
        self._add_report(ReportTypeEnum.personal_weekly, student.id, {
            "student_id": student.id, "student_name": student.name, "class_name": "七年级一班",
            "avg_nutrients": {"protein": 30, "calories": 1800}, "nutrient_sample_counts": {"protein": 4, "calories": 4},
            "recommended_nutrients": {"protein": 60, "calories": 2000}, "overall_score": 74,
            "alerts": [], "suggestions": ["建议多吃奶类、豆制品"], "analysis_basis": "period_daily_average",
        })
        return Report.query.filter_by(target_id=str(student.id), report_type="personal_weekly").order_by(Report.id.desc()).first()

    def _consume(self, timestamp, *, amount=-5, channel="食堂", student=None):
        student = student or self.student
        db.session.add(ConsumptionRecord(
            student_id=student.id, student_no=student.student_no, transaction_time=datetime.fromisoformat(timestamp),
            amount=amount, channel_id=channel, transaction_id=f"tx-{ConsumptionRecord.query.count()}",
        ))
        db.session.commit()

    def _create_export(self, user=None):
        with patch("app.tasks.reports.export_class_reports.delay"):
            return self.client.post(f"/api/v1/reports/class/{self.class_.id}/exports", headers=self._headers(user or self.admin),
                                    json={"period_start": "2026-07-06", "period_end": "2026-07-10"})

    def test_legacy_report_counts_consumption_without_matches_and_deduplicates_slots(self):
        report = self._personal_report()
        for timestamp in ("2026-07-06T07:00", "2026-07-06T08:00", "2026-07-06T12:00", "2026-07-07T07:00",
                          "2026-07-10T23:59", "2026-07-11T07:00", "2026-07-12T12:00",
                          "2026-07-06T15:00", "2026-07-05T07:00", "2026-07-13T07:00"):
            self._consume(timestamp)
        self._consume("2026-07-08T07:00", amount=10)
        self._consume("2026-07-09T07:00", amount=0)
        response = self.client.get(f"/api/v1/reports/student/{self.student.id}/latest", headers=self._headers(self.admin))
        content = response.get_json()["data"]["content"]
        self.assertEqual(content["meal_statistics"], {"meal_count": 4, "breakfast_count": 2})
        self.assertIn("实际用餐次数", content["presentation_html"])
        self.assertNotIn("豆制品", content["presentation_html"])
        self.assertNotIn("meal_statistics", db.session.get(Report, report.id).content)
        detail = self.client.get(f"/api/v1/reports/{report.id}", headers=self._headers(self.admin)).get_json()["data"]
        self.assertEqual(detail["content"]["meal_statistics"], content["meal_statistics"])
        listing = self.client.get(f"/api/v1/reports/student/{self.student.id}?include_content=true", headers=self._headers(self.admin)).get_json()["data"]
        self.assertEqual(listing["items"][0]["content"]["meal_statistics"], content["meal_statistics"])

    def test_meals_respect_location_and_configured_slots(self):
        from app.services.report_meals import report_meal_statistics
        self._consume("2026-07-06T07:00", channel="小卖部")
        self._consume("2026-07-06T09:45", channel="食堂")
        self._consume("2026-07-06T10:15", channel="食堂")
        cfg = {"APP_TIMEZONE": "Asia/Shanghai", "CONSUMPTION_ENABLED_TRANSACTION_LOCATION_IDS": ["食堂"],
               "MEAL_SLOTS": [{"key": "breakfast", "label": "早餐", "start": "09:30", "end": "10:30"}]}
        stats = report_meal_statistics([self.student.id], date(2026, 7, 6), date(2026, 7, 10), cfg)
        self.assertEqual(stats[self.student.id], {"meal_count": 1, "breakfast_count": 1})

    def test_report_list_fetches_all_period_consumption_in_one_query(self):
        from app.services.report_meals import report_period_meal_statistics
        rows = [(self.student.id, datetime(2026, 7, 6, 7)), (self.student.id, datetime(2026, 7, 13, 12))]
        with patch("app.services.report_meals.apply_enabled_transaction_location_filter") as filtered:
            filtered.return_value.with_entities.return_value.all.return_value = rows
            stats = report_period_meal_statistics([
                (1, self.student.id, date(2026, 7, 6), date(2026, 7, 10)),
                (2, self.student.id, date(2026, 7, 13), date(2026, 7, 17)),
            ])
            self.assertEqual(filtered.call_count, 1)
        self.assertEqual(stats[1], {"meal_count": 1, "breakfast_count": 1})
        self.assertEqual(stats[2], {"meal_count": 1, "breakfast_count": 0})

    def test_meals_convert_aware_times_to_local_day_and_breakfast(self):
        from app.services.report_meals import report_meal_statistics
        rows = [(self.student.id, datetime(2026, 7, 5, 23, 0, tzinfo=timezone.utc)),
                (self.student.id, datetime(2026, 7, 6, 0, 0, tzinfo=timezone.utc))]
        with patch("app.services.report_meals.apply_enabled_transaction_location_filter") as filtered:
            filtered.return_value.with_entities.return_value.all.return_value = rows
            stats = report_meal_statistics([self.student.id], date(2026, 7, 6), date(2026, 7, 6))
        self.assertEqual(stats[self.student.id], {"meal_count": 1, "breakfast_count": 1})

    def test_no_consumption_is_zero_and_parent_cannot_export_class(self):
        self._personal_report()
        parent = User(username="parent", name="家长", role=RoleEnum.parent, student_ids=[self.student.id])
        db.session.add(parent)
        db.session.commit()
        response = self.client.get(f"/api/v1/reports/student/{self.student.id}/latest", headers=self._headers(parent))
        self.assertEqual(response.get_json()["data"]["content"]["meal_statistics"], {"meal_count": 0, "breakfast_count": 0})
        self.assertEqual(self._create_export(parent).status_code, 403)
        with patch("app.services.report_exports.render_personal_png", return_value=b"png"):
            response = self.client.get(f"/api/v1/reports/{Report.query.first().id}/export.png", headers=self._headers(parent))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/png")

    def test_v2_snapshot_does_not_change_when_consumption_arrives_later(self):
        from app.services.nutrition_service import NutritionService
        self._consume("2026-07-06T12:00")
        content = NutritionService().generate_personal_report(self.student.id, date(2026, 7, 6), date(2026, 7, 10))
        self._add_report(ReportTypeEnum.personal_weekly, self.student.id, content)
        self._consume("2026-07-07T07:00")
        response = self.client.get(f"/api/v1/reports/student/{self.student.id}/latest", headers=self._headers(self.admin))
        returned = response.get_json()["data"]["content"]
        self.assertEqual(returned["meal_statistics"]["meal_count"], 1)
        self.assertEqual(returned["meal_statistics"], content["meal_statistics"])
        self.assertIn("日均营养供给", returned["presentation_html"])
        self.assertNotIn("综合评分", returned["presentation_html"])
        self.assertIn("儿童青少年“五健”促进行动计划", returned["presentation_html"])

    def test_previews_reuse_font_urls_without_subsetting_per_student(self):
        from app.services.nutrition_service import NutritionService

        content = NutritionService().generate_personal_report(self.student.id, date(2026, 7, 6), date(2026, 7, 10))
        self._add_report(ReportTypeEnum.personal_weekly, self.student.id, content)
        with patch("app.services.report_presentation._font_css", side_effect=AssertionError("Preview must not subset fonts")):
            response = self.client.get(f"/api/v1/reports/student/{self.student.id}/latest", headers=self._headers(self.admin))
            self.assertEqual(response.status_code, 200)
            returned = response.get_json()["data"]["content"]
            for key in ("presentation_html", "brief_presentation_html"):
                self.assertIn("/api/v1/reports/fonts/noto-sans-sc-400.woff2?v=", returned[key])
                self.assertIn("font-display:swap", returned[key])
                self.assertNotIn("data:font/woff2;base64,", returned[key])
            detail = self.client.get(f"/api/v1/reports/{Report.query.one().id}", headers=self._headers(self.admin))
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.get_json()["data"]["content"]["presentation_html"], returned["presentation_html"])

    def test_preview_fonts_are_public_cacheable_and_support_conditional_requests(self):
        response = self.client.get("/api/v1/reports/fonts/noto-sans-sc-400.woff2?v=test")
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "font/woff2")
        self.assertTrue(response.data.startswith(b"wOF2"))
        self.assertTrue(response.cache_control.public)
        self.assertEqual(response.cache_control.max_age, 31536000)
        conditional = self.client.get("/api/v1/reports/fonts/noto-sans-sc-400.woff2?v=test", headers={"If-None-Match": response.headers["ETag"]})
        self.addCleanup(conditional.close)
        self.assertEqual(conditional.status_code, 304)
        for name in ("NotoSansSC-OFL.txt", "personal.html", "unknown.woff2"):
            self.assertEqual(self.client.get(f"/api/v1/reports/fonts/{name}").status_code, 404)

    def test_unknown_slots_are_reported_separately_from_known_meals(self):
        from app.services.report_meals import report_meal_snapshots
        self._consume("2026-07-06T15:00")
        self._consume("2026-07-06T15:10")
        self._consume("2026-07-06T12:00")
        stats = report_meal_snapshots([self.student.id], date(2026, 7, 6), date(2026, 7, 6))[self.student.id]
        self.assertEqual(stats["meal_count"], 1)
        self.assertEqual(stats["unknown_count"], 1)
        self.assertEqual(stats["recorded_days"], 1)

    def test_existing_modern_report_gets_student_reference_without_rewriting_snapshot(self):
        from app.services.nutrition_service import NutritionService
        content = NutritionService().generate_personal_report(self.student.id, date(2026, 7, 6), date(2026, 7, 10))
        content.pop("student_profile")
        content["nutrition_assessment"] = {"level": "D", "label": "重点关注", "passed_count": 0}
        content["meal_projection"]["avg_nutrients"] = {
            "calories": 2900, "protein": 75, "fat": 80, "cholesterol": 350, "carbohydrate": 400, "fiber": 25,
        }
        self._add_report(ReportTypeEnum.personal_weekly, self.student.id, content)
        self.student.age = 15
        self.student.gender = "男"
        db.session.commit()
        response = self.client.get(f"/api/v1/reports/student/{self.student.id}/latest", headers=self._headers(self.admin))
        self.assertEqual(response.status_code, 200)
        returned = response.get_json()["data"]["content"]
        self.assertEqual(returned["student_profile"]["age"], 15)
        self.assertEqual(returned["nutrition_assessment"]["level"], "A")
        self.assertEqual(returned["nutrition_assessment"]["score"], 100)
        self.assertEqual(returned["nutrition_assessment"]["version"], 2)
        self.assertIn("A级 · 达标良好", returned["presentation_html"])
        self.assertNotIn("student_profile", Report.query.one().content)
        self.assertEqual(Report.query.one().content["nutrition_assessment"]["level"], "D")

    def test_old_report_completes_missing_meal_and_nutrition_without_rewriting_snapshot(self):
        from app.models import NutritionLog
        from app.services.nutrition_service import NutritionService
        content = NutritionService().generate_personal_report(self.student.id, date(2026, 7, 6), date(2026, 7, 10))
        content["meal_statistics"].pop("completion_version")
        self._add_report(ReportTypeEnum.personal_weekly, self.student.id, content)
        db.session.add(NutritionLog(student_id=self.student.id, log_date=date(2026, 7, 6), meal_count=1,
                                    nutrient_totals={"_quality_version": 2, "_meal_slots": ["lunch"], "protein": 12}))
        db.session.commit()
        response = self.client.get(f"/api/v1/reports/student/{self.student.id}/latest", headers=self._headers(self.admin))
        self.assertEqual(response.status_code, 200)
        returned = response.get_json()["data"]["content"]
        self.assertEqual(returned["meal_statistics"]["meal_count"], 1)
        self.assertEqual(returned["meal_projection"]["avg_nutrients"]["protein"], 30)
        self.assertEqual(returned["coverage"]["nutrition_days"], 1)
        self.assertEqual(returned["nutrient_sample_counts"]["protein"], 1)
        self.assertEqual(Report.query.one().content["meal_statistics"]["meal_count"], 0)
        self.assertIsNone(Report.query.one().content["meal_projection"]["avg_nutrients"]["protein"])

    def test_export_periods_and_batch_select_latest_for_uniform_period(self):
        first = self._personal_report()
        second = self._personal_report()
        old = Report(report_type=ReportTypeEnum.personal_weekly, target_id=str(self.student.id), period_start=date(2026, 6, 29),
                     period_end=date(2026, 7, 5), content={})
        db.session.add(old)
        db.session.commit()
        response = self.client.get(f"/api/v1/reports/class/{self.class_.id}/export-periods", headers=self._headers(self.teacher))
        self.assertEqual(response.get_json()["data"][0]["period_start"], "2026-07-06")
        response = self._create_export(self.teacher)
        self.assertEqual(response.status_code, 202)
        task = db.session.get(TaskLog, response.get_json()["data"]["id"])
        self.assertEqual(task.meta["students"][0]["report_id"], second.id)
        self.assertNotEqual(first.id, second.id)

    def test_brief_preview_and_download_preserve_stored_report_and_permissions(self):
        report = self._personal_report()
        original = dict(report.content)
        response = self.client.get(f"/api/v1/reports/{report.id}", headers=self._headers(self.teacher))
        content = response.get_json()["data"]["content"]
        self.assertIn("实际用餐次数", content["presentation_html"])
        self.assertIn("营养概览", content["brief_presentation_html"])
        self.assertIn("体育锻炼分析", content["brief_presentation_html"])
        self.assertEqual(db.session.get(Report, report.id).content, original)
        url = f"/api/v1/reports/{report.id}/export.png"
        with patch("app.services.report_exports.render_personal_png", return_value=b"brief-png") as render:
            response = self.client.get(url, query_string={"format": "brief"}, headers=self._headers(self.teacher))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, b"brief-png")
            self.assertEqual(render.call_args.kwargs, {"report_format": "brief"})
            self.assertIn("%E7%AE%80%E6%8A%A5", response.headers["Content-Disposition"])
        self.assertEqual(self.client.get(url + "?format=invalid", headers=self._headers(self.teacher)).status_code, 400)
        outsider = User(username="outsider", name="其他教师", role=RoleEnum.teacher, managed_class_ids=[])
        db.session.add(outsider)
        db.session.commit()
        self.assertEqual(self.client.get(url + "?format=brief", headers=self._headers(outsider)).status_code, 403)
        listing = self.client.get(f"/api/v1/reports/student/{self.student.id}?include_content=true", headers=self._headers(self.teacher))
        self.assertNotIn("brief_presentation_html", listing.get_json()["data"]["items"][0]["content"])

    def test_brief_class_export_propagates_format_to_worker_and_zip(self):
        from app.services.report_exports import artifact_path, build_class_export
        self._personal_report()
        url = f"/api/v1/reports/class/{self.class_.id}/exports"
        data = {"period_start": "2026-07-06", "period_end": "2026-07-10", "format": "brief"}
        with patch("app.tasks.reports.export_class_reports.delay"):
            response = self.client.post(url, json=data, headers=self._headers(self.teacher))
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()["data"]["report_format"], "brief")
        task_id = response.get_json()["data"]["id"]
        with patch("app.services.report_exports.render_personal_png", return_value=b"brief-png") as render:
            build_class_export(task_id)
        self.assertEqual(render.call_args.kwargs, {"report_format": "brief"})
        with zipfile.ZipFile(artifact_path(task_id)) as archive:
            pngs = [name for name in archive.namelist() if name.endswith("_简报.png")]
            self.assertEqual(len(pngs), 1)
            self.assertEqual(archive.read(pngs[0]), b"brief-png")
            self.assertIn(pngs[0], archive.read("导出清单.csv").decode("utf-8-sig"))
        data["format"] = "invalid"
        self.assertEqual(self.client.post(url, json=data, headers=self._headers(self.teacher)).status_code, 400)

    def test_batch_zip_contains_png_and_manifest_missing_and_failed_reports(self):
        from app.services.report_exports import build_class_export, artifact_path
        self._personal_report()
        missing = Student(student_no="S002", name="同名学生", class_id=self.class_.id)
        failing = Student(student_no="S003", name="同名学生", class_id=self.class_.id)
        db.session.add_all([missing, failing])
        db.session.commit()
        self._personal_report(failing)
        response = self._create_export()
        task_id = response.get_json()["data"]["id"]
        with patch("app.services.report_exports.render_personal_png", side_effect=[b"first-png", ValueError("failed")]):
            build_class_export(task_id)
        task = db.session.get(TaskLog, task_id)
        self.assertEqual((task.status, task.success_count, task.invalid_count, task.error_count), ("partial", 1, 1, 1))
        with zipfile.ZipFile(artifact_path(task_id)) as archive:
            pngs = [name for name in archive.namelist() if name.endswith(".png")]
            self.assertEqual(len(pngs), 1)
            self.assertEqual(archive.read(pngs[0]), b"first-png")
            manifest = archive.read("导出清单.csv").decode("utf-8-sig")
            self.assertIn("缺失", manifest)
            self.assertIn("失败", manifest)
        response = self.client.get(f"/api/v1/reports/exports/{task_id}/download", headers=self._headers(self.admin))
        self.assertEqual(response.status_code, 200)
        response.close()
        self.assertEqual(self.client.get(f"/api/v1/reports/exports/{task_id}", headers=self._headers(self.teacher)).status_code, 403)

    def test_export_fails_when_no_reports_or_all_renders_fail(self):
        from app.services.report_exports import build_class_export, artifact_path
        self.assertEqual(self._create_export().status_code, 400)
        self._personal_report()
        response = self._create_export()
        task_id = response.get_json()["data"]["id"]
        with patch("app.services.report_exports.render_personal_png", side_effect=RuntimeError("render failed")):
            build_class_export(task_id)
        self.assertEqual(db.session.get(TaskLog, task_id).status, "failed")
        self.assertFalse(artifact_path(task_id).exists())

    def test_expired_export_is_not_downloadable_and_cleanup_removes_file(self):
        from app.services.report_exports import artifact_path, cleanup_report_exports
        self._personal_report()
        task_id = self._create_export().get_json()["data"]["id"]
        task = db.session.get(TaskLog, task_id)
        task.status = "success"
        task.meta = {**task.meta, "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}
        artifact_path(task_id).write_bytes(b"zip")
        db.session.commit()
        response = self.client.get(f"/api/v1/reports/exports/{task_id}/download", headers=self._headers(self.admin))
        self.assertEqual(response.status_code, 410)
        self.assertEqual(cleanup_report_exports(), 1)
        self.assertFalse(artifact_path(task_id).exists())

    def test_batch_rechecks_class_permission_and_duplicate_delivery_is_ignored(self):
        from app.services.report_exports import build_class_export
        self._personal_report()
        task_id = self._create_export(self.teacher).get_json()["data"]["id"]
        with patch("app.services.report_exports.render_personal_png", return_value=b"png") as render:
            build_class_export(task_id)
            build_class_export(task_id)
            self.assertEqual(render.call_count, 1)
        self.teacher.managed_class_ids = []
        db.session.commit()
        self.assertEqual(self.client.get(f"/api/v1/reports/exports/{task_id}/download", headers=self._headers(self.teacher)).status_code, 403)

    def test_personal_reports_select_type_and_exact_period(self):
        weekly = self._personal_report()
        newer = Report(report_type=ReportTypeEnum.personal_weekly, target_id=str(self.student.id),
                       period_start=date(2026, 7, 13), period_end=date(2026, 7, 17), content=dict(weekly.content))
        monthly = Report(report_type=ReportTypeEnum.personal_monthly, target_id=str(self.student.id),
                         period_start=date(2026, 7, 1), period_end=date(2026, 7, 31), content=dict(weekly.content))
        db.session.add_all([newer, monthly])
        db.session.commit()
        url = f"/api/v1/reports/student/{self.student.id}"
        for params, expected in [({}, newer.id), ({"period": "2026-W28"}, weekly.id),
                                 ({"type": "personal_monthly", "period": "2026-07"}, monthly.id),
                                 ({"type": "personal_monthly", "period": "2026-06"}, None)]:
            with self.subTest(params=params):
                response = self.client.get(f"{url}/latest", query_string=params, headers=self._headers(self.teacher))
                self.assertEqual(response.status_code, 200)
                data = response.get_json()["data"]
                self.assertEqual(data["id"] if data else None, expected)
                if data and data["report_type"] == "personal_monthly":
                    self.assertIn("月报", data["content"]["presentation_html"])
        response = self.client.get(url, query_string={"type": "personal_monthly", "period": "2026-07"}, headers=self._headers(self.teacher))
        self.assertEqual([r["id"] for r in response.get_json()["data"]["items"]], [monthly.id])

    def test_group_monthly_reports_preserve_scope_permissions(self):
        for scope, target, user in [("class", self.class_.id, self.teacher), ("grade", self.grade.id, self.grade_leader),
                                    ("campus", self.campus.id, self.admin)]:
            with self.subTest(scope=scope):
                report = Report(report_type=ReportTypeEnum(f"{scope}_monthly"), target_id=str(target),
                                period_start=date(2026, 7, 1), period_end=date(2026, 7, 31), content=self._group_content(scope, target))
                db.session.add(report)
                db.session.commit()
                response = self.client.get(f"/api/v1/reports/{scope}/{target}/latest", query_string={
                    "type": f"{scope}_monthly", "period": "2026-07",
                }, headers=self._headers(user))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()["data"]["id"], report.id)
                self.assertEqual(self.client.get(f"/api/v1/reports/{report.id}", headers=self._headers(user)).status_code, 200)
                missing = self.client.get(f"/api/v1/reports/{scope}/{target}/latest", query_string={
                    "type": f"{scope}_monthly", "period": "2026-06",
                }, headers=self._headers(user))
                self.assertIsNone(missing.get_json()["data"])
                if scope != "class":
                    self.assertEqual(self.client.get(f"/api/v1/reports/{report.id}", headers=self._headers(self.teacher)).status_code, 403)
        self.teacher.managed_class_ids = []
        db.session.commit()
        self.assertEqual(self.client.get(f"/api/v1/reports/class/{self.class_.id}/latest?type=class_monthly", headers=self._headers(self.teacher)).status_code, 403)

    def test_group_png_export_matches_preview_snapshot_and_checks_scope_access(self):
        for cadence in ("weekly", "monthly"):
            for scope, target, user in [("class", self.class_.id, self.teacher), ("grade", self.grade.id, self.grade_leader),
                                        ("campus", self.campus.id, self.admin)]:
                with self.subTest(scope=scope, cadence=cadence):
                    content = {**self._group_content(scope, target), "scope_name": "测试范围", "student_count": 1,
                               "health_summary": {"participating_students": 1}, "period_comparison": {"has_report": False}}
                    report = Report(report_type=ReportTypeEnum(f"{scope}_{cadence}"), target_id=str(target),
                                    period_start=date(2026, 7, 6) if cadence == "weekly" else date(2026, 7, 1),
                                    period_end=date(2026, 7, 10) if cadence == "weekly" else date(2026, 7, 31), content=content)
                    db.session.add(report)
                    db.session.commit()
                    preview = self.client.get(f"/api/v1/reports/{report.id}", headers=self._headers(user))
                    self.assertEqual(preview.status_code, 200)
                    preview_content = preview.get_json()["data"]["content"]
                    self.assertIn("体质健康月报" if cadence == "monthly" else "体质健康周报", preview_content["presentation_html"])
                    with patch("app.services.report_exports.render_group_png", return_value=b"group-png") as render:
                        response = self.client.get(f"/api/v1/reports/{report.id}/export.png", headers=self._headers(user))
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.data, b"group-png")
                        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
                        self.assertEqual(render.call_args.args[0], {key: value for key, value in preview_content.items() if key != "presentation_html"})
                        if scope != "class":
                            render.reset_mock()
                            denied = self.client.get(f"/api/v1/reports/{report.id}/export.png", headers=self._headers(self.teacher))
                            self.assertEqual(denied.status_code, 403)
                            render.assert_not_called()
                    self.assertEqual(db.session.get(Report, report.id).content, content)
        self.teacher.managed_class_ids = []
        db.session.commit()
        class_report = Report.query.filter_by(report_type=ReportTypeEnum.class_weekly).first()
        with patch("app.services.report_exports.render_group_png") as render:
            response = self.client.get(f"/api/v1/reports/{class_report.id}/export.png", headers=self._headers(self.teacher))
            self.assertEqual(response.status_code, 403)
            render.assert_not_called()

    def test_report_queries_reject_mismatched_types_and_invalid_periods(self):
        for path, params in [(f"student/{self.student.id}/latest", {"type": "class_monthly"}),
                             (f"student/{self.student.id}", {"period": "2021-W53"}),
                             (f"class/{self.class_.id}/latest", {"type": "class_monthly", "period": "2026-W28"}),
                             (f"class/{self.class_.id}", {"type": "class_monthly", "period": "2026-13"})]:
            with self.subTest(path=path, params=params):
                self.assertEqual(self.client.get(f"/api/v1/reports/{path}", query_string=params, headers=self._headers(self.admin)).status_code, 400)

    def test_generation_normalizes_periods_before_enqueuing(self):
        with patch("app.tasks.reports.generate_all_reports.delay") as delay:
            for scope in ("personal", "class", "grade", "campus"):
                response = self.client.post("/api/v1/reports/generate", json={"type": f"{scope}_monthly", "period": "2024-02"}, headers=self._headers(self.admin))
                self.assertEqual(response.status_code, 200)
                delay.assert_called_with(f"{scope}_monthly", "2024-02-01", "2024-02-29", overwrite=False, task_log_id=response.get_json()["data"]["id"])
            for scope in ("personal", "class", "grade", "campus"):
                response = self.client.post("/api/v1/reports/generate", json={"type": f"{scope}_weekly", "period": "2020-W53"}, headers=self._headers(self.admin))
                self.assertEqual(response.status_code, 200)
                delay.assert_called_with(f"{scope}_weekly", "2020-12-28", "2021-01-01", overwrite=False, task_log_id=response.get_json()["data"]["id"])

    def test_generation_passes_explicit_overwrite_option(self):
        with patch("app.tasks.reports.generate_all_reports.delay") as delay:
            for overwrite in (False, True):
                response = self.client.post("/api/v1/reports/generate", json={
                    "type": "class_monthly", "period": "2024-02", "overwrite": overwrite,
                }, headers=self._headers(self.admin))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()["data"]["overwrite"], overwrite)
                delay.assert_called_with("class_monthly", "2024-02-01", "2024-02-29", overwrite=overwrite, task_log_id=response.get_json()["data"]["id"])

    def _submit_generation_batch(self, report_type="class_weekly", overwrite=True):
        with patch("app.tasks.reports.generate_all_reports.delay"):
            response = self.client.post("/api/v1/reports/generate", json={
                "type": report_type, "period": "2026-07" if report_type.endswith("monthly") else "2026-W28", "overwrite": overwrite,
            }, headers=self._headers(self.admin))
        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual((data["status"], data["processed_count"]), ("pending", 0))
        return db.session.get(TaskLog, data["id"])

    def _dispatch_generation_batch(self, batch):
        from app.tasks.reports import generate_all_reports
        meta = batch.meta
        with patch("app.tasks.reports._generate_batch_report.delay") as enqueue:
            generate_all_reports.run(meta["report_type"], meta["period_start"], meta["period_end"], overwrite=meta["overwrite"], task_log_id=batch.id)
        return [call.args for call in enqueue.call_args_list]

    def test_overwrite_progress_counts_completed_reports_and_deduplicates_callbacks(self):
        from app.tasks.reports import _generate_batch_report
        from app.services.report_generation import generation_task_payload, record_generation_result

        self._personal_report()
        second = Student(student_no="S002", name="第二位学生", class_id=self.class_.id)
        db.session.add(second)
        db.session.commit()
        self._personal_report(second)
        original_times = [report.created_at for report in Report.query.order_by(Report.id).all()]
        batch = self._submit_generation_batch("personal_weekly")
        jobs = self._dispatch_generation_batch(batch)
        self.assertEqual((batch.status, batch.total_count, batch.success_count), ("running", 2, 0))
        with (
            patch("app.services.nutrition_service.NutritionService.generate_personal_report", return_value={"student_name": "新报告"}) as generate,
            patch("app.tasks.reports.push_report_task.delay"),
        ):
            _generate_batch_report.run(*jobs[0])
            self.assertEqual((batch.status, batch.success_count), ("running", 1))
            self.assertEqual(generation_task_payload(batch)["processed_count"], 1)
            _generate_batch_report.run(*jobs[0])
            record_generation_result(batch.id, jobs[0][1], "success")
            self.assertEqual(batch.success_count, 1)
            self.assertEqual(generate.call_count, 1)
            _generate_batch_report.run(*jobs[1])
        self.assertEqual((batch.status, batch.success_count, batch.error_count), ("success", 2, 0))
        self.assertIsNotNone(batch.finished_at)
        self.assertEqual(Report.query.count(), 2)
        for report, old_time in zip(Report.query.order_by(Report.id).all(), original_times):
            self.assertGreater(report.created_at, old_time)

    def test_tracked_batches_generate_weekly_and_monthly_reports_for_all_scopes(self):
        from app.tasks.reports import _generate_batch_report
        for cadence in ("weekly", "monthly"):
            for scope in ("personal", "class", "grade", "campus"):
                with self.subTest(scope=scope, cadence=cadence):
                    batch = self._submit_generation_batch(f"{scope}_{cadence}")
                    jobs = self._dispatch_generation_batch(batch)
                    self.assertEqual(len(jobs), 1)
                    with patch("app.tasks.reports.push_report_task.delay"):
                        _generate_batch_report.run(*jobs[0])
                    self.assertEqual((batch.status, batch.success_count, batch.invalid_count, batch.error_count), ("success", 1, 0, 0))

    def test_batch_progress_distinguishes_existing_and_empty_reports(self):
        from app.tasks.reports import _generate_batch_report
        self._personal_report()
        batch = self._submit_generation_batch("personal_weekly", overwrite=False)
        jobs = self._dispatch_generation_batch(batch)
        with patch("app.services.nutrition_service.NutritionService.generate_personal_report") as generate:
            _generate_batch_report.run(*jobs[0])
            generate.assert_not_called()
        self.assertEqual((batch.status, batch.success_count, batch.invalid_count), ("success", 0, 1))
        batch = self._submit_generation_batch()
        jobs = self._dispatch_generation_batch(batch)
        with patch("app.services.nutrition_service.NutritionService.generate_group_report", return_value=None):
            _generate_batch_report.run(*jobs[0])
        self.assertEqual((batch.status, batch.success_count, batch.invalid_count), ("success", 0, 1))

    def test_batch_continues_after_report_failure_and_reports_partial_completion(self):
        from app.tasks.reports import _generate_batch_report
        db.session.add(Student(student_no="S002", name="第二位学生", class_id=self.class_.id))
        db.session.commit()
        batch = self._submit_generation_batch("personal_weekly")
        jobs = self._dispatch_generation_batch(batch)
        with (
            patch("app.services.nutrition_service.NutritionService.generate_personal_report", side_effect=[RuntimeError("数据暂不可用"), {"student_name": "新报告"}]),
            patch("app.tasks.reports.push_report_task.delay"),
        ):
            _generate_batch_report.run(*jobs[0])
            self.assertEqual((batch.status, batch.success_count, batch.error_count), ("running", 0, 1))
            _generate_batch_report.run(*jobs[1])
        self.assertEqual((batch.status, batch.success_count, batch.error_count), ("partial", 1, 1))
        self.assertEqual(batch.error_message, "数据暂不可用")
        self.assertIsNotNone(batch.finished_at)

    def test_batch_finishes_for_no_targets_and_dispatch_failures(self):
        from app.tasks.reports import generate_all_reports
        batch = self._submit_generation_batch()
        with patch("app.tasks.reports._generate_batch_report.delay", side_effect=RuntimeError("分发失败")):
            generate_all_reports.run("class_weekly", "2026-07-06", "2026-07-10", overwrite=True, task_log_id=batch.id)
        self.assertEqual((batch.status, batch.error_count), ("failed", 1))
        self.assertIsNotNone(batch.finished_at)
        self.student.is_active = False
        db.session.commit()
        batch = self._submit_generation_batch("personal_weekly")
        self.assertEqual(self._dispatch_generation_batch(batch), [])
        self.assertEqual((batch.status, batch.total_count), ("success", 0))
        self.assertIsNotNone(batch.finished_at)

    def test_generation_task_submission_failure_is_persisted(self):
        with patch("app.tasks.reports.generate_all_reports.delay", side_effect=RuntimeError("broker unavailable")):
            response = self.client.post("/api/v1/reports/generate", json={"type": "class_weekly", "period": "2026-W28"}, headers=self._headers(self.admin))
        self.assertEqual(response.status_code, 503)
        task = TaskLog.query.filter_by(task_type="report_gen").one()
        self.assertEqual(task.status, "failed")
        self.assertIsNotNone(task.finished_at)

    def test_generation_tasks_remain_available_after_reload_and_require_admin(self):
        batch = self._submit_generation_batch()
        db.session.add(TaskLog(task_type="class_report_export", status="pending"))
        db.session.add(TaskLog(task_type="report_gen", status="running"))
        db.session.commit()
        response = self.client.get("/api/v1/reports/generation-tasks", headers=self._headers(self.admin))
        self.assertEqual(response.status_code, 200)
        tasks = response.get_json()["data"]
        self.assertEqual([task["id"] for task in tasks], [batch.id])
        self.assertTrue(tasks[0]["overwrite"])
        self.assertNotIn("meta", tasks[0])
        self.assertEqual(self.client.get("/api/v1/reports/generation-tasks", headers=self._headers(self.teacher)).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/reports/generation-tasks").status_code, 401)

    def test_saved_nutrient_gains_take_effect_when_overwriting_each_report_scope(self):
        from app.services.report_nutrient_gains import REPORT_GAIN_KEYS
        from app.services.runtime_config import persist_runtime_overrides
        from app.tasks.reports import _generate_batch_report

        values = {"calories": 800, "protein": 24, "carbohydrate": 100, "fiber": 8, "fat": 20, "calcium": 200}
        gains = {"calories": 1.5, "protein": 2, "carbohydrate": 1.2, "fiber": 1.25, "fat": .75, "calcium": 2.5}
        db.session.add(NutritionLog(student_id=self.student.id, log_date=date(2026, 7, 6), meal_count=1, nutrient_totals={
            **values, "_quality_version": 3, "_meal_slots": ["lunch"], "_meals": [{"slot": "lunch", "nutrients": values}],
        }))
        db.session.commit()
        for scope in ("personal", "class", "grade", "campus"):
            with self.subTest(scope=scope), patch("app.tasks.reports.push_report_task.delay"):
                persist_runtime_overrides(self.app.config, {"REPORT_NUTRIENT_GAINS": dict.fromkeys(REPORT_GAIN_KEYS, 1)})
                batch = self._submit_generation_batch(f"{scope}_weekly")
                for job in self._dispatch_generation_batch(batch):
                    _generate_batch_report.run(*job)
                report = Report.query.filter_by(report_type=f"{scope}_weekly").one()
                original = dict(report.content["avg_nutrients"])
                original_time = report.created_at

                persist_runtime_overrides(self.app.config, {"REPORT_NUTRIENT_GAINS": gains})
                skipped = self._submit_generation_batch(f"{scope}_weekly", overwrite=False)
                for job in self._dispatch_generation_batch(skipped):
                    _generate_batch_report.run(*job)
                self.assertEqual((skipped.status, skipped.invalid_count), ("success", 1))
                self.assertEqual(report.content["avg_nutrients"], original)
                self.assertEqual(report.created_at, original_time)

                updated = self._submit_generation_batch(f"{scope}_weekly", overwrite=True)
                for job in self._dispatch_generation_batch(updated):
                    _generate_batch_report.run(*job)
                self.assertEqual((updated.status, updated.success_count), ("success", 1))
                db.session.refresh(report)
                self.assertEqual(report.content["report_nutrient_gains"], gains)
                for key, factor in gains.items():
                    self.assertEqual(report.content["avg_nutrients"][key], round(original[key] * factor, 1))
                self.assertGreater(report.created_at, original_time)

    def test_previews_and_exports_show_actual_generation_time_without_mutating_content(self):
        from app.services.report_presentation import render_group_html, render_personal_html
        report = self._personal_report()
        original = dict(report.content)
        report.created_at = datetime(2026, 10, 28, 17, 2, 3, tzinfo=timezone.utc)
        db.session.commit()
        response = self.client.get(f"/api/v1/reports/student/{self.student.id}/latest", headers=self._headers(self.teacher))
        content = response.get_json()["data"]["content"]
        for key in ("presentation_html", "brief_presentation_html"):
            self.assertIn("生成日期", content[key])
            self.assertIn("2026-10-29 01:02:03", content[key])
        with patch("app.services.report_exports.render_personal_png", return_value=b"png") as render:
            self.client.get(f"/api/v1/reports/{report.id}/export.png", headers=self._headers(self.teacher))
        self.assertEqual(render.call_args.args[0]["generated_at"], content["generated_at"])
        self.assertEqual(db.session.get(Report, report.id).content, original)
        # Both modern detailed and group templates use the same timezone and timestamp.
        from app.services.nutrition_service import NutritionService
        modern = NutritionService().generate_personal_report(self.student.id, report.period_start, report.period_end)
        with self.app.test_request_context():
            for html in (render_personal_html({**modern, "generated_at": content["generated_at"]}, embed_fonts=False),
                         render_group_html({**self._group_content("class", self.class_.id), "generated_at": content["generated_at"]}, embed_fonts=False)):
                self.assertIn("生成日期", html)
                self.assertIn("2026-10-29 01:02:03", html)

    def test_generation_rejects_invalid_overwrite_option_without_scheduling(self):
        with patch("app.tasks.reports.generate_all_reports.delay") as delay:
            for overwrite in ("false", "true", 0, 1, None, [], {}):
                with self.subTest(overwrite=overwrite):
                    response = self.client.post("/api/v1/reports/generate", json={
                        "type": "personal_weekly", "period": "2026-W28", "overwrite": overwrite,
                    }, headers=self._headers(self.admin))
                    self.assertEqual(response.status_code, 400)
            delay.assert_not_called()

    def test_generation_rejects_invalid_dates_without_scheduling(self):
        with patch("app.tasks.reports.generate_all_reports.delay") as delay:
            for data in [{"type": "personal_monthly", "period": "2026-13"}, {"type": "personal_weekly", "period": "2021-W53"},
                         {"type": "personal_weekly", "period_start": "bad", "period_end": "2026-07-10"},
                         {"type": "personal_weekly", "period_start": "2026-07-06", "period_end": "2026-07-11"},
                         {"type": "class_weekly", "period_start": "2026-07-06", "period_end": "2026-07-12"},
                         {"type": "class_monthly", "period_start": "2026-07-01", "period_end": "2026-07-30"},
                         {"type": "personal_weekly", "period_start": "2026-07-06"}, {"type": "unsupported"}]:
                with self.subTest(data=data):
                    self.assertEqual(self.client.post("/api/v1/reports/generate", json=data, headers=self._headers(self.admin)).status_code, 400)
            self.assertEqual(self.client.post("/api/v1/reports/generate", json={"type": "personal_monthly"}, headers=self._headers(self.teacher)).status_code, 403)
            delay.assert_not_called()

    def test_weekly_generation_excludes_weekend_nutrition_meals_and_trends_for_all_scopes(self):
        from app.services.report_periods import parse_report_period
        from app.tasks.reports import _generate_personal_report, _generate_class_report, _generate_group_report

        monday, friday = parse_report_period("personal_weekly", "2026-W28")
        for offset in range(7):
            day = monday + timedelta(days=offset)
            db.session.add(NutritionLog(
                student_id=self.student.id, log_date=day, meal_count=1,
                nutrient_totals={"protein": 30 if offset < 5 else 300, "_quality_version": 2, "_meal_slots": ["lunch"]},
            ))
            self._consume(f"{day.isoformat()}T12:00")
        db.session.commit()

        with patch("app.tasks.reports.push_report_task.delay"):
            _generate_personal_report.run(self.student.id, monday.isoformat(), friday.isoformat())
        _generate_class_report.run(self.class_.id, monday.isoformat(), friday.isoformat())
        _generate_group_report.run("grade", self.grade.id, monday.isoformat(), friday.isoformat(), "grade_weekly")
        _generate_group_report.run("campus", self.campus.id, monday.isoformat(), friday.isoformat(), "campus_weekly")

        self.assertEqual(Report.query.count(), 4)
        for report in Report.query.all():
            with self.subTest(report_type=report.report_type):
                content = report.content
                self.assertEqual((report.period_start, report.period_end), (monday, friday))
                self.assertEqual(content["avg_nutrients"]["protein"], 30)
                self.assertEqual(content["meal_projection"]["avg_nutrients"]["protein"], 75)
                self.assertEqual([day["date"] for day in content["daily_series"]],
                                 [(monday + timedelta(days=offset)).isoformat() for offset in range(5)])
                if report.report_type == ReportTypeEnum.personal_weekly:
                    self.assertEqual(content["meal_statistics"]["meal_count"], 5)
                    self.assertEqual(content["coverage"]["period_days"], 5)
                    self.assertEqual(content["coverage"]["nutrition_days"], 5)
                    self.assertEqual(content["dining_features"]["items"][0]["evidence"]["meal_counts"], {"lunch": 5})
                else:
                    self.assertEqual(content["meal_count"], 5)
                    self.assertEqual(content["meal_distribution"], {"lunch": 5})

    def test_generation_dispatches_weekly_and_monthly_tasks_for_each_scope(self):
        from app.tasks.reports import generate_all_reports
        with (
            patch("app.tasks.reports._generate_personal_report.delay") as personal,
            patch("app.tasks.reports._generate_class_report.delay") as class_task,
            patch("app.tasks.reports._generate_group_report.delay") as group,
        ):
            for overwrite in (False, True):
                for cadence, start, end in [("weekly", "2026-07-06", "2026-07-10"), ("monthly", "2026-07-01", "2026-07-31")]:
                    generate_all_reports.run(f"personal_{cadence}", start, end, overwrite=overwrite)
                    personal.assert_called_with(self.student.id, start, end, f"personal_{cadence}", overwrite=overwrite)
                    generate_all_reports.run(f"class_{cadence}", start, end, overwrite=overwrite)
                    class_task.assert_called_with(self.class_.id, start, end, f"class_{cadence}", overwrite=overwrite)
                    for scope, target in [("grade", self.grade.id), ("campus", self.campus.id)]:
                        generate_all_reports.run(f"{scope}_{cadence}", start, end, overwrite=overwrite)
                        group.assert_called_with(scope, target, start, end, f"{scope}_{cadence}", overwrite=overwrite)
                generate_all_reports.run("school_monthly", "2026-07-01", "2026-07-31", overwrite=overwrite)
                personal.assert_called_with(self.student.id, "2026-07-01", "2026-07-31", "personal_monthly", overwrite=overwrite)
                class_task.assert_called_with(self.class_.id, "2026-07-01", "2026-07-31", "class_monthly", overwrite=overwrite)

    def test_generation_skips_existing_reports_or_overwrites_in_place_for_all_scopes(self):
        from app.tasks.reports import _generate_personal_report, _generate_class_report, _generate_group_report
        scopes = [
            ("personal", self.student.id, _generate_personal_report),
            ("class", self.class_.id, _generate_class_report),
            ("grade", self.grade.id, _generate_group_report),
            ("campus", self.campus.id, _generate_group_report),
        ]
        for cadence, start, end in [("weekly", "2026-07-06", "2026-07-10"), ("monthly", "2026-07-01", "2026-07-31")]:
            for scope, target, task in scopes:
                with self.subTest(scope=scope, cadence=cadence):
                    report_type = f"{scope}_{cadence}"
                    original_time = datetime(2026, 7, 1, tzinfo=timezone.utc)
                    report = Report(
                        report_type=ReportTypeEnum(report_type), target_id=str(target),
                        period_start=date.fromisoformat(start), period_end=date.fromisoformat(end),
                        content={"original": True}, summary="原报告", push_status="sent",
                        pushed_at=original_time, push_retry_count=2, created_at=original_time,
                    )
                    db.session.add(report)
                    db.session.commit()
                    report_id = report.id
                    count = Report.query.count()
                    args = (target, start, end, report_type) if scope in ("personal", "class") else (scope, target, start, end, report_type)
                    content = {"student_name": "更新学生", "scope_type": scope, "data_coverage_rate": 90}
                    method = "generate_personal_report" if scope == "personal" else "generate_group_report"
                    with (
                        patch(f"app.services.nutrition_service.NutritionService.{method}", return_value=content) as generate,
                        patch("app.tasks.reports.push_report_task.delay") as push,
                    ):
                        self.assertEqual(task.run(*args), report_id)
                        generate.assert_not_called()
                        push.assert_not_called()
                        self.assertEqual(report.content, {"original": True})
                        self.assertEqual((report.summary, report.push_status, report.push_retry_count), ("原报告", "sent", 2))
                        self.assertIsNotNone(report.pushed_at)
                        self.assertEqual(task.run(*args, overwrite=True), report_id)
                        generate.assert_called_once_with(*((target,) if scope == "personal" else (scope, target)), date.fromisoformat(start), date.fromisoformat(end))
                        if scope == "personal":
                            push.assert_called_once_with(report_id)
                        else:
                            push.assert_not_called()
                    db.session.refresh(report)
                    self.assertEqual(Report.query.count(), count)
                    self.assertEqual(report.content, content)
                    self.assertNotEqual(report.summary, "原报告")
                    self.assertGreater(report.created_at.replace(tzinfo=timezone.utc), original_time)
                    self.assertEqual((report.push_status, report.push_retry_count), ("pending", 0))
                    self.assertIsNone(report.pushed_at)

    def test_overwrite_only_changes_matching_target_type_and_period(self):
        from app.tasks.reports import _generate_personal_report
        report = self._personal_report()
        other_student = Student(student_no="S002", name="另一学生", class_id=self.class_.id)
        db.session.add(other_student)
        db.session.flush()
        untouched = [
            Report(report_type=report.report_type, target_id=str(other_student.id), period_start=report.period_start, period_end=report.period_end, content={"other": "student"}),
            Report(report_type=report.report_type, target_id=report.target_id, period_start=date(2026, 7, 13), period_end=date(2026, 7, 17), content={"other": "period"}),
            Report(report_type=ReportTypeEnum.personal_monthly, target_id=report.target_id, period_start=date(2026, 7, 1), period_end=date(2026, 7, 31), content={"other": "type"}),
        ]
        db.session.add_all(untouched)
        db.session.commit()
        originals = [dict(item.content) for item in untouched]
        with (
            patch("app.services.nutrition_service.NutritionService.generate_personal_report", return_value={"student_name": "更新报告"}),
            patch("app.tasks.reports.push_report_task.delay"),
        ):
            _generate_personal_report.run(self.student.id, "2026-07-06", "2026-07-10", overwrite=True)
        self.assertEqual(Report.query.count(), 4)
        self.assertEqual([item.content for item in untouched], originals)
        self.assertEqual(report.content, {"student_name": "更新报告"})

    def test_failed_or_empty_regeneration_preserves_original_reports(self):
        from app.tasks.reports import _generate_personal_report, _generate_class_report
        personal = self._personal_report()
        self._add_report(ReportTypeEnum.class_weekly, self.class_.id, {"original": "group"})
        group = Report.query.filter_by(report_type=ReportTypeEnum.class_weekly).one()
        original = dict(personal.content)
        with (
            patch("app.services.nutrition_service.NutritionService.generate_personal_report", side_effect=RuntimeError("failed")),
            patch("app.tasks.reports.push_report_task.delay") as push,
        ):
            with self.assertRaises(RuntimeError):
                _generate_personal_report.run(self.student.id, "2026-07-06", "2026-07-10", overwrite=True)
            push.assert_not_called()
        with patch("app.services.nutrition_service.NutritionService.generate_group_report", return_value=None):
            self.assertIsNone(_generate_class_report.run(self.class_.id, "2026-07-06", "2026-07-10", overwrite=True))
        db.session.expire_all()
        self.assertEqual(personal.content, original)
        self.assertEqual(group.content, {"original": "group"})
        self.assertEqual(Report.query.count(), 2)

    def test_monthly_tasks_persist_monthly_reports(self):
        from app.tasks.reports import _generate_personal_report, _generate_class_report, _generate_group_report
        with patch("app.tasks.reports.push_report_task.delay"):
            _generate_personal_report.run(self.student.id, "2026-07-01", "2026-07-31", "personal_monthly")
        _generate_class_report.run(self.class_.id, "2026-07-01", "2026-07-31", "class_monthly")
        _generate_group_report.run("grade", self.grade.id, "2026-07-01", "2026-07-31", "grade_monthly")
        _generate_group_report.run("campus", self.campus.id, "2026-07-01", "2026-07-31", "campus_monthly")
        self.assertCountEqual([r.report_type.value for r in Report.query.all()], ["personal_monthly", "class_monthly", "grade_monthly", "campus_monthly"])
        self.assertTrue(all(r.period_end == date(2026, 7, 31) for r in Report.query.all()))

    def test_monthly_class_export_selects_monthly_reports(self):
        report = self._personal_report()
        report.report_type = ReportTypeEnum.personal_monthly
        report.period_start, report.period_end = date(2026, 7, 1), date(2026, 7, 31)
        db.session.commit()
        url = f"/api/v1/reports/class/{self.class_.id}"
        response = self.client.get(f"{url}/export-periods?type=personal_monthly", headers=self._headers(self.teacher))
        self.assertEqual(response.get_json()["data"], [{"period_start": "2026-07-01", "period_end": "2026-07-31"}])
        with patch("app.tasks.reports.export_class_reports.delay"):
            response = self.client.post(f"{url}/exports", json={"type": "personal_monthly", "period_start": "2026-07-01", "period_end": "2026-07-31"}, headers=self._headers(self.teacher))
        self.assertEqual(response.status_code, 202)
        task = db.session.get(TaskLog, response.get_json()["data"]["id"])
        self.assertEqual(task.meta["students"][0]["report_id"], report.id)
        self.assertEqual(response.get_json()["data"]["report_type"], "personal_monthly")

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
