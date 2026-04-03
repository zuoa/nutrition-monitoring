import io
import os
import sys
import tempfile
import types
import unittest
from datetime import timedelta
from unittest import mock

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
from app.api.dishes import bp as dishes_bp  # noqa: E402
from app.models import Dish, DishSampleImage, EmbeddingStatusEnum, RoleEnum, User  # noqa: E402
from app.utils.jwt_utils import generate_token  # noqa: E402


class DishesApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.app = Flask(__name__)
        cls.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SECRET_KEY="test-secret",
            JWT_ALGORITHM="HS256",
            JWT_ACCESS_TOKEN_EXPIRES=timedelta(hours=1),
            IMAGE_STORAGE_PATH=cls.temp_dir.name,
            MAX_IMAGE_SIZE=5 * 1024 * 1024,
        )
        db.init_app(cls.app)
        cls.app.register_blueprint(dishes_bp, url_prefix="/api/v1/dishes")
        cls.app_context = cls.app.app_context()
        cls.app_context.push()
        db.create_all()
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        db.drop_all()
        cls.app_context.pop()
        cls.temp_dir.cleanup()

    def setUp(self):
        db.session.query(DishSampleImage).delete()
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

    def tearDown(self):
        db.session.rollback()

    def _auth_headers(self) -> dict[str, str]:
        token = generate_token(self.admin_id, RoleEnum.admin.value)
        return {"Authorization": f"Bearer {token}"}

    def test_update_dish_image_replaces_file_and_resets_embedding_state(self):
        dish = Dish(
            name="红烧肉",
            price=12.0,
            category="荤菜",
            is_active=True,
        )
        db.session.add(dish)
        db.session.commit()

        dish_dir = os.path.join(self.temp_dir.name, "dish_samples", str(dish.id))
        os.makedirs(dish_dir, exist_ok=True)
        old_path = os.path.join(dish_dir, "old.jpg")
        with open(old_path, "wb") as fh:
            fh.write(b"old-image")

        image = DishSampleImage(
            dish_id=dish.id,
            image_path=old_path,
            original_filename="old.jpg",
            sort_order=1,
            is_cover=True,
            embedding_status=EmbeddingStatusEnum.ready,
            embedding_model="qwen3-vl-embedding-2b",
            embedding_version="v1",
            error_message="old error",
        )
        db.session.add(image)
        db.session.commit()

        with mock.patch("app.api.dishes.trigger_local_embedding_rebuild") as rebuild_mock:
            res = self.client.put(
                f"/api/v1/dishes/images/{image.id}",
                headers=self._auth_headers(),
                data={
                    "image": (io.BytesIO(b"new-image-bytes"), "cropped.jpg"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["data"]["image"]["embedding_status"], EmbeddingStatusEnum.pending.value)
        self.assertEqual(payload["data"]["image"]["original_filename"], "cropped.jpg")

        db.session.refresh(image)
        self.assertEqual(image.embedding_status, EmbeddingStatusEnum.pending)
        self.assertIsNone(image.embedding_model)
        self.assertIsNone(image.embedding_version)
        self.assertIsNone(image.error_message)
        self.assertNotEqual(image.image_path, old_path)
        self.assertTrue(os.path.exists(image.image_path))
        self.assertFalse(os.path.exists(old_path))
        rebuild_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
