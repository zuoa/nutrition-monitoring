import os
import sys
import types
import unittest
from datetime import date, datetime, timedelta

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
from app.api.menus import bp as menus_bp  # noqa: E402
from app.models import CategoryEnum, DailyMenu, Dish, RoleEnum, User  # noqa: E402
from app.models.menu import resolve_meal_slot_for_datetime  # noqa: E402
from app.utils.jwt_utils import generate_token  # noqa: E402


class MenuApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask(__name__)
        cls.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SECRET_KEY="test-secret",
            JWT_ALGORITHM="HS256",
            JWT_ACCESS_TOKEN_EXPIRES=timedelta(hours=1),
            APP_TIMEZONE="Asia/Shanghai",
            VIDEO_TIMEZONE="Asia/Shanghai",
        )
        db.init_app(cls.app)
        cls.app.register_blueprint(menus_bp, url_prefix="/api/v1/menus")
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

    def test_get_menu_returns_default_four_meal_structure(self):
        self._create_dish("红烧鸡腿")
        self._create_dish("清炒菠菜")
        db.session.commit()

        res = self.client.get(
            f"/api/v1/menus/{date.today().isoformat()}",
            headers=self._auth_headers(),
        )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()["data"]
        self.assertTrue(payload["is_default"])
        self.assertEqual(payload["meal_dish_ids"], {
            "breakfast": [],
            "lunch": [],
            "dinner": [],
            "late_night": [],
        })

    def test_upsert_menu_persists_meal_specific_dishes(self):
        breakfast = self._create_dish("豆浆")
        lunch_a = self._create_dish("红烧肉")
        lunch_b = self._create_dish("清炒西兰花")
        late_night = self._create_dish("小馄饨")
        db.session.commit()

        res = self.client.put(
            f"/api/v1/menus/{date.today().isoformat()}",
            headers=self._auth_headers(),
            json={
                "meal_dish_ids": {
                    "breakfast": [breakfast.id],
                    "lunch": [lunch_a.id, lunch_b.id],
                    "dinner": [],
                    "late_night": [breakfast.id, late_night.id],
                },
            },
        )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()["data"]
        self.assertFalse(payload["is_default"])
        self.assertEqual(payload["meal_dish_ids"], {
            "breakfast": [breakfast.id],
            "lunch": [lunch_a.id, lunch_b.id],
            "dinner": [],
            "late_night": [breakfast.id, late_night.id],
        })

        menu = DailyMenu.query.filter_by(menu_date=date.today()).first()
        self.assertIsNotNone(menu)
        assert menu is not None
        self.assertEqual(menu.aggregated_dish_ids(), [breakfast.id, lunch_a.id, lunch_b.id, late_night.id])
        self.assertEqual(menu.dish_ids_for_meal("late_night"), [breakfast.id, late_night.id])

    def test_upsert_menu_rejects_legacy_dish_ids_payload(self):
        dish_a = self._create_dish("青椒炒蛋")
        dish_b = self._create_dish("番茄蛋汤")
        db.session.commit()

        res = self.client.put(
            f"/api/v1/menus/{date.today().isoformat()}",
            headers=self._auth_headers(),
            json={"dish_ids": [dish_a.id, dish_b.id]},
        )

        self.assertEqual(res.status_code, 400)

    def test_meal_slot_resolution_and_specific_lookup(self):
        dish_a = self._create_dish("鸡蛋灌饼")
        dish_b = self._create_dish("南瓜粥")
        db.session.add(DailyMenu(
            menu_date=date.today(),
            meal_dish_ids={
                "breakfast": [dish_a.id],
                "lunch": [],
                "dinner": [dish_b.id],
                "late_night": [],
            },
            is_default=False,
            created_by=self.admin_id,
        ))
        db.session.commit()

        menu = DailyMenu.query.filter_by(menu_date=date.today()).first()
        self.assertIsNotNone(menu)
        assert menu is not None
        self.assertEqual(menu.dish_ids_for_meal("dinner"), [dish_b.id])
        self.assertEqual(
            resolve_meal_slot_for_datetime(datetime(2026, 4, 7, 22, 15)),
            "late_night",
        )


if __name__ == "__main__":
    unittest.main()
