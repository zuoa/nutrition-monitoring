import io
import os
import sys
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
from app.api.admin import bp as admin_bp  # noqa: E402
from app.models import CategoryEnum, Dish, DishSampleImage, EmbeddingStatusEnum, RoleEnum, User, VideoSource  # noqa: E402
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
        runtime_config_path = "/tmp/nutrition-monitoring-admin-runtime-config.json"
        if os.path.exists(runtime_config_path):
            os.unlink(runtime_config_path)
        self.app.config["LOCAL_RUNTIME_CONFIG_PATH"] = runtime_config_path
        db.session.query(VideoSource).delete()
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
        runtime_config_path = self.app.config.get("LOCAL_RUNTIME_CONFIG_PATH")
        if runtime_config_path and os.path.exists(runtime_config_path):
            os.unlink(runtime_config_path)

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

    def test_config_includes_local_embedding_sample_stats(self):
        db.session.add(DishSampleImage(
            dish_id=self.dish_id,
            image_path="/tmp/sample-ready.jpg",
            original_filename="sample-ready.jpg",
            embedding_status=EmbeddingStatusEnum.ready,
        ))
        db.session.add(DishSampleImage(
            dish_id=self.dish_id,
            image_path="/tmp/sample-pending.jpg",
            original_filename="sample-pending.jpg",
            embedding_status=EmbeddingStatusEnum.pending,
        ))
        db.session.add(DishSampleImage(
            dish_id=self.dish_id,
            image_path="/tmp/sample-failed.jpg",
            original_filename="sample-failed.jpg",
            embedding_status=EmbeddingStatusEnum.failed,
        ))
        db.session.commit()

        res = self.client.get(
            "/api/v1/admin/config",
            headers=self._auth_headers(),
        )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()["data"]
        self.assertEqual(payload["local_embedding_sample_image_count"], 3)
        self.assertEqual(payload["local_embedding_sample_ready_count"], 1)
        self.assertEqual(payload["local_embedding_sample_pending_count"], 1)
        self.assertEqual(payload["local_embedding_sample_failed_count"], 1)

    def test_update_config_persists_video_sync_meal_windows(self):
        update_res = self.client.put(
            "/api/v1/admin/config",
            headers=self._auth_headers(),
            json={
                "video_sync_meal_windows": [
                    {"start": "06:30", "end": "08:30"},
                    {"start": "11:00", "end": "13:30"},
                    {"start": "17:00", "end": "19:30"},
                ],
            },
        )

        self.assertEqual(update_res.status_code, 200)
        self.assertEqual(update_res.get_json()["data"]["updated_keys"], ["VIDEO_SYNC_MEAL_WINDOWS"])

        get_res = self.client.get(
            "/api/v1/admin/config",
            headers=self._auth_headers(),
        )
        self.assertEqual(get_res.status_code, 200)
        self.assertEqual(get_res.get_json()["data"]["video_sync_meal_windows"], [
            {"start": "06:30", "end": "08:30"},
            {"start": "11:00", "end": "13:30"},
            {"start": "17:00", "end": "19:30"},
        ])

    def test_video_source_crud_activate_and_config_summary(self):
        create_res = self.client.post(
            "/api/v1/admin/video-sources",
            headers=self._auth_headers(),
            json={
                "name": "食堂主 NVR",
                "source_type": "nvr",
                "status": "enabled",
                "is_active": True,
                "config": {
                    "host": "192.168.1.10",
                    "port": 8080,
                    "username": "admin",
                    "password": "secret-1",
                    "channel_ids": ["1", "2"],
                    "meal_windows": [{"start": "11:30", "end": "13:00"}],
                    "download_trigger_time": "21:30",
                    "local_storage_path": "/data/nvr_cache",
                    "retention_days": 3,
                },
            },
        )
        self.assertEqual(create_res.status_code, 200)
        created = create_res.get_json()["data"]
        self.assertEqual(created["source_type"], "nvr")
        self.assertTrue(created["is_active"])
        self.assertTrue(created["config"]["password_configured"])
        self.assertEqual(created["config"]["channel_ids"], ["1", "2"])

        with mock.patch(
            "app.services.video_sources.manager.HikvisionCameraService.discover_device",
            return_value={
                "host": "192.168.1.88",
                "port": 80,
                "device_info": {
                    "device_name": "档口 IPC",
                    "model": "DS-2CD",
                    "serial_number": "SN001",
                },
                "channels": [
                    {"channel_id": "1", "name": "主通道"},
                    {"channel_id": "2", "name": "副通道"},
                ],
            },
        ):
            second_res = self.client.post(
                "/api/v1/admin/video-sources",
                headers=self._auth_headers(),
                json={
                    "name": "档口海康",
                    "source_type": "hikvision_camera",
                    "status": "enabled",
                    "config": {
                        "host": "192.168.1.88",
                        "port": 80,
                        "username": "admin",
                        "password": "camera-secret",
                        "selected_channel_ids": ["2"],
                    },
                },
            )
        self.assertEqual(second_res.status_code, 200)
        second_payload = second_res.get_json()["data"]
        second_id = second_payload["id"]
        self.assertEqual(second_payload["config"]["host"], "192.168.1.88")
        self.assertEqual(second_payload["config"]["selected_channel_ids"], ["2"])
        self.assertEqual(second_payload["config"]["cameras"][0]["channel_id"], "2")
        self.assertEqual(second_payload["config"]["device_name"], "档口 IPC")
        self.assertEqual(second_payload["config"]["username"], "admin")
        self.assertTrue(second_payload["config"]["password_configured"])

        list_res = self.client.get(
            "/api/v1/admin/video-sources",
            headers=self._auth_headers(),
        )
        self.assertEqual(list_res.status_code, 200)
        items = list_res.get_json()["data"]["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["name"], "食堂主 NVR")

        with mock.patch(
            "app.services.video_sources.manager.HikvisionCameraService.discover_device",
            return_value={
                "host": "192.168.1.99",
                "port": 80,
                "device_info": {
                    "device_name": "档口 IPC 2",
                    "model": "DS-2CD",
                    "serial_number": "SN002",
                },
                "channels": [
                    {"channel_id": "1", "name": "主通道"},
                    {"channel_id": "3", "name": "出口通道"},
                ],
            },
        ):
            update_res = self.client.put(
                f"/api/v1/admin/video-sources/{second_id}",
                headers=self._auth_headers(),
                json={
                    "name": "档口海康 2",
                    "status": "enabled",
                    "config": {
                        "host": "192.168.1.99",
                        "port": 80,
                        "username": "admin-updated",
                        "password": "",
                        "selected_channel_ids": ["3"],
                    },
                },
            )
        self.assertEqual(update_res.status_code, 200)
        updated = update_res.get_json()["data"]
        self.assertEqual(updated["name"], "档口海康 2")
        self.assertEqual(updated["config"]["username"], "admin-updated")
        self.assertTrue(updated["config"]["password_configured"])
        self.assertEqual(updated["config"]["selected_channel_ids"], ["3"])
        self.assertEqual(updated["config"]["cameras"][0]["channel_id"], "3")

        activate_res = self.client.post(
            f"/api/v1/admin/video-sources/{second_id}/activate",
            headers=self._auth_headers(),
        )
        self.assertEqual(activate_res.status_code, 200)
        self.assertTrue(activate_res.get_json()["data"]["is_active"])

        config_res = self.client.get(
            "/api/v1/admin/config",
            headers=self._auth_headers(),
        )
        self.assertEqual(config_res.status_code, 200)
        summary = config_res.get_json()["data"]["active_video_source_summary"]
        self.assertEqual(summary["id"], second_id)
        self.assertEqual(summary["source_type"], "hikvision_camera")

    def test_hikvision_discover_endpoint_uses_existing_password_when_editing(self):
        source = VideoSource(
            name="档口海康",
            source_type="hikvision_camera",
            status="enabled",
            is_active=False,
            config_json={
                "host": "192.168.1.88",
                "port": 80,
                "device_name": "旧设备",
                "selected_channel_ids": ["1"],
                "cameras": [{
                    "channel_id": "1",
                    "name": "主通道",
                    "host": "192.168.1.88",
                    "port": 80,
                }],
            },
            credentials_json_encrypted=b"",
        )
        db.session.add(source)
        db.session.flush()

        from app.services.video_sources.crypto import encrypt_json_payload
        source.credentials_json_encrypted = encrypt_json_payload({
            "username": "admin",
            "password": "stored-secret",
        }, self.app.config["SECRET_KEY"])
        db.session.commit()

        with mock.patch(
            "app.services.video_sources.manager.HikvisionCameraService.discover_device",
            return_value={
                "host": "192.168.1.88",
                "port": 80,
                "device_info": {
                    "device_name": "新设备名",
                    "model": "DS-2CD",
                    "serial_number": "SN009",
                },
                "channels": [
                    {"channel_id": "1", "name": "主通道"},
                    {"channel_id": "2", "name": "侧边通道"},
                ],
            },
        ) as discover_mock:
            res = self.client.post(
                "/api/v1/admin/video-sources/hikvision/discover",
                headers=self._auth_headers(),
                json={
                    "video_source_id": source.id,
                    "config": {
                        "host": "192.168.1.88",
                        "port": 80,
                        "username": "admin",
                        "password": "",
                        "selected_channel_ids": ["2"],
                    },
                },
            )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()["data"]
        self.assertEqual(payload["selected_channel_ids"], ["2"])
        self.assertEqual(payload["channels"][1]["channel_id"], "2")
        self.assertTrue(payload["channels"][1]["selected"])
        self.assertTrue(payload["password_configured"])
        discover_mock.assert_called_once_with(
            host="192.168.1.88",
            port=80,
            username="admin",
            password="stored-secret",
        )

    def test_video_source_validate_updates_validation_status(self):
        create_res = self.client.post(
            "/api/v1/admin/video-sources",
            headers=self._auth_headers(),
            json={
                "name": "食堂主 NVR",
                "source_type": "nvr",
                "status": "enabled",
                "config": {
                    "host": "192.168.1.10",
                    "port": 8080,
                    "username": "admin",
                    "password": "secret-1",
                    "channel_ids": ["1"],
                    "meal_windows": [{"start": "11:30", "end": "13:00"}],
                    "download_trigger_time": "21:30",
                    "local_storage_path": "/data/nvr_cache",
                    "retention_days": 3,
                },
            },
        )
        source_id = create_res.get_json()["data"]["id"]

        with mock.patch("app.services.nvr.NVRService.is_available", return_value=True):
            validate_res = self.client.post(
                f"/api/v1/admin/video-sources/{source_id}/validate",
                headers=self._auth_headers(),
            )

        self.assertEqual(validate_res.status_code, 200)
        payload = validate_res.get_json()["data"]
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"]["last_validation_status"], "success")


if __name__ == "__main__":
    unittest.main()
