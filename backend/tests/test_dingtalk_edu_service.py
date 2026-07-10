import os
import sys
import types
import unittest


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

from app.modules.students.services.dingtalk_edu import DingTalkEduService  # noqa: E402


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeDingTalkEdu(DingTalkEduService):
    def __init__(self, responses):
        super().__init__({
            "DINGTALK_APP_KEY": "test-key",
            "DINGTALK_APP_SECRET": "test-secret",
        })
        self._responses = list(responses)
        self.calls = []

    def get_access_token(self) -> str:
        return "test-token"

    def _request(self, method: str, url: str, max_retries: int = 3, **kwargs):
        self.calls.append((method, url, kwargs))
        return _FakeResponse(self._responses.pop(0))


class DingTalkEduServiceTests(unittest.TestCase):
    def test_get_school_root_uses_local_synthetic_root(self):
        service = _FakeDingTalkEdu([])

        root = service.get_school_root()

        self.assertEqual(root, {"node_id": "1", "name": "家校通讯录"})
        self.assertEqual(service.calls, [])

    def test_get_node_children_reads_nested_result_list(self):
        service = _FakeDingTalkEdu([{
            "errcode": 0,
            "result": {
                "details": [{
                    "dept_id": 11,
                    "name": "示范校区",
                    "dept_type": "campus",
                    "contact_type": "classic",
                    "feature": "{\"school_code\":\"S1\"}",
                }],
                "has_more": False,
                "super_id": 1,
            },
        }])

        children = service.get_node_children("1")

        self.assertEqual(children, [{
            "node_id": "11",
            "name": "示范校区",
            "parent_id": "1",
            "dept_type": "campus",
            "contact_type": "classic",
            "feature": {"school_code": "S1"},
        }])
        self.assertIn("/topapi/edu/dept/list", service.calls[0][1])
        self.assertEqual(service.calls[0][2]["json"], {"page_no": 1, "page_size": 30})

    def test_get_node_children_sends_super_id_for_non_root(self):
        service = _FakeDingTalkEdu([{
            "errcode": 0,
            "result": {
                "details": [{"dept_id": 111, "name": "初中部", "dept_type": "period"}],
                "has_more": False,
                "super_id": 11,
            },
        }])

        children = service.get_node_children("11")

        self.assertEqual(children[0]["node_id"], "111")
        self.assertEqual(children[0]["parent_id"], "11")
        self.assertEqual(service.calls[0][2]["json"], {
            "page_no": 1,
            "page_size": 30,
            "super_id": 11,
        })

    def test_get_node_children_ignores_negative_department_ids(self):
        service = _FakeDingTalkEdu([{
            "errcode": 0,
            "result": {
                "details": [
                    {"dept_id": -7, "name": "非法节点"},
                    {"dept_id": 11, "name": "示范校区"},
                ],
                "has_more": False,
                "super_id": 1,
            },
        }])

        children = service.get_node_children("1")

        self.assertEqual(children, [{
            "node_id": "11",
            "name": "示范校区",
            "parent_id": "1",
            "dept_type": None,
            "contact_type": None,
            "feature": {},
        }])

    def test_get_node_children_does_not_treat_generic_id_as_department_id(self):
        service = _FakeDingTalkEdu([{
            "errcode": 0,
            "result": {
                "details": [
                    {"id": -7, "name": "非部门对象"},
                    {"dept_id": 11, "name": "示范校区"},
                ],
                "has_more": False,
                "super_id": 1,
            },
        }])

        children = service.get_node_children("1")

        self.assertEqual(children, [{
            "node_id": "11",
            "name": "示范校区",
            "parent_id": "1",
            "dept_type": None,
            "contact_type": None,
            "feature": {},
        }])

    def test_get_node_children_skips_invalid_request_department_id(self):
        service = _FakeDingTalkEdu([])

        children = service.get_node_children("-7")

        self.assertEqual(children, [])
        self.assertEqual(service.calls, [])

    def test_get_node_members_reads_nested_user_list(self):
        service = _FakeDingTalkEdu([
            {
                "errcode": 0,
                "result": {
                    "details": [{
                        "userid": "S001",
                        "name": "林晓彤",
                        "role": "student",
                        "feature": "{\"student_no\":\"2026001\"}",
                    }],
                    "has_more": False,
                },
            },
            {
                "errcode": 0,
                "result": {
                    "details": [{"userid": "P001", "name": "林父", "role": "guardian"}],
                    "has_more": False,
                },
            },
        ])

        members = service.get_node_members("111001")

        self.assertEqual(members, [
            {
                "dingtalk_user_id": "S001",
                "name": "林晓彤",
                "identity": "student",
                "mobile": None,
                "student_no": "2026001",
                "feature": {"student_no": "2026001"},
            },
            {
                "dingtalk_user_id": "P001",
                "name": "林父",
                "identity": "parent",
                "mobile": None,
                "student_no": None,
                "feature": {},
            },
        ])
        self.assertEqual(service.calls[0][2]["json"], {
            "class_id": 111001,
            "role": "student",
            "page_no": 1,
            "page_size": 30,
        })
        self.assertEqual(service.calls[1][2]["json"], {
            "class_id": 111001,
            "role": "guardian",
            "page_no": 1,
            "page_size": 30,
        })

    def test_member_and_relation_requests_skip_invalid_department_id(self):
        service = _FakeDingTalkEdu([])

        self.assertEqual(service.get_node_members("-7"), [])
        self.assertEqual(service.get_student_relations("-7"), [])
        self.assertEqual(service.calls, [])

    def test_get_student_relations_reads_nested_relation_list_with_string_success(self):
        service = _FakeDingTalkEdu([{
            "errcode": "0",
            "result": {
                "relations": [{
                    "to_userid": "S001",
                    "from_userid": "P001",
                    "relation_name": "妈妈",
                }],
                "has_more": False,
            },
        }])

        relations = service.get_student_relations("111001")

        self.assertEqual(relations, [{
            "student_user_id": "S001",
            "student_name": "",
            "guardian_user_id": "P001",
            "guardian_name": "",
            "relation": "妈妈",
        }])
        self.assertIn("/topapi/edu/user/relation/list", service.calls[0][1])
        self.assertEqual(service.calls[0][2]["json"], {
            "class_id": 111001,
            "page_no": 1,
            "page_size": 30,
        })


if __name__ == "__main__":
    unittest.main()
