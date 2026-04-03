import os
import sys
import types
import unittest
import io
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
from app.api.analysis import bp as analysis_bp  # noqa: E402
from app.models import CapturedImage, ImageStatusEnum, RoleEnum, TaskLog, User  # noqa: E402
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
        db.session.query(CapturedImage).delete()
        db.session.query(TaskLog).delete()
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

    def tearDown(self):
        db.session.rollback()

    def _auth_headers(self) -> dict[str, str]:
        token = generate_token(self.admin_id, RoleEnum.admin.value)
        return {"Authorization": f"Bearer {token}"}

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

    def test_recognize_image_allows_candidate_frame_manual_trigger(self):
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

    def test_trigger_analysis_rejects_when_sync_task_is_active(self):
        db.session.add(TaskLog(
            task_type="video_source_sync",
            task_date=date(2026, 4, 3),
            status="running",
        ))
        db.session.commit()

        res = self.client.post(
            "/api/v1/analysis/tasks/trigger",
            headers=self._auth_headers(),
            json={"date": "2026-04-03"},
        )

        self.assertEqual(res.status_code, 400)
        payload = res.get_json()
        self.assertEqual(payload["code"], 400)
        self.assertEqual(payload["message"], "当前已有视频同步任务在执行，请等待完成后再触发")

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
