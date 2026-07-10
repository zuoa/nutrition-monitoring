"""学生管理模块：组织同步与学生/监护人同步（含本地覆盖保留策略）的单测。"""
import os
import sys
import types
import unittest

from flask import Flask


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# ── 与其它测试一致的缺失依赖桩 ────────────────────────────────────────────────
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
from app.models import Student, User, RoleEnum  # noqa: E402
from app.modules.students.models.organization import (  # noqa: E402
    School, Campus, Stage, Grade, Class,
)
from app.modules.students.models.guardian import Guardian  # noqa: E402
from app.modules.students.services.org_sync_service import OrgSyncService  # noqa: E402
from app.modules.students.services.student_sync_service import StudentSyncService  # noqa: E402


class FakeEdu:
    """可控的家校通讯录桩，按 node_id 返回树/成员/关系。"""

    def __init__(self, school, children, members, relations):
        self._school = school
        self._children = children
        self._members = members
        self._relations = relations

    def get_school_root(self):
        return self._school

    def get_node_children(self, node_id):
        return self._children.get(str(node_id), [])

    def get_node_members(self, node_id):
        return self._members.get(str(node_id), [])

    def get_student_relations(self, node_id):
        return self._relations.get(str(node_id), [])


# classic 5 层：学校 1 → 校区 11 → 学段 111 → 年级 111G7 → 班级 111G7C1
TREE_CHILDREN = {
    "1": [{"node_id": "11", "name": "示范校区", "parent_id": "1"}],
    "11": [{"node_id": "111", "name": "初中部", "parent_id": "11"}],
    "111": [{"node_id": "111G7", "name": "七年级", "parent_id": "111"}],
    "111G7": [{"node_id": "111G7C1", "name": "七年级（1）班", "parent_id": "111G7"}],
}


def _make_edu(members=None, relations=None):
    return FakeEdu(
        school={"node_id": "1", "name": "示范学校"},
        children=TREE_CHILDREN,
        members=members or {
            "111G7C1": [
                {"dingtalk_user_id": "S001", "name": "林晓彤", "identity": "student"},
                {"dingtalk_user_id": "P001", "name": "林父", "identity": "parent"},
            ],
        },
        relations=relations or {
            "111G7C1": [{
                "student_user_id": "S001", "student_name": "林晓彤",
                "guardian_user_id": "P001", "guardian_name": "林父", "relation": "父",
            }],
        },
    )


class StudentSyncServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask(__name__)
        cls.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
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
        for model in (Guardian, Student, Class, Grade, Stage, Campus, School, User):
            model.query.delete()
        db.session.commit()

    def tearDown(self):
        db.session.rollback()

    def _sync_org(self, edu=None):
        OrgSyncService(edu or _make_edu()).sync()

    # ---- 组织节点映射 ----
    def test_org_sync_maps_classic_five_levels(self):
        self._sync_org()
        self.assertEqual(School.query.count(), 1)
        self.assertEqual(Campus.query.count(), 1)
        self.assertEqual(Stage.query.count(), 1)
        self.assertEqual(Grade.query.count(), 1)
        self.assertEqual(Class.query.count(), 1)

        cls = Class.query.filter_by(dingtalk_node_id="111G7C1").one()
        self.assertEqual(cls.name, "七年级（1）班")
        self.assertEqual(cls.grade.dingtalk_node_id, "111G7")
        self.assertEqual(cls.grade.stage.dingtalk_node_id, "111")
        self.assertEqual(cls.grade.stage.campus.dingtalk_node_id, "11")
        self.assertEqual(cls.grade.stage.campus.school.dingtalk_node_id, "1")

    def test_org_sync_raises_when_school_root_missing(self):
        edu = FakeEdu(school=None, children={}, members={}, relations={})

        with self.assertRaisesRegex(RuntimeError, "学校根节点"):
            OrgSyncService(edu).sync()

    # ---- 本地覆盖保留 ----
    def test_sync_preserves_local_fields_and_overwrites_managed(self):
        self._sync_org()
        cls = Class.query.filter_by(dingtalk_node_id="111G7C1").one()
        # 预置一个本地学生（已有卡号/学号/姓名）
        db.session.add(Student(
            student_no="OLD_NO", name="旧名", card_no="OLD_CARD",
            dingtalk_user_id="S001", class_id=cls.id, is_active=True,
        ))
        db.session.commit()

        StudentSyncService(_make_edu()).sync()

        s = Student.query.filter_by(dingtalk_user_id="S001").one()
        self.assertEqual(s.name, "林晓彤")          # 托管字段被覆盖
        self.assertEqual(s.student_no, "OLD_NO")     # 本地字段保留
        self.assertEqual(s.card_no, "OLD_CARD")      # 本地字段保留
        self.assertEqual(s.class_id, cls.id)
        self.assertTrue(s.is_active)

    def test_sync_creates_new_student_with_placeholder_student_no(self):
        self._sync_org()
        StudentSyncService(_make_edu()).sync()
        s = Student.query.filter_by(dingtalk_user_id="S001").one()
        self.assertEqual(s.name, "林晓彤")
        self.assertEqual(s.student_no, "S001")  # 占位学号 = 钉钉 id
        self.assertTrue(s.is_active)

    # ---- 本地禁用不被同步复活 ----
    def test_sync_does_not_revive_locally_disabled_student(self):
        self._sync_org()
        cls = Class.query.filter_by(dingtalk_node_id="111G7C1").one()
        s = Student(student_no="OLD_NO", name="林晓彤", dingtalk_user_id="S001",
                    class_id=cls.id, is_locally_disabled=True, is_active=False)
        db.session.add(s)
        db.session.commit()

        StudentSyncService(_make_edu()).sync()

        s = Student.query.filter_by(dingtalk_user_id="S001").one()
        self.assertTrue(s.is_locally_disabled)
        self.assertFalse(s.is_active)  # 不会被同步复活

    # ---- 监护人 upsert + 关联 parent 用户 ----
    def test_sync_upserts_guardian_and_links_parent_user(self):
        self._sync_org()
        parent = User(name="林父", role=RoleEnum.parent, dingtalk_user_id="P001",
                      student_ids=[], is_active=True)
        db.session.add(parent)
        db.session.commit()

        StudentSyncService(_make_edu()).sync()

        s = Student.query.filter_by(dingtalk_user_id="S001").one()
        g = Guardian.query.filter_by(student_id=s.id, dingtalk_user_id="P001").one()
        self.assertEqual(g.name, "林父")
        self.assertEqual(g.user_id, parent.id)
        self.assertIn(s.id, parent.student_ids)

    # ---- 单班级入库失败（占位学号撞唯一约束）不得作废整次同步 ----
    def test_sync_per_class_savepoint_isolates_failure(self):
        # 两个班级：C1 的钉钉学生 S001 与一个本地学生的 student_no 撞车 → 入库失败；
        # C2 的学生 S006 正常。验证 C1 回滚、C2 仍入库、sync 不抛异常。
        two_class_children = {
            "1": [{"node_id": "11", "name": "示范校区", "parent_id": "1"}],
            "11": [{"node_id": "111", "name": "初中部", "parent_id": "11"}],
            "111": [{"node_id": "111G7", "name": "七年级", "parent_id": "111"}],
            "111G7": [
                {"node_id": "111G7C1", "name": "七年级（1）班", "parent_id": "111G7"},
                {"node_id": "111G7C2", "name": "七年级（2）班", "parent_id": "111G7"},
            ],
        }
        edu = _make_edu(
            members={
                "111G7C1": [{"dingtalk_user_id": "S001", "name": "林晓彤", "identity": "student"}],
                "111G7C2": [{"dingtalk_user_id": "S006", "name": "苏芷晴", "identity": "student"}],
            },
            relations={},
        )
        edu._children = two_class_children  # 覆盖默认单班级树
        self._sync_org(edu)

        # 预置本地学生占用了 student_no="S001"（dingtalk_user_id 留空，同步不会匹配到它）
        db.session.add(Student(
            student_no="S001", name="本地占位", class_id=None, is_active=True,
        ))
        db.session.commit()

        # 同步不得抛异常
        stats = StudentSyncService(edu).sync()

        # C2 的学生正常入库
        s006 = Student.query.filter_by(dingtalk_user_id="S006").first()
        self.assertIsNotNone(s006)
        self.assertEqual(s006.name, "苏芷晴")
        # C1 的同步学生因撞约束回滚，不存在 dingtalk_user_id="S001" 的学生
        self.assertIsNone(Student.query.filter_by(dingtalk_user_id="S001").first())
        # 本地占位学生仍完好
        local = Student.query.filter_by(student_no="S001").one()
        self.assertEqual(local.name, "本地占位")
        # 统计只计入成功的 C2
        self.assertEqual(stats["students"], 1)


if __name__ == "__main__":
    unittest.main()
