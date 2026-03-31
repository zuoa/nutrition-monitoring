import os
import sys
import types
import unittest
from datetime import date, datetime, timedelta, timezone
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

from app import db  # noqa: E402
import app.models  # noqa: F401,E402
from app.api.analysis import bp as analysis_bp  # noqa: E402
from app.models import CapturedImage, ImageStatusEnum, RoleEnum, User  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
