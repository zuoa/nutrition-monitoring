"""钉钉「家校通讯录 2.0（新教育）」客户端。

独立于企业通讯录的 ``DingTalkService``：本服务只负责学生侧——学校/校区/学段/
年级/班级组织树，以及班级内学生与监护人。

注意：家校通讯录 2.0 的端点需在钉钉应用后台开通「新教育」权限后才能调用，且
不同租户的 classic / custom 结构字段略有差异。此处按官方文档实现常用端点，并把
端点常量集中便于真实凭据接入时核对（见 plan「验证」第 2 步）。未配置凭据时提供
本地 mock 数据，方便开发与单测。
"""
import logging
import time

import requests

logger = logging.getLogger(__name__)

OAPI = "https://oapi.dingtalk.com"

# ---- 端点常量（接入时按真实「新教育」权限凭据核对）------------------------------
# 家校通讯录根部门（学校）固定为 dept_id=1
EP_DEPT_LIST = "/topapi/v2/department/listsub"           # 子部门列表
EP_DEPT_ROOT = "/topapi/v2/department/list"              # 列出全部部门（含根）
EP_USER_LIST = "/topapi/v2/user/list"                    # 部门下人员
EP_STUDENT_RELATIONS = "/topapi/edu/class/student/list"  # 班级内学生-家长关系


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class DingTalkEduService:
    def __init__(self, config: dict):
        self.app_key = (config.get("DINGTALK_APP_KEY") or "").strip()
        self.app_secret = config.get("DINGTALK_APP_SECRET") or ""
        self.mock = bool(config.get("DINGTALK_EDU_MOCK")) or not self.app_key or not self.app_secret
        self._access_token = None
        self._token_expires = 0

    # ---- access token（与企业应用共用 AppKey/Secret）----
    def get_access_token(self) -> str:
        if self._access_token and time.time() < self._token_expires - 60:
            return self._access_token
        resp = self._request(
            "GET", f"{OAPI}/gettoken",
            params={"appkey": self.app_key, "appsecret": self.app_secret},
        )
        data = resp.json()
        if data.get("errcode") != 0:
            raise RuntimeError(f"获取钉钉 access_token 失败：{data}")
        self._access_token = data["access_token"]
        self._token_expires = time.time() + data.get("expires_in", 7200)
        return self._access_token

    # ---- 组织树 ----
    def get_school_root(self) -> dict | None:
        """返回学校根节点 {node_id, name}（家校通讯录根部门 id=1）。"""
        if self.mock:
            return {"node_id": "1", "name": "（mock）示范学校"}
        token = self.get_access_token()
        # dept_id=1 为家校通讯录根（学校）
        resp = self._request("POST", f"{OAPI}{EP_DEPT_ROOT}", params={"access_token": token},
                             json={"dept_id": 1})
        data = resp.json()
        nodes = data.get("result") or data.get("department") or []
        root = next((n for n in nodes if _to_int(n.get("dept_id")) == 1), None)
        if not root and nodes:
            root = nodes[0]
        if not root:
            return None
        return {"node_id": str(root.get("dept_id")), "name": root.get("name", "")}

    def get_node_children(self, node_id: str) -> list[dict]:
        """返回某节点的子节点列表，归一为 {node_id, name, parent_id}。"""
        if self.mock:
            return _mock_children(node_id)
        token = self.get_access_token()
        out: list[dict] = []
        cursor = 0
        while True:
            resp = self._request("POST", f"{OAPI}{EP_DEPT_LIST}", params={"access_token": token},
                                 json={"dept_id": _to_int(node_id), "cursor": cursor, "size": 100})
            data = resp.json()
            if data.get("errcode") not in (0, None):
                raise RuntimeError(f"获取钉钉子部门失败：{data}")
            result = data.get("result") or []
            for n in result:
                out.append({
                    "node_id": str(n.get("dept_id")),
                    "name": n.get("name", ""),
                    "parent_id": str(n.get("parent_id") or node_id),
                })
            if not result or not data.get("has_more"):
                break
            cursor = data.get("next_cursor") or (cursor + len(result))
        return out

    def get_node_members(self, node_id: str) -> list[dict]:
        """返回班级节点下人员，归一为 {dingtalk_user_id, name, identity}。

        identity ∈ {student, parent, teacher, other}。家校通讯录中「人员」可同时具
        备多个身份；这里尽量按钉钉返回的 ``role``/``title`` 推断。
        """
        if self.mock:
            return _mock_members(node_id)
        token = self.get_access_token()
        out: list[dict] = []
        cursor = 0
        while True:
            resp = self._request("POST", f"{OAPI}{EP_USER_LIST}", params={"access_token": token},
                                 json={"dept_id": _to_int(node_id), "cursor": cursor, "size": 100})
            data = resp.json()
            if data.get("errcode") not in (0, None):
                raise RuntimeError(f"获取钉钉部门人员失败：{data}")
            users = data.get("result") or []
            for u in users:
                out.append({
                    "dingtalk_user_id": str(u.get("userid") or u.get("unionid") or ""),
                    "name": u.get("name", ""),
                    "identity": _infer_identity(u),
                    "mobile": u.get("mobile"),
                })
            if not users or not data.get("has_more"):
                break
            cursor = data.get("next_cursor") or (cursor + len(users))
        return out

    def get_student_relations(self, class_node_id: str) -> list[dict]:
        """返回班级内学生-家长关系，归一为 {student_user_id, student_name,
        guardian_user_id, guardian_name, relation}。"""
        if self.mock:
            return mock_relations(class_node_id)
        token = self.get_access_token()
        resp = self._request("POST", f"{OAPI}{EP_STUDENT_RELATIONS}", params={"access_token": token},
                             json={"class_id": _to_int(class_node_id)})
        data = resp.json()
        if data.get("errcode") not in (0, None):
            logger.warning("获取班级学生关系失败（端点可能需核对）：class=%s resp=%s", class_node_id, data)
            return []
        rows = data.get("result") or data.get("relations") or []
        out = []
        for r in rows:
            out.append({
                "student_user_id": str(r.get("student_userid") or r.get("student_user_id") or ""),
                "student_name": r.get("student_name", ""),
                "guardian_user_id": str(r.get("parent_userid") or r.get("guardian_user_id") or ""),
                "guardian_name": r.get("parent_name") or r.get("guardian_name") or "",
                "relation": r.get("relation") or r.get("role"),
            })
        return out

    # ---- HTTP ----
    def _request(self, method: str, url: str, max_retries: int = 3, **kwargs) -> requests.Response:
        last_exc = None
        for attempt in range(max_retries):
            try:
                resp = requests.request(method, url, timeout=10, **kwargs)
                resp.raise_for_status()
                return resp
            except requests.RequestException as exc:
                last_exc = exc
                wait = 2 ** attempt
                logger.warning("DingTalk edu API 重试 %s/%s after %ss：%s", attempt + 1, max_retries, wait, exc)
                time.sleep(wait)
        raise RuntimeError(f"DingTalk edu API 重试耗尽：{last_exc}")


def _infer_identity(user: dict) -> str:
    role = str(user.get("role") or "").lower()
    title = str(user.get("title") or "").lower()
    ext = user.get("ext") or {}
    if isinstance(ext, dict) and ext.get("student"):
        return "student"
    if "student" in role or "学生" in (user.get("title") or ""):
        return "student"
    if "parent" in role or "家長" in title or "家长" in title:
        return "parent"
    if "teacher" in role or "老师" in title or "教师" in title:
        return "teacher"
    return "other"


# ---- mock 数据（开发/单测用）---------------------------------------------------
def _mock_children(node_id: str) -> list[dict]:
    tree = {
        "1": [{"node_id": "11", "name": "示范校区", "parent_id": "1"}],
        "11": [{"node_id": "111", "name": "初中部", "parent_id": "11"}],
        "111": [{"node_id": "111G7", "name": "七年级", "parent_id": "111"}],
        "111G7": [
            {"node_id": "111G7C1", "name": "七年级（1）班", "parent_id": "111G7"},
            {"node_id": "111G7C2", "name": "七年级（2）班", "parent_id": "111G7"},
        ],
    }
    return tree.get(str(node_id), [])


def _mock_members(node_id: str) -> list[dict]:
    members = {
        "111G7C1": [
            {"dingtalk_user_id": "S001", "name": "林晓彤", "identity": "student"},
            {"dingtalk_user_id": "P001", "name": "林父", "identity": "parent", "relation": "父"},
        ],
        "111G7C2": [
            {"dingtalk_user_id": "S006", "name": "苏芷晴", "identity": "student"},
        ],
    }
    return members.get(str(node_id), [])


def mock_relations(node_id: str) -> list[dict]:
    rels = {
        "111G7C1": [{
            "student_user_id": "S001", "student_name": "林晓彤",
            "guardian_user_id": "P001", "guardian_name": "林父", "relation": "父",
        }],
    }
    return rels.get(str(node_id), [])
