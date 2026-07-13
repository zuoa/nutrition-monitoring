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

if "celery_app" not in sys.modules:
    celery_app_module = types.ModuleType("celery_app")

    class _FakeTask:
        def __init__(self, fn):
            self.fn = fn

        def __call__(self, *args, **kwargs):
            return self.fn(*args, **kwargs)

        def delay(self, *args, **kwargs):
            return types.SimpleNamespace(id="fake-celery-task")

    class _FakeCelery:
        def task(self, *args, **kwargs):
            def decorator(fn):
                return _FakeTask(fn)
            return decorator

    celery_app_module.celery = _FakeCelery()
    sys.modules["celery_app"] = celery_app_module

from app import db  # noqa: E402
import app.models  # noqa: F401,E402
from app.api.dishes import bp as dishes_bp  # noqa: E402
from app.models import Dish, DishSampleImage, EmbeddingStatusEnum, RoleEnum, TaskLog, User  # noqa: E402
from app.services import embedding_jobs  # noqa: E402
from app.services.dish_confusion import enrich_dish_confusion_report  # noqa: E402
from app.services.inference_client import InferenceServiceError  # noqa: E402
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
        db.session.remove()
        db.session.query(TaskLog).delete()
        db.session.query(DishSampleImage).delete()
        db.session.query(Dish).delete()
        db.session.query(User).delete()
        db.session.commit()
        self.app.config["OPENAI_API_KEY"] = "test-key"

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

    def test_create_dish_accepts_expanded_nutrition_fields(self):
        res = self.client.post(
            "/api/v1/dishes/",
            headers=self._auth_headers(),
            json={
                "name": "番茄炒蛋",
                "price": 8.0,
                "category": "荤菜",
                "weight": 120,
                "calories": 135,
                "protein": 8.8,
                "fat": 8.4,
                "cholesterol": 180,
                "carbohydrate": 7.2,
                "added_sugar": 1.5,
                "fiber": 0.8,
                "sodium": 320,
                "calcium": 48,
                "iron": 1.6,
                "zinc": 1.1,
                "vitamin_a": 120,
                "vitamin_c": 12,
                "vitamin_d": 1.4,
            },
        )

        self.assertEqual(res.status_code, 201)
        data = res.get_json()["data"]
        self.assertEqual(data["calories"], 135.0)
        self.assertEqual(data["cholesterol"], 180.0)
        self.assertEqual(data["added_sugar"], 1.5)
        self.assertEqual(data["vitamin_d"], 1.4)

    def test_list_dishes_filters_by_active_sample_images(self):
        with_sample = Dish(
            name="红烧肉",
            price=12.0,
            category="荤菜",
            is_active=True,
        )
        without_sample = Dish(
            name="米饭",
            price=2.0,
            category="主食",
            is_active=True,
        )
        inactive_sample_only = Dish(
            name="清炒菠菜",
            price=6.0,
            category="素菜",
            is_active=True,
        )
        db.session.add_all([with_sample, without_sample, inactive_sample_only])
        db.session.flush()
        db.session.add_all([
            DishSampleImage(
                dish_id=with_sample.id,
                image_path="/tmp/red-braised-pork.jpg",
                original_filename="red-braised-pork.jpg",
                is_active=True,
            ),
            DishSampleImage(
                dish_id=inactive_sample_only.id,
                image_path="/tmp/spinach.jpg",
                original_filename="spinach.jpg",
                is_active=False,
            ),
        ])
        db.session.commit()

        has_sample_res = self.client.get(
            "/api/v1/dishes/?has_sample_images=true&active_only=false",
            headers=self._auth_headers(),
        )
        self.assertEqual(has_sample_res.status_code, 200)
        has_sample_items = has_sample_res.get_json()["data"]["items"]
        self.assertEqual([item["name"] for item in has_sample_items], ["红烧肉"])
        self.assertEqual(has_sample_items[0]["sample_image_count"], 1)

        no_sample_res = self.client.get(
            "/api/v1/dishes/?has_sample_images=false&active_only=false",
            headers=self._auth_headers(),
        )
        self.assertEqual(no_sample_res.status_code, 200)
        no_sample_items = no_sample_res.get_json()["data"]["items"]
        self.assertEqual({item["name"] for item in no_sample_items}, {"米饭", "清炒菠菜"})

    def test_list_dishes_exposes_and_filters_sample_embedding_status(self):
        ready_dish = Dish(name="全量就绪", price=10.0, category="荤菜", is_active=True)
        pending_dish = Dish(name="等待生成", price=8.0, category="素菜", is_active=True)
        failed_dish = Dish(name="生成失败", price=6.0, category="汤", is_active=True)
        no_sample_dish = Dish(name="没有样图", price=2.0, category="主食", is_active=True)
        db.session.add_all([ready_dish, pending_dish, failed_dish, no_sample_dish])
        db.session.flush()
        db.session.add_all([
            DishSampleImage(
                dish_id=ready_dish.id,
                image_path="/tmp/ready-1.jpg",
                embedding_status=EmbeddingStatusEnum.ready,
                is_active=True,
            ),
            DishSampleImage(
                dish_id=ready_dish.id,
                image_path="/tmp/ready-2.jpg",
                embedding_status=EmbeddingStatusEnum.ready,
                is_active=True,
            ),
            DishSampleImage(
                dish_id=pending_dish.id,
                image_path="/tmp/pending.jpg",
                embedding_status=EmbeddingStatusEnum.pending,
                is_active=True,
            ),
            DishSampleImage(
                dish_id=failed_dish.id,
                image_path="/tmp/failed.jpg",
                embedding_status=EmbeddingStatusEnum.failed,
                is_active=True,
            ),
        ])
        db.session.commit()

        all_res = self.client.get(
            "/api/v1/dishes/?active_only=false&page_size=20",
            headers=self._auth_headers(),
        )
        items_by_name = {item["name"]: item for item in all_res.get_json()["data"]["items"]}
        self.assertEqual(items_by_name["全量就绪"]["sample_embedding_status"], "ready")
        self.assertEqual(items_by_name["全量就绪"]["sample_embedding_ready_count"], 2)
        self.assertEqual(items_by_name["等待生成"]["sample_embedding_status"], "pending")
        self.assertEqual(items_by_name["生成失败"]["sample_embedding_status"], "failed")
        self.assertEqual(items_by_name["没有样图"]["sample_embedding_status"], "none")

        not_ready_res = self.client.get(
            "/api/v1/dishes/?embedding_status=not_ready&active_only=false",
            headers=self._auth_headers(),
        )
        self.assertEqual(
            {item["name"] for item in not_ready_res.get_json()["data"]["items"]},
            {"等待生成", "生成失败"},
        )

        ready_res = self.client.get(
            "/api/v1/dishes/?embedding_status=ready&active_only=false",
            headers=self._auth_headers(),
        )
        self.assertEqual(
            [item["name"] for item in ready_res.get_json()["data"]["items"]],
            ["全量就绪"],
        )

        no_sample_res = self.client.get(
            "/api/v1/dishes/?embedding_status=none&active_only=false",
            headers=self._auth_headers(),
        )
        self.assertEqual(
            [item["name"] for item in no_sample_res.get_json()["data"]["items"]],
            ["没有样图"],
        )

    def test_list_dishes_uses_requested_visual_embedding_progress(self):
        ready_dish = Dish(name="视觉就绪", price=10.0, category="荤菜", is_active=True)
        pending_dish = Dish(name="视觉待生成", price=8.0, category="素菜", is_active=True)
        db.session.add_all([ready_dish, pending_dish])
        db.session.flush()
        db.session.add_all([
            DishSampleImage(
                dish_id=ready_dish.id,
                image_path="/tmp/visual-ready.jpg",
                embedding_status=EmbeddingStatusEnum.pending,
                visual_embedding_status=EmbeddingStatusEnum.ready,
                visual_embedding_version="siglip2+dinov3-v1",
                is_active=True,
            ),
            DishSampleImage(
                dish_id=pending_dish.id,
                image_path="/tmp/visual-pending.jpg",
                embedding_status=EmbeddingStatusEnum.ready,
                visual_embedding_status=EmbeddingStatusEnum.pending,
                is_active=True,
            ),
        ])
        db.session.commit()

        all_res = self.client.get(
            "/api/v1/dishes/?active_only=false&page_size=20&embedding_pipeline=visual",
            headers=self._auth_headers(),
        )
        self.assertEqual(all_res.status_code, 200)
        items_by_name = {item["name"]: item for item in all_res.get_json()["data"]["items"]}
        self.assertEqual(items_by_name["视觉就绪"]["sample_embedding_pipeline"], "visual")
        self.assertEqual(items_by_name["视觉就绪"]["sample_embedding_status"], "ready")
        self.assertEqual(
            items_by_name["视觉就绪"]["sample_images"][0]["active_embedding_status"],
            "ready",
        )
        self.assertEqual(items_by_name["视觉待生成"]["sample_embedding_status"], "pending")

        ready_res = self.client.get(
            "/api/v1/dishes/?active_only=false&embedding_pipeline=visual&embedding_status=ready",
            headers=self._auth_headers(),
        )
        self.assertEqual(
            [item["name"] for item in ready_res.get_json()["data"]["items"]],
            ["视觉就绪"],
        )

    def test_sample_change_keeps_existing_sample_embedding_cache_ready(self):
        dish = Dish(name="索引待更新", price=10.0, category="荤菜", is_active=True)
        db.session.add(dish)
        db.session.flush()
        image = DishSampleImage(
            dish_id=dish.id,
            image_path="/tmp/stale-sample.jpg",
            embedding_status=EmbeddingStatusEnum.ready,
            embedding_input_hash="cached-hash",
            embedding_vector=[1.0, 0.0],
            visual_embedding_status=EmbeddingStatusEnum.ready,
            is_active=True,
        )
        db.session.add(image)
        db.session.commit()

        fake_client = mock.Mock()
        fake_client.get_json.return_value = {"retrieval_pipeline": "visual"}
        with mock.patch.object(embedding_jobs, "make_retrieval_control_client", return_value=fake_client), \
             mock.patch("app.tasks.embeddings.rebuild_sample_embeddings.delay") as delay_mock:
            triggered = embedding_jobs.trigger_local_embedding_rebuild({
                "DISH_RECOGNITION_MODE": "local_embedding",
                "LOCAL_REBUILD_SAMPLE_EMBEDDINGS_ON_UPLOAD": True,
                "LOCAL_RUNTIME_CONFIG_PATH": "/tmp/nonexistent-runtime-config.json",
            }, reason="test sample change")

        db.session.refresh(image)
        self.assertTrue(triggered)
        self.assertEqual(image.embedding_status, EmbeddingStatusEnum.ready)
        self.assertEqual(image.visual_embedding_status, EmbeddingStatusEnum.ready)
        self.assertEqual(image.embedding_vector, [1.0, 0.0])
        fake_client.post_json.assert_called_once_with(
            "/v1/index/invalidate",
            {"pipeline": "qwen"},
        )
        delay_mock.assert_called_once_with("visual")

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
            embedding_input_hash="old-hash",
            embedding_vector=[1.0, 0.0],
            error_message="old error",
            visual_embedding_status=EmbeddingStatusEnum.ready,
            visual_embedding_input_hash="old-visual-hash",
            visual_embedding_version="visual-v1",
            visual_error_message="old visual error",
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
        self.assertIsNone(image.embedding_input_hash)
        self.assertIsNone(image.embedding_vector)
        self.assertIsNone(image.error_message)
        self.assertEqual(image.visual_embedding_status, EmbeddingStatusEnum.pending)
        self.assertIsNone(image.visual_embedding_input_hash)
        self.assertIsNone(image.visual_embedding_version)
        self.assertIsNone(image.visual_embedding_updated_at)
        self.assertIsNone(image.visual_error_message)
        self.assertNotEqual(image.image_path, old_path)
        self.assertTrue(os.path.exists(image.image_path))
        self.assertFalse(os.path.exists(old_path))
        rebuild_mock.assert_called_once()

    def test_batch_analyze_nutrition_queues_async_task(self):
        missing_nutrition = Dish(
            name="番茄炒蛋",
            price=8.0,
            category="荤菜",
            weight=120,
            is_active=True,
        )
        completed = Dish(
            name="米饭",
            price=2.0,
            category="主食",
            calories=116,
            protein=2.6,
            fat=0.3,
            cholesterol=0,
            carbohydrate=25.6,
            added_sugar=0,
            fiber=0.3,
            sodium=2,
            calcium=5,
            iron=0.2,
            zinc=0.4,
            vitamin_a=0,
            vitamin_c=0,
            vitamin_d=0,
            is_active=True,
        )
        db.session.add_all([missing_nutrition, completed])
        db.session.commit()

        with mock.patch("app.tasks.dishes.batch_analyze_dish_nutrition.delay", return_value=types.SimpleNamespace(id="celery-123")) as delay_mock:
            res = self.client.post(
                "/api/v1/dishes/batch-analyze-nutrition",
                headers=self._auth_headers(),
                json={},
            )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertEqual(payload["code"], 0)
        data = payload["data"]
        self.assertEqual(data["message"], "批量营养分析任务已提交")
        self.assertEqual(data["total"], 1)
        self.assertIsInstance(data["task_id"], int)
        self.assertEqual(data["task"]["status"], "pending")
        self.assertEqual(data["task"]["total_count"], 1)
        self.assertEqual(data["task"]["meta"]["celery_task_id"], "celery-123")
        delay_mock.assert_called_once_with(data["task_id"])

        db.session.refresh(missing_nutrition)
        self.assertIsNone(missing_nutrition.calories)

    def test_confusion_analysis_enriches_pairs_and_reports_uncovered_dishes(self):
        left = Dish(name="红烧肉", price=12.0, category="荤菜", is_active=True)
        right = Dish(name="糖醋排骨", price=13.0, category="荤菜", is_active=True)
        uncovered = Dish(name="米饭", price=2.0, category="主食", is_active=True)
        db.session.add_all([left, right, uncovered])
        db.session.flush()
        left_image = DishSampleImage(
            dish_id=left.id,
            image_path="/data/images/dish_samples/left.jpg",
            embedding_status=EmbeddingStatusEnum.ready,
            is_active=True,
        )
        right_image = DishSampleImage(
            dish_id=right.id,
            image_path="/data/images/dish_samples/right.jpg",
            embedding_status=EmbeddingStatusEnum.ready,
            is_active=True,
        )
        db.session.add_all([left_image, right_image])
        db.session.commit()

        fake_client = mock.Mock()
        fake_client.post_json.return_value = {
            "pipeline": "qwen",
            "index_ready": True,
            "method": "global_embedding_cosine",
            "generated_at": "2026-07-13T10:00:00+00:00",
            "thresholds": {"high": 0.85, "medium": 0.75},
            "summary": {
                "indexed_dish_count": 2,
                "indexed_sample_count": 2,
                "invalid_sample_count": 0,
                "analyzed_pair_count": 1,
                "high_risk_pair_count": 1,
                "medium_risk_pair_count": 0,
                "safe_pair_count": 0,
                "returned_pair_count": 1,
                "truncated_pair_count": 0,
            },
            "indexed_dishes": [
                {"dish_id": left.id, "dish_name": left.name, "sample_count": 1},
                {"dish_id": right.id, "dish_name": right.name, "sample_count": 1},
            ],
            "pairs": [{
                "risk_level": "high",
                "max_similarity": 0.93,
                "similar_sample_pair_count": 1,
                "left": {
                    "dish_id": left.id,
                    "dish_name": left.name,
                    "sample_count": 1,
                    "sample_image_id": left_image.id,
                },
                "right": {
                    "dish_id": right.id,
                    "dish_name": right.name,
                    "sample_count": 1,
                    "sample_image_id": right_image.id,
                },
            }],
        }

        with mock.patch("app.api.dishes.make_retrieval_control_client", return_value=fake_client):
            res = self.client.post(
                "/api/v1/dishes/confusion-analysis",
                headers=self._auth_headers(),
                json={"pipeline": "qwen"},
            )

        self.assertEqual(res.status_code, 200)
        data = res.get_json()["data"]
        self.assertEqual(data["summary"]["total_active_dish_count"], 3)
        self.assertEqual(data["summary"]["not_analyzed_dish_count"], 1)
        self.assertEqual(data["summary"]["stale_indexed_dish_count"], 0)
        self.assertEqual(data["not_analyzed_dishes"][0]["dish_name"], "米饭")
        self.assertEqual(data["pairs"][0]["left"]["category"], "荤菜")
        self.assertEqual(data["pairs"][0]["left"]["sample_image_url"], "/images/dish_samples/left.jpg")
        self.assertTrue(data["recommendations"])
        fake_client.post_json.assert_called_once_with(
            "/v1/index/confusion-report",
            {"pipeline": "qwen"},
        )

    def test_confusion_analysis_maps_internal_unauthorized_to_bad_gateway(self):
        fake_client = mock.Mock()
        fake_client.post_json.side_effect = InferenceServiceError("未授权", status_code=401)

        with mock.patch("app.api.dishes.make_retrieval_control_client", return_value=fake_client):
            res = self.client.post(
                "/api/v1/dishes/confusion-analysis",
                headers=self._auth_headers(),
                json={"pipeline": "qwen"},
            )

        self.assertEqual(res.status_code, 502)
        payload = res.get_json()
        self.assertEqual(payload["code"], 502)
        self.assertIn("推理服务鉴权失败", payload["message"])

    def test_confusion_report_marks_deleted_index_dish_as_missing(self):
        dish = Dish(name="现有菜品", price=10.0, category="荤菜", is_active=True)
        db.session.add(dish)
        db.session.commit()
        deleted_dish_id = dish.id + 1000
        report = {
            "index_ready": True,
            "summary": {"analyzed_pair_count": 1, "high_risk_pair_count": 1},
            "indexed_dishes": [
                {"dish_id": dish.id, "dish_name": dish.name, "sample_count": 1},
                {"dish_id": deleted_dish_id, "dish_name": "已删除菜品", "sample_count": 1},
            ],
            "pairs": [{
                "risk_level": "high",
                "max_similarity": 0.93,
                "similar_sample_pair_count": 1,
                "left": {"dish_id": dish.id, "dish_name": dish.name, "sample_count": 1},
                "right": {"dish_id": deleted_dish_id, "dish_name": "已删除菜品", "sample_count": 1},
            }],
        }

        enriched = enrich_dish_confusion_report(report, pipeline="qwen")

        self.assertTrue(enriched["pairs"][0]["left"]["exists"])
        self.assertFalse(enriched["pairs"][0]["right"]["exists"])
        self.assertEqual(enriched["summary"]["stale_indexed_dish_count"], 1)

    def test_confusion_report_does_not_call_zero_comparisons_safe(self):
        dish = Dish(name="唯一菜品", price=10.0, category="荤菜", is_active=True)
        db.session.add(dish)
        db.session.commit()
        report = {
            "index_ready": True,
            "summary": {"analyzed_pair_count": 0, "high_risk_pair_count": 0, "medium_risk_pair_count": 0},
            "indexed_dishes": [{"dish_id": dish.id, "dish_name": dish.name, "sample_count": 1}],
            "pairs": [],
        }

        enriched = enrich_dish_confusion_report(report, pipeline="qwen")

        self.assertIn("尚未执行跨菜品对比", enriched["recommendations"][0])

    def test_batch_analyze_nutrition_returns_existing_active_task(self):
        task = TaskLog(
            task_type="dish_nutrition_analysis",
            status="running",
            total_count=3,
            success_count=1,
            meta={"status_text": "正在分析"},
        )
        db.session.add(task)
        db.session.commit()

        res = self.client.post(
            "/api/v1/dishes/batch-analyze-nutrition",
            headers=self._auth_headers(),
            json={},
        )

        self.assertEqual(res.status_code, 200)
        data = res.get_json()["data"]
        self.assertEqual(data["message"], "已有批量营养分析任务正在执行")
        self.assertEqual(data["task_id"], task.id)
        self.assertEqual(data["task"]["status"], "running")


if __name__ == "__main__":
    unittest.main()
