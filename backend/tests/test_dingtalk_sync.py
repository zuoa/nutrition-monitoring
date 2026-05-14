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

if "celery" not in sys.modules:
    celery_module = types.ModuleType("celery")
    schedules_module = types.ModuleType("celery.schedules")

    class _FakeTaskWrapper:
        def __init__(self, fn):
            self.run = fn
            self.delay = lambda *args, **kwargs: None

        def __call__(self, *args, **kwargs):
            return self.run(*args, **kwargs)

    class _FakeCelery:
        def __init__(self, *args, **kwargs):
            self.conf = {}

        def task(self, *args, **kwargs):
            def decorator(fn):
                return _FakeTaskWrapper(fn)
            return decorator

    def _fake_crontab(*args, **kwargs):
        return {"args": args, "kwargs": kwargs}

    celery_module.Celery = _FakeCelery
    schedules_module.crontab = _fake_crontab
    sys.modules["celery"] = celery_module
    sys.modules["celery.schedules"] = schedules_module

from app import db  # noqa: E402
import app.models  # noqa: F401,E402
from app.models import Department, RoleEnum, User  # noqa: E402
from app.tasks.sync import sync_dingtalk_org  # noqa: E402


class DingTalkSyncTests(unittest.TestCase):
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
        )
        db.init_app(cls.app)
        cls.app_context = cls.app.app_context()
        cls.app_context.push()
        db.create_all()

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        db.drop_all()
        cls.app_context.pop()

    def setUp(self):
        db.session.query(Department).delete()
        db.session.query(User).delete()
        db.session.commit()

    def tearDown(self):
        db.session.rollback()
        db.session.remove()

    def test_sync_reuses_unique_login_placeholder_with_same_name(self):
        placeholder = User(
            dingtalk_user_id="oauth-open-1",
            name="张三",
            role=RoleEnum.canteen_manager,
            is_active=True,
        )
        db.session.add(placeholder)
        db.session.commit()
        placeholder_id = placeholder.id

        class FakeDingTalk:
            def __init__(self, _cfg):
                pass

            def get_department_list(self):
                return []

            def get_department_users(self, dept_id, offset=0, size=100):
                return {
                    "errcode": 0,
                    "hasMore": False,
                    "userlist": [
                        {
                            "userid": "ding-user-1",
                            "name": "张三",
                            "title": "班主任",
                        },
                    ],
                }

        with mock.patch("app.services.dingtalk.DingTalkService", FakeDingTalk):
            synced = sync_dingtalk_org()

        self.assertEqual(synced, 1)
        users = User.query.filter_by(name="张三").all()
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0].id, placeholder_id)
        self.assertEqual(users[0].dingtalk_user_id, "ding-user-1")
        self.assertEqual(users[0].role, RoleEnum.canteen_manager)
        self.assertEqual(users[0].dept_id, "1")

    def test_sync_reactivates_existing_inactive_user(self):
        user = User(
            dingtalk_user_id="ding-user-1",
            name="张三",
            role=RoleEnum.canteen_manager,
            is_active=False,
        )
        db.session.add(user)
        db.session.commit()

        class FakeDingTalk:
            def __init__(self, _cfg):
                pass

            def get_department_list(self):
                return []

            def get_department_users(self, dept_id, offset=0, size=100):
                return {
                    "errcode": 0,
                    "hasMore": False,
                    "userlist": [
                        {
                            "userid": "ding-user-1",
                            "name": "张三",
                            "title": "班主任",
                        },
                    ],
                }

        with mock.patch("app.services.dingtalk.DingTalkService", FakeDingTalk):
            synced = sync_dingtalk_org()

        self.assertEqual(synced, 1)
        db.session.refresh(user)
        self.assertTrue(user.is_active)
        self.assertEqual(user.role, RoleEnum.canteen_manager)
        self.assertEqual(user.dept_id, "1")

    def test_sync_reads_root_department_when_department_list_is_empty(self):
        class FakeDingTalk:
            def __init__(self, _cfg):
                pass

            def get_department_list(self):
                return []

            def get_department_users(self, dept_id, offset=0, size=100):
                self.last_dept_id = dept_id
                return {
                    "errcode": 0,
                    "hasMore": False,
                    "userlist": [
                        {
                            "userid": "root-user-1",
                            "name": "根部门用户",
                            "title": "食堂负责人",
                        },
                    ],
                }

        with mock.patch("app.services.dingtalk.DingTalkService", FakeDingTalk):
            synced = sync_dingtalk_org()

        self.assertEqual(synced, 1)
        user = User.query.filter_by(dingtalk_user_id="root-user-1").first()
        self.assertIsNotNone(user)
        assert user is not None
        self.assertEqual(user.dept_id, "1")
        self.assertEqual(user.role, RoleEnum.canteen_manager)

        root = Department.query.filter_by(dingtalk_dept_id="1").first()
        self.assertIsNotNone(root)
        assert root is not None
        self.assertEqual(root.name, "根部门")
        self.assertIsNone(root.parent_dingtalk_dept_id)
        self.assertTrue(root.is_active)

    def test_sync_persists_department_tree(self):
        class FakeDingTalk:
            def __init__(self, _cfg):
                pass

            def get_department_list(self):
                return [
                    {"id": 10, "name": "小学部", "parentid": 1, "order": 2},
                    {"id": 11, "name": "一年级", "parentid": 10, "order": 1},
                ]

            def get_department_users(self, dept_id, offset=0, size=100):
                users_by_dept = {
                    10: [{"userid": "dept-user-10", "name": "部门用户", "title": "老师"}],
                    11: [{"userid": "dept-user-11", "name": "年级用户", "title": "年级组长"}],
                }
                return {
                    "errcode": 0,
                    "hasMore": False,
                    "userlist": users_by_dept.get(dept_id, []),
                }

        with mock.patch("app.services.dingtalk.DingTalkService", FakeDingTalk):
            synced = sync_dingtalk_org()

        self.assertEqual(synced, 2)
        departments = {
            department.dingtalk_dept_id: department
            for department in Department.query.order_by(Department.dingtalk_dept_id).all()
        }
        self.assertEqual(set(departments.keys()), {"1", "10", "11"})
        self.assertEqual(departments["10"].parent_dingtalk_dept_id, "1")
        self.assertEqual(departments["11"].parent_dingtalk_dept_id, "10")
        self.assertEqual(departments["11"].sort_order, 1)

        user = User.query.filter_by(dingtalk_user_id="dept-user-11").first()
        self.assertIsNotNone(user)
        assert user is not None
        self.assertEqual(user.dept_id, "11")
        self.assertEqual(user.dept_name, "一年级")

    def test_sync_raises_on_dingtalk_user_api_error(self):
        class FakeDingTalk:
            def __init__(self, _cfg):
                pass

            def get_department_list(self):
                return []

            def get_department_users(self, dept_id, offset=0, size=100):
                return {"errcode": 60011, "errmsg": "没有通讯录权限"}

        with mock.patch("app.services.dingtalk.DingTalkService", FakeDingTalk):
            with self.assertRaises(RuntimeError):
                sync_dingtalk_org()
