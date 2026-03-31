import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

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

if "chardet" not in sys.modules:
    chardet = types.ModuleType("chardet")
    chardet.detect = lambda content: {"encoding": "utf-8"}
    sys.modules["chardet"] = chardet

from app import db  # noqa: E402
import app.models  # noqa: F401,E402
from app.api.consumption import bp as consumption_bp  # noqa: E402
from app.models import (  # noqa: E402
    ConsumptionRecord,
    MatchResult,
    MatchStatusEnum,
    CapturedImage,
    Dish,
    DishRecognition,
    CategoryEnum,
    ImageStatusEnum,
    RoleEnum,
    User,
)
from app.utils.jwt_utils import generate_token  # noqa: E402


class ConsumptionApiTests(unittest.TestCase):
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
        cls.app.register_blueprint(consumption_bp, url_prefix="/api/v1/consumption")
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
        db.session.query(CapturedImage).delete()
        db.session.query(Dish).delete()
        db.session.query(ConsumptionRecord).delete()
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

    def test_list_matches_is_consumption_record_driven(self):
        record = ConsumptionRecord(
            student_no="230501",
            student_name="张三",
            transaction_time=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc),
            amount=12.0,
            transaction_id="tx-001",
        )
        db.session.add(record)
        db.session.commit()

        res = self.client.get(
            "/api/v1/consumption/matches?date=2026-03-31",
            headers=self._auth_headers(),
        )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["data"]["total"], 1)

        item = payload["data"]["items"][0]
        self.assertEqual(item["consumption_record_id"], record.id)
        self.assertEqual(item["status"], "unmatched_record")
        self.assertEqual(item["consumption_record"]["transaction_id"], "tx-001")

    def test_list_unmatched_images_returns_image_payload(self):
        image = CapturedImage(
            capture_date=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc).date(),
            channel_id="1",
            captured_at=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc),
            image_path="/tmp/unmatched.jpg",
            status=ImageStatusEnum.identified,
            source_video="nvr_001.mp4",
            is_candidate=False,
        )
        db.session.add(image)
        db.session.commit()

        match = MatchResult(
            image_id=image.id,
            status=MatchStatusEnum.unmatched_image,
            match_date=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc).date(),
        )
        db.session.add(match)
        db.session.commit()

        res = self.client.get(
            "/api/v1/consumption/matches/unmatched-images?date=2026-03-31",
            headers=self._auth_headers(),
        )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["data"]["total"], 1)
        item = payload["data"]["items"][0]
        self.assertEqual(item["status"], "unmatched_image")
        self.assertEqual(item["image"]["id"], image.id)
        self.assertEqual(item["image"]["source_video"], "nvr_001.mp4")

    def test_list_matches_returns_linked_image_payload(self):
        record = ConsumptionRecord(
            student_no="230501",
            student_name="张三",
            transaction_time=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc),
            amount=12.0,
            transaction_id="tx-002",
        )
        db.session.add(record)
        db.session.commit()

        image = CapturedImage(
            capture_date=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc).date(),
            channel_id="2",
            captured_at=datetime(2026, 3, 31, 12, 0, 1, tzinfo=timezone.utc),
            image_path="/tmp/matched.jpg",
            status=ImageStatusEnum.identified,
            source_video="nvr_002.mp4",
            is_candidate=False,
        )
        db.session.add(image)
        db.session.commit()

        match = MatchResult(
            consumption_record_id=record.id,
            image_id=image.id,
            status=MatchStatusEnum.time_matched_only,
            match_date=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc).date(),
        )
        dish = Dish(
            name="红烧肉",
            price=12.0,
            category=CategoryEnum.meat,
            is_active=True,
        )
        db.session.add(dish)
        db.session.commit()

        recognition = DishRecognition(
            image_id=image.id,
            dish_id=dish.id,
            dish_name_raw=dish.name,
            confidence=0.95,
            is_low_confidence=False,
            is_manual=False,
            model_version="test",
        )
        db.session.add(match)
        db.session.add(recognition)
        db.session.commit()

        res = self.client.get(
            "/api/v1/consumption/matches?date=2026-03-31&status=time_matched_only",
            headers=self._auth_headers(),
        )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["data"]["total"], 1)
        item = payload["data"]["items"][0]
        self.assertEqual(item["status"], "time_matched_only")
        self.assertEqual(item["image"]["id"], image.id)
        self.assertEqual(item["image"]["channel_id"], "2")
        self.assertEqual(item["image_price_total"], 12.0)


if __name__ == "__main__":
    unittest.main()
