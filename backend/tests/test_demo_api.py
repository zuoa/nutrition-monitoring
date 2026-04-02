import base64
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

if "redis" not in sys.modules:
    redis = types.ModuleType("redis")
    redis.from_url = lambda *args, **kwargs: object()
    sys.modules["redis"] = redis

from app import db  # noqa: E402
import app.models  # noqa: F401,E402
from app.api.demo import bp as demo_bp  # noqa: E402
from app.models import CategoryEnum, DailyMenu, Dish, RoleEnum, User  # noqa: E402
from app.utils.jwt_utils import generate_token  # noqa: E402


class DemoApiTests(unittest.TestCase):
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
        cls.app.register_blueprint(demo_bp, url_prefix="/api/v1/demo")
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
        db.session.query(DailyMenu).delete(synchronize_session=False)
        db.session.query(Dish).delete(synchronize_session=False)
        db.session.query(User).delete(synchronize_session=False)
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
        db.session.remove()

    def _auth_headers(self) -> dict[str, str]:
        token = generate_token(self.admin_id, RoleEnum.admin.value)
        return {"Authorization": f"Bearer {token}"}

    def _create_dish(self, name: str, price: float = 12.0) -> Dish:
        dish = Dish(
            name=name,
            description=f"{name} 的视觉描述",
            price=price,
            category=CategoryEnum.meat,
            is_active=True,
        )
        db.session.add(dish)
        db.session.flush()
        return dish

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

    def _with_fake_demo_agent(self, questions, callback):
        original_module = sys.modules.get("app.services.demo_agent")
        fake_module = types.ModuleType("app.services.demo_agent")

        class FakeDemoAgentService:
            def __init__(self, config):
                self.config = config

            def suggest_follow_up_questions_for_analysis(self, analysis_result=None):
                return questions

        fake_module.DemoAgentService = FakeDemoAgentService
        sys.modules["app.services.demo_agent"] = fake_module
        try:
            callback()
        finally:
            if original_module is None:
                sys.modules.pop("app.services.demo_agent", None)
            else:
                sys.modules["app.services.demo_agent"] = original_module

    def test_quick_analyze_prefers_today_menu_candidates(self):
        menu_dish_a = self._create_dish("红烧鸡腿")
        menu_dish_b = self._create_dish("清炒上海青")
        self._create_dish("不在菜单里的鱼香肉丝")
        db.session.add(DailyMenu(
            menu_date=date.today(),
            dish_ids=[menu_dish_a.id, menu_dish_b.id],
            is_default=False,
            created_by=self.admin_id,
        ))
        db.session.commit()

        captured_candidates = {}

        def handler(_image_path, candidate_dishes):
            captured_candidates["names"] = [item["name"] for item in candidate_dishes]
            return {
                "dishes": [{"name": menu_dish_a.name, "confidence": 0.93}],
                "notes": "menu-based",
            }

        def run_request():
            res = self.client.post(
                "/api/v1/demo/quick-analyze",
                headers=self._auth_headers(),
                json={
                    "image_base64": base64.b64encode(b"demo-image").decode("utf-8"),
                },
            )

            self.assertEqual(res.status_code, 200)
            payload = res.get_json()
            self.assertEqual(payload["code"], 0)
            self.assertEqual(captured_candidates["names"], [menu_dish_a.name, menu_dish_b.name])
            self.assertEqual(payload["data"]["matched_dishes"][0]["name"], menu_dish_a.name)
            self.assertAlmostEqual(payload["data"]["matched_dishes"][0]["confidence"], 0.93)

        self._with_fake_recognizer(handler, run_request)

    def test_quick_analyze_falls_back_to_all_active_dishes_without_limit(self):
        dishes = [self._create_dish(f"菜品{i:03d}") for i in range(120)]
        target = dishes[-1]
        db.session.commit()

        captured_candidates = {}

        def handler(_image_path, candidate_dishes):
            captured_candidates["count"] = len(candidate_dishes)
            captured_candidates["last_name"] = candidate_dishes[-1]["name"]
            return {
                "dishes": [{"name": target.name, "confidence": 0.88}],
                "notes": "all-active",
            }

        def run_request():
            res = self.client.post(
                "/api/v1/demo/quick-analyze",
                headers=self._auth_headers(),
                json={
                    "image_base64": base64.b64encode(b"demo-image").decode("utf-8"),
                },
            )

            self.assertEqual(res.status_code, 200)
            payload = res.get_json()
            self.assertEqual(payload["code"], 0)
            self.assertEqual(captured_candidates["count"], 120)
            self.assertEqual(captured_candidates["last_name"], target.name)
            self.assertEqual(payload["data"]["matched_dishes"][0]["name"], target.name)

        self._with_fake_recognizer(handler, run_request)

    def test_quick_analyze_returns_dynamic_follow_up_questions(self):
        dish = self._create_dish("清蒸南瓜")
        db.session.commit()

        def recognizer_handler(_image_path, _candidate_dishes):
            return {
                "dishes": [{"name": dish.name, "confidence": 0.91}],
                "notes": "dynamic-follow-up",
            }

        dynamic_questions = [
            "这份餐盘里最该先减掉哪一口？",
            "如果只能补一样，优先补什么？",
            "下午这餐怎么搭配会更稳？",
        ]

        def run_request():
            res = self.client.post(
                "/api/v1/demo/quick-analyze",
                headers=self._auth_headers(),
                json={
                    "image_base64": base64.b64encode(b"demo-image").decode("utf-8"),
                },
            )

            self.assertEqual(res.status_code, 200)
            payload = res.get_json()
            self.assertEqual(payload["code"], 0)
            self.assertEqual(payload["data"]["follow_up_questions"], dynamic_questions)

        self._with_fake_demo_agent(
            dynamic_questions,
            lambda: self._with_fake_recognizer(recognizer_handler, run_request),
        )


if __name__ == "__main__":
    unittest.main()
