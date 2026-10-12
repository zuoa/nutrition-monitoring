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
from app.models import CategoryEnum, DailyMenu, Dish, DishMealSlot, DishResidentMealSlot, DishSampleImage, RoleEnum, User  # noqa: E402
from app.models.menu import (  # noqa: E402
    is_menu_configured,
    normalize_meal_dish_ids,
    resolve_meal_slot_for_datetime,
)
from app.utils.jwt_utils import generate_token  # noqa: E402
from app.services.candidate_dishes import (  # noqa: E402
    CandidateDishResolutionError,
    FIXED_MEAL_POOL_EMPTY,
    MEAL_MENU_NOT_CONFIGURED,
    resolve_candidate_dishes,
)


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
        db.session.query(DishSampleImage).delete(synchronize_session=False)
        db.session.query(DishResidentMealSlot).delete(synchronize_session=False)
        db.session.query(DishMealSlot).delete(synchronize_session=False)
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

    def _create_dish(self, name: str, price: float = 12.0, *, with_sample: bool = True) -> Dish:
        dish = Dish(
            name=name,
            description=f"{name} 的视觉描述",
            price=price,
            category=CategoryEnum.meat,
            is_active=True,
        )
        db.session.add(dish)
        db.session.flush()
        if with_sample:
            dish.sample_images.append(DishSampleImage(image_path=f"/tmp/{dish.id}.jpg"))
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
        self.assertEqual(payload["dishes"], [])

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
        self.assertEqual(menu.dish_ids_for_recognition("breakfast", "day"), [breakfast.id, lunch_a.id, lunch_b.id, late_night.id])
        self.assertEqual(menu.dish_ids_for_recognition("late_night", "meal"), [breakfast.id, late_night.id])

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

    def test_upsert_rejects_all_missing_samples_without_changing_saved_menu(self):
        ready = self._create_dish("米饭")
        missing = self._create_dish("红烧肉", with_sample=False)
        inactive_sample = self._create_dish("炒白菜")
        inactive_sample.sample_images[0].is_active = False
        menu = DailyMenu(menu_date=date.today(), meal_dish_ids={"lunch": [ready.id]}, is_default=False)
        db.session.add(menu)
        db.session.commit()

        res = self.client.put(
            f"/api/v1/menus/{date.today().isoformat()}", headers=self._auth_headers(),
            json={"meal_dish_ids": {"lunch": [ready.id, missing.id], "dinner": [inactive_sample.id, missing.id]}},
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("红烧肉", res.get_json()["message"])
        self.assertIn("炒白菜", res.get_json()["message"])
        self.assertEqual(res.get_json()["message"].count("红烧肉"), 1)
        db.session.expire_all()
        self.assertEqual(menu.normalized_meal_dish_ids()["lunch"], [ready.id])
        self.assertEqual(menu.normalized_meal_dish_ids()["dinner"], [])

    def test_missing_sample_does_not_create_menu_and_pending_sample_is_sufficient(self):
        dish = self._create_dish("豆浆", with_sample=False)
        db.session.commit()
        target = f"/api/v1/menus/{date.today().isoformat()}"
        body = {"meal_dish_ids": {"breakfast": [dish.id]}}
        res = self.client.put(target, headers=self._auth_headers(), json=body)
        self.assertEqual(res.status_code, 400)
        self.assertIsNone(DailyMenu.query.filter_by(menu_date=date.today()).first())
        dish.sample_images.append(DishSampleImage(image_path="/tmp/soymilk.jpg"))
        db.session.commit()
        self.assertEqual(self.client.put(target, headers=self._auth_headers(), json=body).status_code, 200)

        # Existing selections must also pass the check after the last sample is removed.
        dish.sample_images[0].is_active = False
        db.session.commit()
        self.assertEqual(self.client.put(target, headers=self._auth_headers(), json=body).status_code, 400)
        self.assertEqual(self.client.put(target, headers=self._auth_headers(), json={"meal_dish_ids": {}}).status_code, 200)

    def test_upsert_reports_inactive_and_nonexistent_dishes(self):
        dish = self._create_dish("已停用米饭")
        dish.is_active = False
        db.session.commit()
        res = self.client.put(
            f"/api/v1/menus/{date.today().isoformat()}", headers=self._auth_headers(),
            json={"meal_dish_ids": {"lunch": [dish.id, 999999]}},
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("已停用米饭", res.get_json()["message"])
        self.assertIn("999999", res.get_json()["message"])
        self.assertIsNone(DailyMenu.query.filter_by(menu_date=date.today()).first())

    def test_resident_setting_does_not_configure_menu_or_change_saved_choices(self):
        rice = self._create_dish("米饭")
        rice.set_resident_meal_slots(["lunch", "dinner"])
        db.session.commit()
        target = f"/api/v1/menus/{date.today().isoformat()}"
        # Residents are defaults in the editor, never an implicit persisted menu.
        self.assertTrue(self.client.get(target, headers=self._auth_headers()).get_json()["data"]["is_default"])
        res = self.client.put(target, headers=self._auth_headers(), json={"meal_dish_ids": {"lunch": [rice.id]}})
        self.assertEqual(res.status_code, 200)
        rice.set_resident_meal_slots(["breakfast"])
        db.session.commit()
        payload = self.client.get(target, headers=self._auth_headers()).get_json()["data"]
        self.assertEqual(payload["meal_dish_ids"]["lunch"], [rice.id])
        self.assertEqual(payload["meal_dish_ids"]["breakfast"], [])

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

    def test_fixed_breakfast_pool_uses_tagged_active_dishes_without_daily_menu(self):
        breakfast_only = self._create_dish("豆浆")
        shared = self._create_dish("水煮鸡蛋")
        lunch_only = self._create_dish("红烧肉")
        breakfast_only.set_meal_slots(["breakfast"])
        shared.set_meal_slots(["breakfast", "lunch"])
        lunch_only.set_meal_slots(["lunch"])
        db.session.commit()

        image = types.SimpleNamespace(
            capture_date=date(2026, 4, 7),
            captured_at=datetime(2026, 4, 7, 7, 30),
        )
        config = dict(self.app.config)
        config["FIXED_CANDIDATE_MEAL_SLOTS"] = ["breakfast"]
        config["RECOGNITION_MENU_SCOPE"] = "meal"

        dishes = resolve_candidate_dishes(image, config)

        self.assertEqual({dish.name for dish in dishes}, {"豆浆", "水煮鸡蛋"})

    def test_fixed_breakfast_pool_empty_does_not_fallback_to_library(self):
        self._create_dish("红烧肉")
        db.session.commit()
        image = types.SimpleNamespace(
            capture_date=date(2026, 4, 7),
            captured_at=datetime(2026, 4, 7, 7, 30),
        )
        config = dict(self.app.config)
        config["FIXED_CANDIDATE_MEAL_SLOTS"] = ["breakfast"]
        config["RECOGNITION_MENU_SCOPE"] = "all"

        with self.assertRaises(CandidateDishResolutionError) as captured:
            resolve_candidate_dishes(image, config)

        self.assertEqual(captured.exception.code, FIXED_MEAL_POOL_EMPTY)

    def test_meal_scope_does_not_fallback_when_target_meal_is_empty(self):
        dinner = self._create_dish("南瓜粥")
        db.session.add(DailyMenu(
            menu_date=date(2026, 4, 7),
            meal_dish_ids={"breakfast": [], "lunch": [], "dinner": [dinner.id], "late_night": []},
            is_default=False,
        ))
        db.session.commit()
        image = types.SimpleNamespace(
            capture_date=date(2026, 4, 7),
            captured_at=datetime(2026, 4, 7, 12, 0),
        )
        config = dict(self.app.config)
        config["FIXED_CANDIDATE_MEAL_SLOTS"] = ["breakfast"]
        config["RECOGNITION_MENU_SCOPE"] = "meal"

        with self.assertRaises(CandidateDishResolutionError) as captured:
            resolve_candidate_dishes(image, config)

        self.assertEqual(captured.exception.code, MEAL_MENU_NOT_CONFIGURED)

    def test_meal_helpers_honor_explicit_config_for_custom_slots(self):
        """Regression: Celery/tasks must pass effective cfg so custom slot keys
        are not dropped in favor of the default keys from current_app.config."""
        dish_a = self._create_dish("下午茶套餐")
        custom_cfg = {
            "MEAL_SLOTS": [
                {"key": "brunch", "label": "早午餐", "start": "10:00", "end": "11:30"},
                {"key": "snack", "label": "下午茶", "start": "14:00", "end": "16:00"},
            ],
        }
        db.session.add(DailyMenu(
            menu_date=date.today() + timedelta(days=1),
            meal_dish_ids={"snack": [dish_a.id]},
            is_default=False,
            created_by=self.admin_id,
        ))
        db.session.commit()

        normalized = normalize_meal_dish_ids({"snack": [dish_a.id]}, custom_cfg)
        self.assertEqual(set(normalized.keys()), {"brunch", "snack"})
        self.assertEqual(normalized["snack"], [dish_a.id])

        menu = DailyMenu.query.filter_by(
            menu_date=date.today() + timedelta(days=1),
        ).first()
        assert menu is not None
        # Without config the default keys would hide the "snack" dishes.
        self.assertTrue(is_menu_configured(menu, custom_cfg))
        self.assertEqual(menu.dish_ids_for_meal("snack", custom_cfg), [dish_a.id])
        self.assertEqual(
            resolve_meal_slot_for_datetime(
                datetime(2026, 4, 7, 15, 0),
                config=custom_cfg,
            ),
            "snack",
        )


if __name__ == "__main__":
    unittest.main()
