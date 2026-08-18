import os
import sys
import types
import unittest
import io
import tempfile
from datetime import date, datetime, timedelta, timezone
from unittest import mock
from unittest.mock import Mock

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

if "celery" not in sys.modules:
    celery_module = types.ModuleType("celery")
    schedules_module = types.ModuleType("celery.schedules")

    class _FakeTaskWrapper:
        def __init__(self, fn):
            self.run = fn
            self.delay = lambda *args, **kwargs: None

        def __call__(self, *args, **kwargs):
            return self.run(*args, **kwargs)

    class _FakeCelery:
        def __init__(self, *args, **kwargs):
            self.conf = {}

        def task(self, *args, **kwargs):
            def decorator(fn):
                return _FakeTaskWrapper(fn)
            return decorator

        def __getattr__(self, name):
            if name == "conf":
                return self.conf
            if name == "Task":
                return object
            raise AttributeError(name)

    def _fake_crontab(*args, **kwargs):
        return {"args": args, "kwargs": kwargs}

    celery_module.Celery = _FakeCelery
    schedules_module.crontab = _fake_crontab
    sys.modules["celery"] = celery_module
    sys.modules["celery.schedules"] = schedules_module

from app import db  # noqa: E402
import app.models  # noqa: F401,E402
from app.api.analysis import bp as analysis_bp, _build_candidate_dishes_for_pipeline  # noqa: E402
from app.models import (  # noqa: E402
    CapturedImage,
    CapturedImageRegion,
    CategoryEnum,
    DailyMenu,
    Dish,
    DishRecognition,
    DishSampleImage,
    ImageStatusEnum,
    MatchResult,
    MatchStatusEnum,
    NutritionLog,
    RegionRecognitionStatusEnum,
    RegionReviewStatusEnum,
    RoleEnum,
    TaskLog,
    User,
)
from app.services.region_candidates import create_region_candidates_from_recognition  # noqa: E402
from app.services.inference_client import InferenceServiceError  # noqa: E402
from app.utils.jwt_utils import generate_token  # noqa: E402


class AnalysisApiTests(unittest.TestCase):
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
        cls.app.register_blueprint(analysis_bp, url_prefix="/api/v1/analysis")
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
        db.session.query(MatchResult).delete()
        db.session.query(DishRecognition).delete()
        db.session.query(CapturedImageRegion).delete()
        db.session.query(DishSampleImage).delete()
        db.session.query(CapturedImage).delete()
        db.session.query(NutritionLog).delete()
        db.session.query(TaskLog).delete()
        db.session.query(DailyMenu).delete()
        db.session.query(Dish).delete()
        db.session.query(User).delete()
        db.session.commit()

        admin = User(
            username="admin",
            name="管理员",
            role=RoleEnum.admin,
            is_active=True,
        )
        db.session.add(admin)
        db.session.commit()
        self.admin_id = admin.id

    def _create_source_image(self, directory: str, filename: str = "source.jpg") -> str:
        from PIL import Image

        path = os.path.join(directory, filename)
        Image.new("RGB", (240, 180), color=(220, 80, 60)).save(path, format="JPEG")
        return path

    def tearDown(self):
        db.session.rollback()

    def _auth_headers(self) -> dict[str, str]:
        token = generate_token(self.admin_id, RoleEnum.admin.value)
        return {"Authorization": f"Bearer {token}"}

    def _create_menu(self, menu_date: date) -> DailyMenu:
        dish = Dish(
            name="红烧肉",
            price=12.0,
            category=CategoryEnum.meat,
            is_active=True,
        )
        db.session.add(dish)
        db.session.flush()
        menu = DailyMenu(
            menu_date=menu_date,
            meal_dish_ids={
                "breakfast": [],
                "lunch": [dish.id],
                "dinner": [],
                "late_night": [],
            },
            is_default=False,
            created_by=self.admin_id,
        )
        db.session.add(menu)
        db.session.commit()
        return menu

    def test_summary_aggregates_date_range(self):
        images = [
            CapturedImage(
                capture_date=date(2026, 4, 1),
                channel_id="manual",
                captured_at=datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
                image_path="/tmp/summary-pending.jpg",
                status=ImageStatusEnum.pending,
                source_video="manual.mp4",
                is_candidate=False,
            ),
            CapturedImage(
                capture_date=date(2026, 4, 2),
                channel_id="manual",
                captured_at=datetime(2026, 4, 2, 12, 0, tzinfo=timezone.utc),
                image_path="/tmp/summary-identified.jpg",
                status=ImageStatusEnum.identified,
                source_video="manual.mp4",
                is_candidate=False,
            ),
            CapturedImage(
                capture_date=date(2026, 4, 3),
                channel_id="manual",
                captured_at=datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc),
                image_path="/tmp/summary-matched.jpg",
                status=ImageStatusEnum.matched,
                source_video="manual.mp4",
                is_candidate=False,
            ),
            CapturedImage(
                capture_date=date(2026, 4, 4),
                channel_id="manual",
                captured_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
                image_path="/tmp/summary-error.jpg",
                status=ImageStatusEnum.error,
                source_video="manual.mp4",
                is_candidate=False,
            ),
            CapturedImage(
                capture_date=date(2026, 4, 8),
                channel_id="manual",
                captured_at=datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc),
                image_path="/tmp/summary-outside.jpg",
                status=ImageStatusEnum.pending,
                source_video="manual.mp4",
                is_candidate=False,
            ),
            CapturedImage(
                capture_date=date(2026, 4, 5),
                channel_id="manual",
                captured_at=datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc),
                image_path="/tmp/summary-queued.jpg",
                status=ImageStatusEnum.queued,
                source_video="manual.mp4",
                is_candidate=False,
            ),
            CapturedImage(
                capture_date=date(2026, 4, 5),
                channel_id="manual",
                captured_at=datetime(2026, 4, 5, 12, 1, tzinfo=timezone.utc),
                image_path="/tmp/summary-processing.jpg",
                status=ImageStatusEnum.processing,
                source_video="manual.mp4",
                is_candidate=False,
            ),
            CapturedImage(
                capture_date=date(2026, 4, 6),
                channel_id="manual",
                captured_at=datetime(2026, 4, 6, 12, 0, tzinfo=timezone.utc),
                image_path="/tmp/summary-retry.jpg",
                status=ImageStatusEnum.retry_wait,
                source_video="manual.mp4",
                is_candidate=False,
            ),
            CapturedImage(
                capture_date=date(2026, 4, 7),
                channel_id="manual",
                captured_at=datetime(2026, 4, 7, 12, 0, tzinfo=timezone.utc),
                image_path="/tmp/summary-invalid.jpg",
                status=ImageStatusEnum.invalid,
                source_video="manual.mp4",
                is_candidate=False,
                recognition_error_code="no_plate_detected",
            ),
        ]
        db.session.add_all(images)
        db.session.flush()
        db.session.add_all([
            DishRecognition(
                image_id=images[1].id,
                dish_name_raw="低置信菜品 A",
                confidence=0.4,
                is_low_confidence=True,
                is_manual=False,
                model_version="test",
            ),
            DishRecognition(
                image_id=images[2].id,
                dish_name_raw="低置信菜品 B",
                confidence=0.45,
                is_low_confidence=True,
                is_manual=False,
                model_version="test",
            ),
            DishRecognition(
                image_id=images[4].id,
                dish_name_raw="范围外菜品",
                confidence=0.3,
                is_low_confidence=True,
                is_manual=False,
                model_version="test",
            ),
        ])
        db.session.add_all([
            TaskLog(
                task_type="ai_recognition",
                task_date=date(2026, 4, 2),
                status="success",
                total_count=2,
                success_count=2,
                error_count=0,
                started_at=datetime(2026, 4, 2, 1, 0, tzinfo=timezone.utc),
                finished_at=datetime(2026, 4, 2, 1, 1, tzinfo=timezone.utc),
                meta={
                    "analysis_duration_seconds": 12.345,
                    "image_processing_duration_seconds": 10.0,
                    "processed_image_count": 2,
                    "avg_image_duration_seconds": 5.0,
                },
            ),
            TaskLog(
                task_type="ai_recognition",
                task_date=date(2026, 4, 3),
                status="partial",
                total_count=2,
                success_count=1,
                error_count=1,
                started_at=datetime(2026, 4, 3, 1, 0, tzinfo=timezone.utc),
                finished_at=datetime(2026, 4, 3, 1, 0, 8, tzinfo=timezone.utc),
                meta={},
            ),
            TaskLog(
                task_type="ai_recognition",
                task_date=date(2026, 4, 4),
                status="failed",
                total_count=2,
                success_count=0,
                error_count=2,
                started_at=datetime(2026, 4, 4, 1, 0, tzinfo=timezone.utc),
                finished_at=datetime(2026, 4, 4, 1, 0, 20, tzinfo=timezone.utc),
                meta={"analysis_duration_seconds": 20.0},
            ),
            TaskLog(
                task_type="ai_recognition",
                task_date=date(2026, 4, 8),
                status="success",
                total_count=1,
                success_count=1,
                error_count=0,
                started_at=datetime(2026, 4, 8, 1, 0, tzinfo=timezone.utc),
                finished_at=datetime(2026, 4, 8, 1, 1, tzinfo=timezone.utc),
                meta={"analysis_duration_seconds": 60.0},
            ),
        ])
        db.session.commit()

        res = self.client.get(
            "/api/v1/analysis/summary?start_date=2026-04-01&end_date=2026-04-07",
            headers=self._auth_headers(),
        )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["data"]["start_date"], "2026-04-01")
        self.assertEqual(payload["data"]["end_date"], "2026-04-07")
        self.assertEqual(payload["data"]["total_images"], 8)
        self.assertEqual(payload["data"]["pending"], 4)
        self.assertEqual(payload["data"]["queued"], 1)
        self.assertEqual(payload["data"]["processing"], 1)
        self.assertEqual(payload["data"]["retry_wait"], 1)
        self.assertEqual(payload["data"]["identified"], 1)
        self.assertEqual(payload["data"]["matched"], 1)
        self.assertEqual(payload["data"]["invalid"], 1)
        self.assertEqual(payload["data"]["error"], 1)
        self.assertEqual(payload["data"]["low_confidence_recognitions"], 2)
        self.assertEqual(payload["data"]["image_analysis_task_count"], 2)
        self.assertEqual(payload["data"]["image_analysis_processed_images"], 4)
        self.assertEqual(payload["data"]["image_analysis_duration_seconds"], 20.345)
        self.assertEqual(payload["data"]["image_analysis_avg_seconds"], 4.5)

    def test_summary_rejects_invalid_date_range(self):
        res = self.client.get(
            "/api/v1/analysis/summary?start_date=2026-04-07&end_date=2026-04-01",
            headers=self._auth_headers(),
        )

        self.assertEqual(res.status_code, 400)
        payload = res.get_json()
        self.assertEqual(payload["code"], 400)
        self.assertEqual(payload["message"], "开始日期不能晚于结束日期")

    def test_list_images_supports_image_ids_filter(self):
        image_a = CapturedImage(
            capture_date=date(2026, 3, 31),
            channel_id="manual",
            captured_at=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc),
            image_path="/tmp/a.jpg",
            status=ImageStatusEnum.pending,
            source_video="manual_a.mp4",
            is_candidate=False,
        )
        image_b = CapturedImage(
            capture_date=date(2026, 3, 31),
            channel_id="manual",
            captured_at=datetime(2026, 3, 31, 12, 1, tzinfo=timezone.utc),
            image_path="/tmp/b.jpg",
            status=ImageStatusEnum.pending,
            source_video="manual_b.mp4",
            is_candidate=True,
        )
        db.session.add_all([image_a, image_b])
        db.session.commit()

        res = self.client.get(
            f"/api/v1/analysis/images?image_ids={image_a.id}",
            headers=self._auth_headers(),
        )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["data"]["total"], 1)
        self.assertEqual(payload["data"]["items"][0]["id"], image_a.id)

    def test_images_include_recognition_price_total(self):
        dish_a = Dish(
            name="红烧肉",
            price=12.5,
            category=CategoryEnum.meat,
            is_active=True,
        )
        dish_b = Dish(
            name="青菜",
            price=4.0,
            category=CategoryEnum.vegetable,
            is_active=True,
        )
        db.session.add_all([dish_a, dish_b])
        db.session.flush()
        image = CapturedImage(
            capture_date=date(2026, 3, 31),
            channel_id="manual",
            captured_at=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc),
            image_path="/tmp/price-total.jpg",
            status=ImageStatusEnum.identified,
            source_video="manual.mp4",
            is_candidate=False,
        )
        db.session.add(image)
        db.session.flush()
        db.session.add_all([
            DishRecognition(
                image_id=image.id,
                dish_id=dish_a.id,
                dish_name_raw=dish_a.name,
                confidence=0.9,
                is_low_confidence=False,
                is_manual=False,
                model_version="test",
            ),
            DishRecognition(
                image_id=image.id,
                dish_id=dish_b.id,
                dish_name_raw=dish_b.name,
                confidence=0.4,
                is_low_confidence=True,
                is_manual=False,
                model_version="test",
            ),
            DishRecognition(
                image_id=image.id,
                dish_name_raw="未知菜品",
                confidence=0.8,
                is_low_confidence=False,
                is_manual=False,
                model_version="test",
            ),
        ])
        db.session.commit()

        list_res = self.client.get(
            f"/api/v1/analysis/images?image_ids={image.id}",
            headers=self._auth_headers(),
        )
        detail_res = self.client.get(
            f"/api/v1/analysis/images/{image.id}",
            headers=self._auth_headers(),
        )

        self.assertEqual(list_res.status_code, 200)
        list_item = list_res.get_json()["data"]["items"][0]
        self.assertEqual(list_item["recognition_price_total"], 12.5)

        self.assertEqual(detail_res.status_code, 200)
        detail_item = detail_res.get_json()["data"]
        self.assertEqual(detail_item["recognition_price_total"], 12.5)
        prices_by_name = {
            item["dish_name_raw"]: item["dish_price"]
            for item in detail_item["recognitions"]
        }
        self.assertEqual(prices_by_name["红烧肉"], 12.5)
        self.assertEqual(prices_by_name["青菜"], 4.0)
        self.assertIsNone(prices_by_name["未知菜品"])

    def test_list_images_supports_candidate_filter(self):
        regular_image = CapturedImage(
            capture_date=date(2026, 3, 31),
            channel_id="manual",
            captured_at=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc),
            image_path="/tmp/regular.jpg",
            status=ImageStatusEnum.pending,
            source_video="manual_regular.mp4",
            is_candidate=False,
        )
        candidate_image = CapturedImage(
            capture_date=date(2026, 3, 31),
            channel_id="manual",
            captured_at=datetime(2026, 3, 31, 12, 1, tzinfo=timezone.utc),
            image_path="/tmp/candidate.jpg",
            status=ImageStatusEnum.pending,
            source_video="manual_candidate.mp4",
            is_candidate=True,
        )
        db.session.add_all([regular_image, candidate_image])
        db.session.commit()

        res = self.client.get(
            "/api/v1/analysis/images?is_candidate=true",
            headers=self._auth_headers(),
        )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["data"]["total"], 1)
        self.assertEqual(payload["data"]["items"][0]["id"], candidate_image.id)

    def test_delete_image_removes_records_and_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = os.path.join(temp_dir, "captured.jpg")
            region_path = os.path.join(temp_dir, "region.jpg")
            with open(image_path, "wb") as fh:
                fh.write(b"captured")
            with open(region_path, "wb") as fh:
                fh.write(b"region")

            image = CapturedImage(
                capture_date=date(2026, 3, 31),
                channel_id="manual",
                captured_at=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc),
                image_path=image_path,
                status=ImageStatusEnum.identified,
                source_video="manual_upload.mp4",
                is_candidate=False,
            )
            db.session.add(image)
            db.session.flush()
            recognition = DishRecognition(
                image_id=image.id,
                dish_name_raw="红烧肉",
                confidence=0.9,
                is_low_confidence=False,
                is_manual=False,
                model_version="test",
            )
            region = CapturedImageRegion(
                image_id=image.id,
                region_index=1,
                bbox={"x1": 1, "y1": 2, "x2": 60, "y2": 80},
                bbox_source="pixels",
                image_path=region_path,
                recognition_status=RegionRecognitionStatusEnum.recognized,
                review_status=RegionReviewStatusEnum.pending,
            )
            match = MatchResult(
                image_id=image.id,
                status=MatchStatusEnum.unmatched_image,
                match_date=image.capture_date,
            )
            db.session.add_all([recognition, region, match])
            db.session.commit()

            res = self.client.delete(
                f"/api/v1/analysis/images/{image.id}",
                headers=self._auth_headers(),
            )

            self.assertEqual(res.status_code, 200)
            payload = res.get_json()
            self.assertEqual(payload["code"], 0)
            self.assertEqual(payload["data"]["deleted_count"], 1)
            self.assertEqual(payload["data"]["deleted_file_count"], 2)
            self.assertIsNone(CapturedImage.query.get(image.id))
            self.assertEqual(DishRecognition.query.filter_by(image_id=image.id).count(), 0)
            self.assertEqual(CapturedImageRegion.query.filter_by(image_id=image.id).count(), 0)
            self.assertEqual(MatchResult.query.filter_by(image_id=image.id).count(), 0)
            self.assertFalse(os.path.exists(image_path))
            self.assertFalse(os.path.exists(region_path))

    def test_batch_delete_images_reports_missing_ids(self):
        image_a = CapturedImage(
            capture_date=date(2026, 3, 31),
            channel_id="manual",
            captured_at=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc),
            image_path="/tmp/batch-a.jpg",
            status=ImageStatusEnum.pending,
            source_video="manual_a.mp4",
            is_candidate=False,
        )
        image_b = CapturedImage(
            capture_date=date(2026, 3, 31),
            channel_id="manual",
            captured_at=datetime(2026, 3, 31, 12, 1, tzinfo=timezone.utc),
            image_path="/tmp/batch-b.jpg",
            status=ImageStatusEnum.pending,
            source_video="manual_b.mp4",
            is_candidate=True,
        )
        db.session.add_all([image_a, image_b])
        db.session.commit()

        res = self.client.delete(
            "/api/v1/analysis/images",
            json={"image_ids": [image_a.id, image_b.id, 999999]},
            headers=self._auth_headers(),
        )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["data"]["deleted_count"], 2)
        self.assertEqual(payload["data"]["missing_ids"], [999999])
        self.assertIsNone(CapturedImage.query.get(image_a.id))
        self.assertIsNone(CapturedImage.query.get(image_b.id))

    def test_recognize_image_allows_candidate_frame_manual_trigger(self):
        self._create_menu(date(2026, 3, 31))
        image = CapturedImage(
            capture_date=date(2026, 3, 31),
            channel_id="manual",
            captured_at=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc),
            image_path="/tmp/candidate.jpg",
            status=ImageStatusEnum.pending,
            source_video="manual_upload.mp4",
            is_candidate=True,
        )
        db.session.add(image)
        db.session.commit()

        original_module = sys.modules.get("app.tasks.recognition")
        delay_mock = Mock()
        fake_module = types.ModuleType("app.tasks.recognition")
        fake_module.recognize_single_image = types.SimpleNamespace(delay=delay_mock)
        sys.modules["app.tasks.recognition"] = fake_module

        try:
            res = self.client.post(
                f"/api/v1/analysis/images/{image.id}/recognize",
                headers=self._auth_headers(),
            )
        finally:
            if original_module is None:
                sys.modules.pop("app.tasks.recognition", None)
            else:
                sys.modules["app.tasks.recognition"] = original_module

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["data"]["status"], ImageStatusEnum.pending.value)
        delay_mock.assert_called_once_with(image.id)

    def test_match_image_runs_immediately_and_returns_latest_summary(self):
        image = CapturedImage(
            capture_date=date(2026, 3, 31),
            channel_id="manual",
            captured_at=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc),
            image_path="/tmp/match-now.jpg",
            status=ImageStatusEnum.identified,
            source_video="manual_upload.mp4",
            is_candidate=False,
        )
        db.session.add(image)
        db.session.commit()

        match_now_mock = Mock()
        original_module = sys.modules.get("app.tasks.matching")
        fake_module = types.ModuleType("app.tasks.matching")
        fake_module.match_single_image_now = match_now_mock
        sys.modules["app.tasks.matching"] = fake_module

        try:
            res = self.client.post(
                f"/api/v1/analysis/images/{image.id}/match",
                headers=self._auth_headers(),
            )
        finally:
            if original_module is None:
                sys.modules.pop("app.tasks.matching", None)
            else:
                sys.modules["app.tasks.matching"] = original_module

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertEqual(payload["code"], 0)
        self.assertFalse(payload["data"]["match_summary"]["is_matched"])
        self.assertIn("暂未找到", payload["message"])
        match_now_mock.assert_called_once_with(image.id)

    def test_match_image_rejects_candidate_frame(self):
        image = CapturedImage(
            capture_date=date(2026, 3, 31),
            channel_id="manual",
            captured_at=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc),
            image_path="/tmp/candidate-match.jpg",
            status=ImageStatusEnum.identified,
            source_video="manual_upload.mp4",
            is_candidate=True,
        )
        db.session.add(image)
        db.session.commit()

        res = self.client.post(
            f"/api/v1/analysis/images/{image.id}/match",
            headers=self._auth_headers(),
        )

        self.assertEqual(res.status_code, 400)
        self.assertIn("备用帧", res.get_json()["message"])

    def test_pipeline_candidates_can_use_day_menu_scope(self):
        breakfast = Dish(
            name="豆浆",
            price=3.0,
            category=CategoryEnum.other,
            is_active=True,
        )
        lunch = Dish(
            name="红烧肉",
            price=12.0,
            category=CategoryEnum.meat,
            is_active=True,
        )
        dinner = Dish(
            name="南瓜粥",
            price=4.0,
            category=CategoryEnum.other,
            is_active=True,
        )
        db.session.add_all([breakfast, lunch, dinner])
        db.session.flush()
        db.session.add(DailyMenu(
            menu_date=date(2026, 3, 31),
            meal_dish_ids={
                "breakfast": [breakfast.id],
                "lunch": [lunch.id],
                "dinner": [dinner.id],
                "late_night": [],
            },
            is_default=False,
            created_by=self.admin_id,
        ))
        image = CapturedImage(
            capture_date=date(2026, 3, 31),
            channel_id="manual",
            captured_at=datetime(2026, 3, 31, 12, 0),
            image_path="/tmp/lunch.jpg",
            status=ImageStatusEnum.pending,
            source_video="manual.mp4",
            is_candidate=False,
        )
        db.session.add(image)
        db.session.commit()

        self.app.config["RECOGNITION_MENU_SCOPE"] = "day"
        try:
            candidates = _build_candidate_dishes_for_pipeline(
                captured_image=image,
                candidate_dish_ids=[],
            )
        finally:
            self.app.config.pop("RECOGNITION_MENU_SCOPE", None)

        self.assertEqual([item["name"] for item in candidates], ["豆浆", "红烧肉", "南瓜粥"])

    def test_pipeline_candidates_can_use_all_active_dishes_scope_without_menu(self):
        active = Dish(
            name="红烧肉",
            price=12.0,
            category=CategoryEnum.meat,
            is_active=True,
        )
        inactive = Dish(
            name="下架菜品",
            price=6.0,
            category=CategoryEnum.other,
            is_active=False,
        )
        image = CapturedImage(
            capture_date=date(2026, 4, 1),
            channel_id="manual",
            captured_at=datetime(2026, 4, 1, 12, 0),
            image_path="/tmp/lunch.jpg",
            status=ImageStatusEnum.pending,
            source_video="manual.mp4",
            is_candidate=False,
        )
        db.session.add_all([active, inactive, image])
        db.session.commit()

        self.app.config["RECOGNITION_MENU_SCOPE"] = "all"
        try:
            candidates = _build_candidate_dishes_for_pipeline(
                captured_image=image,
                candidate_dish_ids=[],
            )
        finally:
            self.app.config.pop("RECOGNITION_MENU_SCOPE", None)

        self.assertEqual([item["name"] for item in candidates], ["红烧肉"])

    def test_create_region_candidates_from_recognition_classifies_regions(self):
        dish = Dish(
            name="红烧肉",
            price=12.0,
            category=CategoryEnum.meat,
            is_active=True,
        )
        db.session.add(dish)
        db.session.flush()

        with tempfile.TemporaryDirectory() as tmpdir:
            self.app.config["IMAGE_STORAGE_PATH"] = tmpdir
            image = CapturedImage(
                capture_date=date(2026, 3, 31),
                channel_id="manual",
                captured_at=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc),
                image_path=self._create_source_image(tmpdir),
                status=ImageStatusEnum.pending,
                source_video="manual.mp4",
                is_candidate=False,
            )
            db.session.add(image)
            db.session.flush()

            regions = create_region_candidates_from_recognition(
                image=image,
                recognition_result={
                    "model_version": "retrieval-api",
                    "regions": [
                        {"index": 1, "bbox": {"x1": 10, "y1": 10, "x2": 80, "y2": 80}, "confidence": 0.96, "source": "yolo"},
                        {"index": 2, "bbox": {"x1": 90, "y1": 10, "x2": 160, "y2": 80}, "confidence": 0.88, "source": "yolo"},
                        {"index": 3, "bbox": {"x1": 10, "y1": 90, "x2": 80, "y2": 160}, "confidence": 0.74, "source": "yolo"},
                        {"index": 4, "bbox": {"x1": 170, "y1": 90, "x2": 235, "y2": 160}, "confidence": 0.63, "source": "yolo"},
                    ],
                    "region_results": [
                        {
                            "index": 1,
                            "bbox": {"x1": 10, "y1": 10, "x2": 80, "y2": 80},
                            "accepted": True,
                            "accepted_hit": {"dish_id": dish.id, "dish_name": "红烧肉", "score": 0.87},
                            "reranked_hits": [{"dish_id": dish.id, "dish_name": "红烧肉", "score": 0.87}],
                        },
                        {
                            "index": 2,
                            "bbox": {"x1": 90, "y1": 10, "x2": 160, "y2": 80},
                            "accepted": False,
                            "reranked_hits": [{"dish_id": dish.id, "dish_name": "红烧肉", "score": 0.42}],
                        },
                        {
                            "index": 3,
                            "bbox": {"x1": 10, "y1": 90, "x2": 80, "y2": 160},
                            "accepted": False,
                            "reranked_hits": [],
                            "recall_hits": [],
                        },
                    ],
                },
            )
            db.session.commit()

            self.assertEqual(len(regions), 4)
            statuses = [region.recognition_status for region in regions]
            self.assertEqual(statuses, [
                RegionRecognitionStatusEnum.recognized,
                RegionRecognitionStatusEnum.low_confidence,
                RegionRecognitionStatusEnum.unrecognized,
                RegionRecognitionStatusEnum.unrecognized,
            ])
            self.assertTrue(all(os.path.exists(region.image_path) for region in regions))
            self.assertTrue(all(region.captured_at == image.captured_at for region in regions))
            self.assertEqual(regions[0].to_dict()["captured_at"], image.captured_at.isoformat())
            self.assertEqual(regions[0].to_dict()["detector_confidence"], 0.96)
            self.assertEqual(regions[3].to_dict()["detector_confidence"], 0.63)
            self.assertEqual(regions[3].detector_source, "yolo")

    def test_region_candidate_list_and_bind(self):
        dish = Dish(
            name="红烧肉",
            price=12.0,
            category=CategoryEnum.meat,
            is_active=True,
        )
        db.session.add(dish)
        db.session.flush()

        with tempfile.TemporaryDirectory() as tmpdir:
            self.app.config["IMAGE_STORAGE_PATH"] = tmpdir
            source_path = self._create_source_image(tmpdir)
            region_path = os.path.join(tmpdir, "candidate.jpg")
            self._create_source_image(tmpdir, "candidate.jpg")
            image = CapturedImage(
                capture_date=date(2026, 3, 31),
                channel_id="manual",
                captured_at=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc),
                image_path=source_path,
                status=ImageStatusEnum.identified,
                source_video="manual.mp4",
                is_candidate=False,
            )
            db.session.add(image)
            db.session.flush()
            region = CapturedImageRegion(
                image_id=image.id,
                region_index=1,
                bbox={"x1": 10, "y1": 10, "x2": 80, "y2": 80},
                bbox_source="pixels",
                detector_source="yolo",
                image_path=region_path,
                recognition_status=RegionRecognitionStatusEnum.recognized,
                suggested_dish_id=dish.id,
                suggested_dish_name=dish.name,
                suggested_confidence=0.87,
                review_status=RegionReviewStatusEnum.pending,
            )
            db.session.add(region)
            db.session.commit()

            list_res = self.client.get(
                "/api/v1/analysis/regions?review_status=pending&recognition_status=recognized",
                headers=self._auth_headers(),
            )
            self.assertEqual(list_res.status_code, 200)
            list_payload = list_res.get_json()["data"]
            self.assertEqual(list_payload["total"], 1)
            self.assertEqual(list_payload["items"][0]["id"], region.id)
            self.assertIn("/images/", list_payload["items"][0]["image_url"])

            with mock.patch("app.api.analysis.trigger_local_embedding_rebuild", return_value=True):
                bind_res = self.client.post(
                    f"/api/v1/analysis/regions/{region.id}/bind",
                    headers=self._auth_headers(),
                    json={"dish_id": dish.id},
                )

            self.assertEqual(bind_res.status_code, 200)
            payload = bind_res.get_json()["data"]
            self.assertEqual(payload["region"]["review_status"], RegionReviewStatusEnum.bound.value)
            self.assertEqual(DishSampleImage.query.filter_by(dish_id=dish.id).count(), 1)
            sample = DishSampleImage.query.filter_by(dish_id=dish.id).first()
            self.assertTrue(os.path.exists(sample.image_path))

    def test_list_regions_filters_by_date_and_meal_slot(self):
        def create_region(region_date: date, captured_at: datetime, index: int) -> CapturedImageRegion:
            image = CapturedImage(
                capture_date=region_date,
                channel_id="manual",
                captured_at=captured_at,
                image_path=f"/tmp/source-{index}.jpg",
                status=ImageStatusEnum.identified,
                source_video="manual.mp4",
                is_candidate=False,
            )
            db.session.add(image)
            db.session.flush()
            region = CapturedImageRegion(
                image_id=image.id,
                region_index=index,
                bbox={"x1": 10, "y1": 10, "x2": 80, "y2": 80},
                bbox_source="pixels",
                image_path=f"/tmp/region-{index}.jpg",
                recognition_status=RegionRecognitionStatusEnum.recognized,
                review_status=RegionReviewStatusEnum.pending,
            )
            db.session.add(region)
            db.session.flush()
            return region

        lunch_region = create_region(date(2026, 3, 31), datetime(2026, 3, 31, 12, 0), 1)
        dinner_region = create_region(date(2026, 3, 31), datetime(2026, 3, 31, 18, 0), 2)
        next_day_lunch_region = create_region(date(2026, 4, 1), datetime(2026, 4, 1, 12, 0), 3)
        db.session.commit()

        date_res = self.client.get(
            "/api/v1/analysis/regions?date=2026-03-31&review_status=pending",
            headers=self._auth_headers(),
        )
        self.assertEqual(date_res.status_code, 200)
        self.assertEqual(date_res.get_json()["data"]["total"], 2)

        meal_res = self.client.get(
            "/api/v1/analysis/regions?meal_slot=lunch&review_status=pending",
            headers=self._auth_headers(),
        )
        self.assertEqual(meal_res.status_code, 200)
        meal_payload = meal_res.get_json()["data"]
        meal_ids = [item["id"] for item in meal_payload["items"]]
        self.assertEqual(meal_payload["total"], 2)
        self.assertIn(lunch_region.id, meal_ids)
        self.assertIn(next_day_lunch_region.id, meal_ids)
        self.assertNotIn(dinner_region.id, meal_ids)

        combined_res = self.client.get(
            "/api/v1/analysis/regions?date=2026-03-31&meal_slot=lunch&review_status=pending",
            headers=self._auth_headers(),
        )
        self.assertEqual(combined_res.status_code, 200)
        payload = combined_res.get_json()["data"]
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["id"], lunch_region.id)

    def test_list_regions_rejects_invalid_meal_slot(self):
        res = self.client.get(
            "/api/v1/analysis/regions?meal_slot=snack",
            headers=self._auth_headers(),
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("meal_slot 无效", res.get_json()["message"])

    def test_region_candidate_bind_persists_actual_selected_dish(self):
        suggested_dish = Dish(
            name="红烧肉",
            price=12.0,
            category=CategoryEnum.meat,
            is_active=True,
        )
        selected_dish = Dish(
            name="番茄炒蛋",
            price=8.0,
            category=CategoryEnum.vegetable,
            is_active=True,
        )
        db.session.add_all([suggested_dish, selected_dish])
        db.session.flush()

        with tempfile.TemporaryDirectory() as tmpdir:
            self.app.config["IMAGE_STORAGE_PATH"] = tmpdir
            source_path = self._create_source_image(tmpdir)
            region_path = os.path.join(tmpdir, "candidate.jpg")
            self._create_source_image(tmpdir, "candidate.jpg")
            image = CapturedImage(
                capture_date=date(2026, 3, 31),
                channel_id="manual",
                captured_at=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc),
                image_path=source_path,
                status=ImageStatusEnum.identified,
                source_video="manual.mp4",
                is_candidate=False,
            )
            db.session.add(image)
            db.session.flush()
            region = CapturedImageRegion(
                image_id=image.id,
                region_index=1,
                bbox={"x1": 10, "y1": 10, "x2": 80, "y2": 80},
                bbox_source="pixels",
                detector_source="yolo",
                image_path=region_path,
                recognition_status=RegionRecognitionStatusEnum.low_confidence,
                suggested_dish_id=suggested_dish.id,
                suggested_dish_name=suggested_dish.name,
                suggested_confidence=0.41,
                review_status=RegionReviewStatusEnum.pending,
                raw_result={"index": 1},
            )
            db.session.add(region)
            db.session.commit()

            with mock.patch("app.api.analysis.trigger_local_embedding_rebuild", return_value=True):
                res = self.client.post(
                    f"/api/v1/analysis/regions/{region.id}/bind",
                    headers=self._auth_headers(),
                    json={"dish_id": selected_dish.id},
                )

            self.assertEqual(res.status_code, 200)
            payload_region = res.get_json()["data"]["region"]
            self.assertEqual(payload_region["suggested_dish_id"], selected_dish.id)
            self.assertEqual(payload_region["suggested_dish_name"], selected_dish.name)

            db.session.refresh(region)
            self.assertEqual(region.suggested_dish_id, selected_dish.id)
            self.assertEqual(region.suggested_dish_name, selected_dish.name)
            self.assertEqual(region.raw_result["bound_dish_id"], selected_dish.id)

    def test_region_candidate_sample_image_fk_sets_null_on_delete(self):
        fk = next(iter(CapturedImageRegion.__table__.c.dish_sample_image_id.foreign_keys))
        self.assertEqual(fk.ondelete, "SET NULL")

    def test_upload_video_queues_manual_processing_task(self):
        self._create_menu(date(2026, 3, 31))
        with tempfile.TemporaryDirectory() as tmpdir, \
             mock.patch("app.api.analysis._resolve_video_storage_path", return_value=tmpdir), \
             mock.patch("app.tasks.video.process_manual_video_upload.delay") as delay_mock:
            res = self.client.post(
                "/api/v1/analysis/upload-video",
                headers=self._auth_headers(),
                data={
                    "video_file": (io.BytesIO(b"fake-video"), "meal.mp4"),
                    "video_start_time": "2026-03-31T12:00:00",
                    "channel_id": "manual",
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertEqual(payload["code"], 0)
        task_id = payload["data"]["task_id"]
        task = TaskLog.query.get(task_id)
        self.assertEqual(task.status, "pending")
        self.assertEqual(task.meta["status_text"], "视频已上传，后台任务已提交")
        delay_mock.assert_called_once()
        args = delay_mock.call_args.args
        self.assertEqual(args[0], task_id)
        self.assertIn("manual_uploads", args[1])
        self.assertTrue(os.path.basename(args[1]).startswith("manual_"))
        self.assertTrue(os.path.basename(args[1]).endswith(".mp4"))
        self.assertEqual(args[3], "2026-03-31T12:00:00")
        self.assertEqual(args[4], "manual")

    def test_upload_video_accepts_hikvision_ps_file(self):
        self._create_menu(date(2026, 3, 31))
        with tempfile.TemporaryDirectory() as tmpdir, \
             mock.patch("app.api.analysis._resolve_video_storage_path", return_value=tmpdir), \
             mock.patch("app.tasks.video.process_manual_video_upload.delay") as delay_mock:
            res = self.client.post(
                "/api/v1/analysis/upload-video",
                headers=self._auth_headers(),
                data={
                    "video_file": (io.BytesIO(b"fake-hikvision-ps"), "meal.PS"),
                    "video_start_time": "2026-03-31T12:00:00",
                    "channel_id": "8",
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertEqual(payload["code"], 0)
        delay_mock.assert_called_once()
        args = delay_mock.call_args.args
        self.assertTrue(os.path.basename(args[1]).startswith("8_"))
        self.assertTrue(os.path.basename(args[1]).endswith(".ps"))
        self.assertEqual(args[4], "8")
        self.assertEqual(args[5], os.path.basename(args[1]))

    def test_pipeline_full_falls_back_to_full_image_when_detector_returns_no_regions(self):
        retrieval_calls = []

        class FakeDetectorClient:
            def post_file(self, path, *, image_path, data=None):
                return {
                    "backend": "yolo",
                    "regions": [],
                }

        class FakeRetrievalClient:
            def post_file(self, path, *, image_path, data=None):
                retrieval_calls.append({
                    "path": path,
                    "image_path": image_path,
                    "data": data,
                })
                return {
                    "recognized_dishes": [{"name": "红烧肉", "confidence": 0.91}],
                    "region_results": [{"index": 1, "bbox": None}],
                    "raw_response": {"mode": "local_embedding"},
                    "model_version": "qwen3_vl_embedding+reranker",
                    "notes": "full_image local embedding 模式，区域数 1",
                }

        with mock.patch("app.api.analysis.make_detector_client", return_value=FakeDetectorClient()), \
             mock.patch("app.api.analysis.make_retrieval_client", return_value=FakeRetrievalClient()), \
             mock.patch("app.api.analysis._build_candidate_dishes_for_pipeline", return_value=[{
                 "id": 1,
                 "name": "红烧肉",
                 "description": "",
             }]):
            res = self.client.post(
                "/api/v1/analysis/pipeline",
                headers=self._auth_headers(),
                data={
                    "mode": "full",
                    "image_file": (io.BytesIO(b"fake-image"), "meal.jpg"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()["data"]
        self.assertEqual(payload["detector_backend"], "full_image")
        self.assertEqual(payload["regions"], [])
        self.assertEqual(payload["recognized_dishes"], [{"name": "红烧肉", "confidence": 0.91}])
        self.assertEqual(retrieval_calls, [{
            "path": "/v1/full",
            "image_path": mock.ANY,
            "data": {
                "candidate_dishes": [{"id": 1, "name": "红烧肉", "description": ""}],
            },
        }])

    def test_pipeline_full_falls_back_to_full_image_when_detector_is_unavailable(self):
        retrieval_calls = []

        class FailingDetectorClient:
            def post_file(self, path, *, image_path, data=None):
                raise InferenceServiceError("detector unavailable", status_code=502)

        class FakeRetrievalClient:
            def post_file(self, path, *, image_path, data=None):
                retrieval_calls.append({
                    "path": path,
                    "image_path": image_path,
                    "data": data,
                })
                return {
                    "recognized_dishes": [{"name": "番茄炒蛋", "confidence": 0.82}],
                    "region_results": [{"index": 1, "bbox": None}],
                    "raw_response": {"mode": "local_embedding"},
                    "model_version": "qwen3_vl_embedding+reranker",
                    "notes": "full_image local embedding 模式，区域数 1",
                }

        with mock.patch("app.api.analysis.make_detector_client", return_value=FailingDetectorClient()), \
             mock.patch("app.api.analysis.make_retrieval_client", return_value=FakeRetrievalClient()), \
             mock.patch("app.api.analysis._build_candidate_dishes_for_pipeline", return_value=[{
                 "id": 9,
                 "name": "番茄炒蛋",
                 "description": "",
             }]):
            res = self.client.post(
                "/api/v1/analysis/pipeline",
                headers=self._auth_headers(),
                data={
                    "mode": "full",
                    "image_file": (io.BytesIO(b"fake-image"), "meal.jpg"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()["data"]
        self.assertEqual(payload["detector_backend"], "full_image")
        self.assertEqual(payload["regions"], [])
        self.assertEqual(payload["recognized_dishes"], [{"name": "番茄炒蛋", "confidence": 0.82}])
        self.assertEqual(retrieval_calls, [{
            "path": "/v1/full",
            "image_path": mock.ANY,
            "data": {
                "candidate_dishes": [{"id": 9, "name": "番茄炒蛋", "description": ""}],
            },
        }])

    def test_trigger_analysis_is_idempotent_when_same_date_sync_is_active(self):
        self._create_menu(date(2026, 4, 3))
        active_task = TaskLog(
            task_type="video_source_sync",
            task_date=date(2026, 4, 3),
            status="running",
        )
        db.session.add(active_task)
        db.session.commit()

        res = self.client.post(
            "/api/v1/analysis/tasks/trigger",
            headers=self._auth_headers(),
            json={"date": "2026-04-03"},
        )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()["data"]
        self.assertTrue(payload["already_running"])
        self.assertEqual(payload["task_id"], active_task.id)
        self.assertIn("无需重复触发", payload["message"])
        self.assertEqual(
            TaskLog.query.filter_by(task_type="video_source_sync", task_date=date(2026, 4, 3)).count(),
            1,
        )

    def test_trigger_analysis_allows_another_date_in_distributed_pipeline(self):
        self._create_menu(date(2026, 4, 3))
        db.session.add(TaskLog(
            task_type="video_source_sync",
            task_date=date(2026, 4, 2),
            status="running",
        ))
        db.session.commit()
        self.app.config["VIDEO_DISTRIBUTED_PIPELINE"] = True

        try:
            with mock.patch(
                "app.tasks.video.sync_video_source_media.apply_async",
                create=True,
            ) as publish_mock:
                res = self.client.post(
                    "/api/v1/analysis/tasks/trigger",
                    headers=self._auth_headers(),
                    json={"date": "2026-04-03"},
                )

            self.assertEqual(res.status_code, 200)
            payload = res.get_json()["data"]
            self.assertFalse(payload["already_running"])
            queued = TaskLog.query.get(payload["task_id"])
            self.assertEqual(queued.status, "pending")
            self.assertEqual(queued.task_date, date(2026, 4, 3))
            publish_mock.assert_called_once_with(
                args=["2026-04-03", queued.id],
                task_id=queued.meta["sync_dispatch_task_id"],
                queue="video",
            )
        finally:
            self.app.config.pop("VIDEO_DISTRIBUTED_PIPELINE", None)

    def test_trigger_analysis_rejects_analyzed_date_without_force(self):
        self._create_menu(date(2026, 4, 3))
        db.session.add(CapturedImage(
            capture_date=date(2026, 4, 3),
            channel_id="1",
            captured_at=datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc),
            image_path="/tmp/trigger-no-force.jpg",
            status=ImageStatusEnum.identified,
            source_video="a.mp4",
            is_candidate=False,
        ))
        db.session.commit()

        res = self.client.post(
            "/api/v1/analysis/tasks/trigger",
            headers=self._auth_headers(),
            json={"date": "2026-04-03"},
        )

        self.assertEqual(res.status_code, 409)
        payload = res.get_json()
        self.assertTrue(payload["data"]["already_analyzed"])
        self.assertEqual(payload["data"]["image_count"], 1)
        self.assertIn("已分析过", payload["message"])
        self.assertEqual(
            TaskLog.query.filter_by(task_type="video_source_sync", task_date=date(2026, 4, 3)).count(),
            0,
        )

    def test_trigger_analysis_force_purges_old_data_before_rerun(self):
        self._create_menu(date(2026, 4, 3))
        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = self._create_source_image(tmpdir, "rerun.jpg")
            image = CapturedImage(
                capture_date=date(2026, 4, 3),
                channel_id="1",
                captured_at=datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc),
                image_path=image_path,
                status=ImageStatusEnum.matched,
                source_video="a.mp4",
                is_candidate=False,
            )
            db.session.add(image)
            db.session.flush()
            db.session.add(MatchResult(
                image_id=image.id,
                status=MatchStatusEnum.matched,
                match_date=date(2026, 4, 3),
                is_manual=False,
            ))
            db.session.add(NutritionLog(
                student_id=999,
                log_date=date(2026, 4, 3),
                nutrient_totals={"calories": 500},
                meal_count=1,
                dish_ids=[],
            ))
            db.session.commit()
            image_id = image.id

            with mock.patch(
                "app.tasks.video.sync_video_source_media.apply_async",
                create=True,
            ):
                res = self.client.post(
                    "/api/v1/analysis/tasks/trigger",
                    headers=self._auth_headers(),
                    json={"date": "2026-04-03", "force": True},
                )

            self.assertEqual(res.status_code, 200)
            payload = res.get_json()["data"]
            self.assertFalse(payload["already_running"])
            self.assertEqual(payload["purged_image_count"], 1)
            self.assertIn("已清理旧数据", payload["message"])
            self.assertIsNone(CapturedImage.query.get(image_id))
            self.assertEqual(
                MatchResult.query.filter_by(match_date=date(2026, 4, 3)).count(),
                0,
            )
            self.assertEqual(
                NutritionLog.query.filter_by(log_date=date(2026, 4, 3)).count(),
                0,
            )
            self.assertFalse(os.path.exists(image_path))
            queued = TaskLog.query.get(payload["task_id"])
            self.assertEqual(queued.status, "pending")
            self.assertEqual(queued.task_date, date(2026, 4, 3))

    def test_trigger_analysis_force_blocked_by_manual_match(self):
        self._create_menu(date(2026, 4, 3))
        image = CapturedImage(
            capture_date=date(2026, 4, 3),
            channel_id="1",
            captured_at=datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc),
            image_path="/tmp/trigger-manual-match.jpg",
            status=ImageStatusEnum.matched,
            source_video="a.mp4",
            is_candidate=False,
        )
        db.session.add(image)
        db.session.flush()
        db.session.add(MatchResult(
            image_id=image.id,
            status=MatchStatusEnum.confirmed,
            match_date=date(2026, 4, 3),
            is_manual=True,
        ))
        db.session.commit()
        image_id = image.id

        res = self.client.post(
            "/api/v1/analysis/tasks/trigger",
            headers=self._auth_headers(),
            json={"date": "2026-04-03", "force": True},
        )

        self.assertEqual(res.status_code, 409)
        self.assertIn("人工匹配", res.get_json()["message"])
        self.assertIsNotNone(CapturedImage.query.get(image_id))
        self.assertEqual(
            TaskLog.query.filter_by(task_type="video_source_sync", task_date=date(2026, 4, 3)).count(),
            0,
        )

    def test_retry_video_sync_is_idempotent_when_same_date_trigger_is_active(self):
        failed = TaskLog(
            task_type="video_source_sync",
            task_date=date(2026, 4, 3),
            status="failed",
            finished_at=datetime.now(timezone.utc),
        )
        active = TaskLog(
            task_type="video_source_sync",
            task_date=date(2026, 4, 3),
            status="pending",
        )
        db.session.add_all([failed, active])
        db.session.commit()
        self.app.config["VIDEO_DISTRIBUTED_PIPELINE"] = True

        try:
            res = self.client.post(
                f"/api/v1/analysis/tasks/{failed.id}/retry",
                headers=self._auth_headers(),
            )

            self.assertEqual(res.status_code, 200)
            payload = res.get_json()["data"]
            self.assertTrue(payload["already_running"])
            self.assertEqual(payload["task_id"], active.id)
            db.session.refresh(failed)
            self.assertEqual(failed.status, "failed")
        finally:
            self.app.config.pop("VIDEO_DISTRIBUTED_PIPELINE", None)

    def test_trigger_analysis_rejects_when_menu_is_missing(self):
        # 该用例验证 meal 模式下“菜单缺失即拒绝”；默认范围已改为 all（菜单可选），需显式切回。
        self.app.config["RECOGNITION_MENU_SCOPE"] = "meal"
        try:
            res = self.client.post(
                "/api/v1/analysis/tasks/trigger",
                headers=self._auth_headers(),
                json={"date": "2026-04-03"},
            )

            self.assertEqual(res.status_code, 400)
            payload = res.get_json()
            self.assertEqual(payload["code"], 400)
            self.assertIn("未配置菜单", payload["message"])

            task = TaskLog.query.filter_by(task_type="video_source_sync", task_date=date(2026, 4, 3)).one()
            self.assertEqual(task.status, "failed")
            self.assertEqual(task.meta["alert_type"], "menu_not_configured")
            self.assertIn("未配置菜单", task.error_message or "")
        finally:
            self.app.config.pop("RECOGNITION_MENU_SCOPE", None)

    def test_cancel_active_video_sync_task_marks_it_failed(self):
        task = TaskLog(
            task_type="video_source_sync",
            task_date=date(2026, 4, 3),
            status="running",
            meta={"status_text": "正在抽帧"},
        )
        db.session.add(task)
        db.session.commit()

        res = self.client.post(
            f"/api/v1/analysis/tasks/{task.id}/cancel",
            headers=self._auth_headers(),
        )

        self.assertEqual(res.status_code, 200)
        db.session.refresh(task)
        self.assertEqual(task.status, "failed")
        self.assertIsNotNone(task.finished_at)
        self.assertEqual(task.error_message, "任务已由管理员手动结束")
        self.assertEqual(task.meta["status_text"], "任务已由管理员手动结束")


if __name__ == "__main__":
    unittest.main()
