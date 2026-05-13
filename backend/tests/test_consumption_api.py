import os
import sys
import io
import tempfile
import types
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone

from flask import Flask
from openpyxl import load_workbook


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

try:
    import chardet  # noqa: F401
except ImportError:
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
        runtime_config = tempfile.NamedTemporaryFile(delete=False)
        runtime_config.close()
        cls.runtime_config_path = runtime_config.name
        cls.app = Flask(__name__)
        cls.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SECRET_KEY="test-secret",
            JWT_ALGORITHM="HS256",
            JWT_ACCESS_TOKEN_EXPIRES=timedelta(hours=1),
            LOCAL_RUNTIME_CONFIG_PATH=cls.runtime_config_path,
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
        try:
            os.unlink(cls.runtime_config_path)
        except OSError:
            pass

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

    def test_import_uses_time_based_batch_id(self):
        content = (
            "学号,学生姓名,消费时间,消费金额,流水号,交易地点\n"
            "230501,张三,2026-03-31 12:05:30,12.50,TX202603310001,1\n"
        ).encode("gbk")

        fake_matching = types.ModuleType("app.tasks.matching")
        delay_mock = mock.Mock()
        fake_matching.run_matching_for_batch = types.SimpleNamespace(delay=delay_mock)

        with mock.patch.dict(sys.modules, {"app.tasks.matching": fake_matching}):
            res = self.client.post(
                "/api/v1/consumption/import",
                data={"file": (io.BytesIO(content), "records.csv")},
                content_type="multipart/form-data",
                headers=self._auth_headers(),
            )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertEqual(payload["code"], 0)
        batch_id = payload["data"]["batch_id"]
        self.assertRegex(batch_id, r"^\d{17}$")
        self.assertEqual(ConsumptionRecord.query.one().import_batch, batch_id)
        self.assertEqual(ConsumptionRecord.query.one().channel_id, "1")
        delay_mock.assert_called_once_with(batch_id)

    def test_list_records_filters_by_import_batch(self):
        db.session.add_all([
            ConsumptionRecord(
                student_no="230501",
                student_name="张三",
                transaction_time=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc),
                amount=12.0,
                transaction_id="tx-batch-001",
                import_batch="20260331120000001",
            ),
            ConsumptionRecord(
                student_no="230502",
                student_name="李四",
                transaction_time=datetime(2026, 3, 31, 12, 5, tzinfo=timezone.utc),
                amount=8.0,
                transaction_id="tx-batch-002",
                import_batch="20260331120500001",
            ),
        ])
        db.session.commit()

        res = self.client.get(
            "/api/v1/consumption/records?batch=20260331120000001",
            headers=self._auth_headers(),
        )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["data"]["total"], 1)
        self.assertEqual(payload["data"]["items"][0]["transaction_id"], "tx-batch-001")

    def test_list_record_batches_groups_imports(self):
        db.session.add_all([
            ConsumptionRecord(
                student_no="230501",
                student_name="张三",
                transaction_time=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc),
                amount=12.0,
                transaction_id="tx-group-001",
                import_batch="20260331120000001",
            ),
            ConsumptionRecord(
                student_no="230502",
                student_name="李四",
                transaction_time=datetime(2026, 3, 31, 12, 5, tzinfo=timezone.utc),
                amount=8.0,
                transaction_id="tx-group-002",
                import_batch="20260331120000001",
            ),
            ConsumptionRecord(
                student_no="230503",
                student_name="王五",
                transaction_time=datetime(2026, 3, 31, 12, 10, tzinfo=timezone.utc),
                amount=10.0,
                transaction_id="tx-group-003",
                import_batch="20260331121000001",
            ),
        ])
        db.session.commit()

        res = self.client.get(
            "/api/v1/consumption/records/batches",
            headers=self._auth_headers(),
        )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertEqual(payload["code"], 0)
        batches = {item["batch_id"]: item for item in payload["data"]["items"]}
        self.assertEqual(batches["20260331120000001"]["record_count"], 2)
        self.assertEqual(batches["20260331120000001"]["total_amount"], 20.0)
        self.assertEqual(batches["20260331121000001"]["record_count"], 1)

    def test_delete_record_removes_related_match(self):
        record = ConsumptionRecord(
            student_no="230501",
            student_name="张三",
            transaction_time=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc),
            amount=12.0,
            transaction_id="tx-delete-001",
            import_batch="20260331120000001",
        )
        db.session.add(record)
        db.session.commit()
        db.session.add(MatchResult(
            consumption_record_id=record.id,
            status=MatchStatusEnum.unmatched_record,
            match_date=record.transaction_time.date(),
        ))
        db.session.commit()

        res = self.client.delete(
            f"/api/v1/consumption/records/{record.id}",
            headers=self._auth_headers(),
        )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["data"]["deleted"], 1)
        self.assertEqual(ConsumptionRecord.query.count(), 0)
        self.assertEqual(MatchResult.query.count(), 0)

    def test_delete_batch_removes_all_batch_records_and_matches(self):
        keep = ConsumptionRecord(
            student_no="230501",
            student_name="张三",
            transaction_time=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc),
            amount=12.0,
            transaction_id="tx-keep-001",
            import_batch="20260331120000001",
        )
        delete_one = ConsumptionRecord(
            student_no="230502",
            student_name="李四",
            transaction_time=datetime(2026, 3, 31, 12, 5, tzinfo=timezone.utc),
            amount=8.0,
            transaction_id="tx-delete-batch-001",
            import_batch="20260331120500001",
        )
        delete_two = ConsumptionRecord(
            student_no="230503",
            student_name="王五",
            transaction_time=datetime(2026, 3, 31, 12, 10, tzinfo=timezone.utc),
            amount=10.0,
            transaction_id="tx-delete-batch-002",
            import_batch="20260331120500001",
        )
        db.session.add_all([keep, delete_one, delete_two])
        db.session.commit()
        db.session.add_all([
            MatchResult(
                consumption_record_id=delete_one.id,
                status=MatchStatusEnum.unmatched_record,
                match_date=delete_one.transaction_time.date(),
            ),
            MatchResult(
                consumption_record_id=delete_two.id,
                status=MatchStatusEnum.unmatched_record,
                match_date=delete_two.transaction_time.date(),
            ),
        ])
        db.session.commit()

        res = self.client.delete(
            "/api/v1/consumption/records/batches/20260331120500001",
            headers=self._auth_headers(),
        )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["data"]["deleted"], 2)
        self.assertEqual(ConsumptionRecord.query.count(), 1)
        self.assertEqual(ConsumptionRecord.query.one().transaction_id, "tx-keep-001")
        self.assertEqual(MatchResult.query.count(), 0)

    def test_import_settings_can_be_updated(self):
        res = self.client.put(
            "/api/v1/consumption/import-settings",
            json={"allowed_locations": ["1", " 2 "]},
            headers=self._auth_headers(),
        )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["data"]["allowed_locations"], ["1", "2"])

        res = self.client.get(
            "/api/v1/consumption/import-settings",
            headers=self._auth_headers(),
        )

        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["data"]["allowed_locations"], ["1", "2"])

    def test_download_import_template_returns_excel_file(self):
        res = self.client.get(
            "/api/v1/consumption/import-template",
            headers=self._auth_headers(),
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            res.mimetype,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn("attachment", res.headers.get("Content-Disposition", ""))

        workbook = load_workbook(io.BytesIO(res.data))
        sheet = workbook.active
        self.assertEqual(sheet.title, "消费记录导入模板")
        self.assertEqual(
            [sheet.cell(row=1, column=col).value for col in range(1, 7)],
            ["学号/消费卡号 *", "学生姓名", "消费时间 *", "消费金额 *", "流水号 *", "通道 *"],
        )
        self.assertEqual(sheet.cell(row=2, column=1).value, "230501")
        self.assertEqual(sheet.cell(row=4, column=1).value[:3], "说明：")

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
        dish = Dish(
            name="青椒土豆丝",
            price=6.0,
            category=CategoryEnum.vegetable,
            is_active=True,
        )
        db.session.add(dish)
        db.session.add(image)
        db.session.commit()

        recognition = DishRecognition(
            image_id=image.id,
            dish_id=dish.id,
            dish_name_raw=dish.name,
            confidence=0.91,
            is_low_confidence=False,
            is_manual=False,
            model_version="test",
        )

        match = MatchResult(
            image_id=image.id,
            status=MatchStatusEnum.unmatched_image,
            match_date=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc).date(),
        )
        db.session.add(recognition)
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
        self.assertEqual(item["image"]["recognitions"][0]["dish_price"], 6.0)

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
        self.assertEqual(item["image"]["recognitions"][0]["dish_price"], 12.0)


if __name__ == "__main__":
    unittest.main()
