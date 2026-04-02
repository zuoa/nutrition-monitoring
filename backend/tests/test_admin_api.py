import io
import os
import sys
import types
import unittest
from datetime import timedelta

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
from app.api.admin import bp as admin_bp  # noqa: E402
from app.models import CategoryEnum, Dish, RoleEnum, User  # noqa: E402
from app.utils.jwt_utils import generate_token  # noqa: E402


class AdminApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask(__name__)
        cls.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SECRET_KEY="test-secret",
            JWT_ALGORITHM="HS256",
            JWT_ACCESS_TOKEN_EXPIRES=timedelta(hours=1),
            DISH_RECOGNITION_MODE="local_embedding",
        )
        db.init_app(cls.app)
        cls.app.register_blueprint(admin_bp, url_prefix="/api/v1/admin")
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
        db.session.flush()
        self.admin_id = admin.id

        dish = Dish(
            name="红烧肉",
            description="深红褐色，块状，肥瘦相间",
            price=12.0,
            category=CategoryEnum.meat,
            is_active=True,
        )
        db.session.add(dish)
        db.session.commit()
        self.dish_id = dish.id

    def tearDown(self):
        db.session.rollback()

    def _auth_headers(self) -> dict[str, str]:
        token = generate_token(self.admin_id, RoleEnum.admin.value)
        return {"Authorization": f"Bearer {token}"}

    def _with_fake_recognizer(self, handler, callback):
        original_module = sys.modules.get("app.services.dish_recognition")
        fake_module = types.ModuleType("app.services.dish_recognition")

        class FakeDishRecognitionService:
            def __init__(self, config):
                self.config = config

            def recognize_dishes(self, image_path, candidate_dishes):
                return handler(image_path, candidate_dishes)

        fake_module.DishRecognitionService = FakeDishRecognitionService
        sys.modules["app.services.dish_recognition"] = fake_module
        try:
            callback()
        finally:
            if original_module is None:
                sys.modules.pop("app.services.dish_recognition", None)
            else:
                sys.modules["app.services.dish_recognition"] = original_module

    def test_local_embedding_test_uses_dish_recognition_service_with_selected_candidates(self):
        captured = {}

        def handler(_image_path, candidate_dishes):
            captured["candidate_dishes"] = candidate_dishes
            return {
                "dishes": [{"name": "红烧肉", "confidence": 0.93}],
                "regions": [{
                    "index": 1,
                    "bbox": {"x1": 10, "y1": 20, "x2": 110, "y2": 160},
                    "confidence": 0.91,
                    "source": "yolo",
                }],
                "region_results": [{
                    "index": 1,
                    "bbox": {"x1": 10, "y1": 20, "x2": 110, "y2": 160},
                    "embedding_dim": 1024,
                    "recall_hits": [{"dish_id": self.dish_id, "dish_name": "红烧肉", "similarity": 0.93}],
                    "reranked_hits": [{"dish_id": self.dish_id, "dish_name": "红烧肉", "score": 0.95}],
                }],
                "detector_backend": "yolo",
                "model_version": "qwen3_vl_embedding+reranker",
                "notes": "yolo local embedding 模式，区域数 1",
                "raw_response": {"mode": "local_embedding"},
            }

        def run_request():
            res = self.client.post(
                "/api/v1/admin/local-embedding-test",
                headers=self._auth_headers(),
                data={
                    "image": (io.BytesIO(b"fake-image"), "meal.jpg"),
                    "candidate_dish_ids": f"[{self.dish_id}]",
                },
                content_type="multipart/form-data",
            )

            self.assertEqual(res.status_code, 200)
            payload = res.get_json()["data"]
            self.assertEqual(payload["candidate_source"], "selected")
            self.assertEqual(payload["candidate_count"], 1)
            self.assertEqual(payload["recognized_dishes"], [{"name": "红烧肉", "confidence": 0.93}])
            self.assertEqual(payload["detector_backend"], "yolo")
            self.assertEqual(captured["candidate_dishes"], [{
                "id": self.dish_id,
                "name": "红烧肉",
                "description": "深红褐色，块状，肥瘦相间",
            }])

        self._with_fake_recognizer(handler, run_request)


if __name__ == "__main__":
    unittest.main()
