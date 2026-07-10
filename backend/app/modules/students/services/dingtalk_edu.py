"""钉钉「家校通讯录 2.0（新教育）」客户端。

独立于企业通讯录的 ``DingTalkService``：本服务只负责学生侧——学校/校区/学段/
年级/班级组织树，以及班级内学生与监护人。

注意：家校通讯录 2.0 的端点需在钉钉应用后台开通「新教育」权限后才能调用，且
不同租户的 classic / custom 结构字段略有差异。此处按官方文档实现常用端点，
未配置凭据时提供本地 mock 数据，方便开发与单测。
"""
import json
import logging
import time
from copy import deepcopy

import requests

logger = logging.getLogger(__name__)

OAPI = "https://oapi.dingtalk.com"

# 钉钉教育家校通讯录接口 page_size 最大为 30。
PAGE_SIZE = 30

# ---- 端点常量 ---------------------------------------------------------------
# 本地用 dept_id=1 作为家校通讯录同步的合成根节点；真实家校子节点通过
# /topapi/edu/dept/list 不传 super_id 获取。
ROOT_DEPT_ID = 1
EP_DEPT_LIST = "/topapi/edu/dept/list"                   # 家校部门列表
EP_USER_LIST = "/topapi/edu/user/list"                   # 班级人员列表
EP_STUDENT_RELATIONS = "/topapi/edu/user/relation/list"  # 班级内学生-家长关系


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _positive_int_id(value) -> int | None:
    int_value = _to_int(value, default=None)
    if int_value is None or int_value <= 0:
        return None
    return int_value


class DingTalkEduService:
    def __init__(self, config: dict):
        self.app_key = (config.get("DINGTALK_APP_KEY") or "").strip()
        self.app_secret = config.get("DINGTALK_APP_SECRET") or ""
        self.mock = bool(config.get("DINGTALK_EDU_MOCK")) or not self.app_key or not self.app_secret
        self.root_name = (config.get("DINGTALK_EDU_ROOT_NAME") or "家校通讯录").strip()
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
            logger.error("获取钉钉 access_token 失败，响应：%s", _safe_response_for_log(data))
            raise RuntimeError(f"获取钉钉 access_token 失败：{data}")
        self._access_token = data["access_token"]
        self._token_expires = time.time() + data.get("expires_in", 7200)
        return self._access_token

    # ---- 组织树 ----
    def get_school_root(self) -> dict | None:
        """返回本地合成学校根节点 {node_id, name}。

        家校通讯录 2.0 的部门列表接口不需要先调用企业通讯录根部门详情；
        不传 ``super_id`` 即可获取家校一级节点。企业通讯录根部门下的
        ``dept_id=-7`` 是“家校通讯录”虚拟入口，不能继续用企业部门接口向下查询。
        """
        if self.mock:
            return {"node_id": "1", "name": "（mock）示范学校"}
        return {"node_id": str(ROOT_DEPT_ID), "name": self.root_name}

    def get_node_children(self, node_id: str) -> list[dict]:
        """返回某节点的子节点列表，归一为 {node_id, name, parent_id}。"""
        if self.mock:
            return _mock_children(node_id)
        parent_id = _positive_int_id(node_id)
        if parent_id is None:
            logger.warning("跳过非法钉钉家校部门子级请求：dept_id=%s", node_id)
            return []
        token = self.get_access_token()
        out: list[dict] = []
        page_no = 1
        while True:
            payload = {"page_no": page_no, "page_size": PAGE_SIZE}
            if parent_id != ROOT_DEPT_ID:
                payload["super_id"] = parent_id
            resp = self._request("POST", f"{OAPI}{EP_DEPT_LIST}", params={"access_token": token},
                                 json=payload)
            data = resp.json()
            _ensure_dingtalk_success(data, "获取钉钉子部门")
            result = _extract_items(data, ("details", "list", "dept_list", "departments", "department"))
            response_parent_id = _page_value(data, "super_id", "superId") or node_id
            for n in result:
                if not isinstance(n, dict):
                    continue
                child_id = _department_node_id(n)
                if not child_id:
                    logger.warning(
                        "忽略缺少 dept_id 的钉钉子部门节点：parent_id=%s node=%s",
                        node_id,
                        _safe_response_for_log(n),
                    )
                    continue
                if _positive_int_id(child_id) is None:
                    logger.warning(
                        "忽略钉钉非法子部门节点：dept_id=%s parent_id=%s name=%s node=%s",
                        child_id,
                        node_id,
                        n.get("name", ""),
                        _safe_response_for_log(n),
                    )
                    continue
                out.append({
                    "node_id": child_id,
                    "name": n.get("name") or n.get("nick") or "",
                    "parent_id": str(n.get("parent_id") or n.get("parentid") or n.get("parentId") or response_parent_id),
                    "dept_type": n.get("dept_type") or n.get("deptType"),
                    "contact_type": n.get("contact_type") or n.get("contactType"),
                    "feature": _parse_feature(n.get("feature")),
                })
            if not _page_has_more(data, len(result)):
                break
            page_no += 1
        return out

    def get_node_members(self, node_id: str) -> list[dict]:
        """返回班级节点下人员，归一为 {dingtalk_user_id, name, identity}。

        identity ∈ {student, parent, teacher, other}。家校通讯录中「人员」可同时具
        备多个身份；这里尽量按钉钉返回的 ``role``/``title`` 推断。
        """
        if self.mock:
            return _mock_members(node_id)
        class_id = _positive_int_id(node_id)
        if class_id is None:
            logger.warning("跳过非法钉钉班级人员请求：class_id=%s", node_id)
            return []
        token = self.get_access_token()
        out: list[dict] = []
        for role in ("student", "guardian"):
            page_no = 1
            while True:
                payload = {
                    "class_id": class_id,
                    "role": role,
                    "page_no": page_no,
                    "page_size": PAGE_SIZE,
                }
                resp = self._request("POST", f"{OAPI}{EP_USER_LIST}", params={"access_token": token},
                                     json=payload)
                data = resp.json()
                _ensure_dingtalk_success(data, "获取钉钉班级人员")
                users = _extract_items(data, ("details", "list", "users", "userlist"))
                for u in users:
                    if not isinstance(u, dict):
                        continue
                    user_id = str(u.get("userid") or u.get("unionid") or "").strip()
                    if not user_id:
                        continue
                    feature = _parse_feature(u.get("feature"))
                    identity = _normalize_edu_role(u.get("role") or role)
                    if identity == "other":
                        identity = _infer_identity(u)
                    out.append({
                        "dingtalk_user_id": user_id,
                        "name": u.get("name", ""),
                        "identity": identity,
                        "mobile": u.get("mobile"),
                        "student_no": feature.get("student_no"),
                        "feature": feature,
                    })
                if not _page_has_more(data, len(users)):
                    break
                page_no += 1
        return out

    def get_student_relations(self, class_node_id: str) -> list[dict]:
        """返回班级内学生-家长关系，归一为 {student_user_id, student_name,
        guardian_user_id, guardian_name, relation}。"""
        if self.mock:
            return mock_relations(class_node_id)
        class_id = _positive_int_id(class_node_id)
        if class_id is None:
            logger.warning("跳过非法钉钉班级关系请求：class_id=%s", class_node_id)
            return []
        token = self.get_access_token()
        out = []
        page_no = 1
        while True:
            resp = self._request("POST", f"{OAPI}{EP_STUDENT_RELATIONS}", params={"access_token": token},
                                 json={"class_id": class_id, "page_no": page_no, "page_size": PAGE_SIZE})
            data = resp.json()
            if data.get("errcode") not in (0, "0", None):
                logger.warning("获取班级学生关系失败：class=%s resp=%s", class_node_id, _safe_response_for_log(data))
                return out
            rows = _extract_items(data, ("relations", "list", "relation_list"))
            for r in rows:
                if not isinstance(r, dict):
                    continue
                out.append({
                    "student_user_id": str(r.get("to_userid") or r.get("to_user_id") or r.get("student_userid") or r.get("student_user_id") or ""),
                    "student_name": r.get("student_name", ""),
                    "guardian_user_id": str(r.get("from_userid") or r.get("from_user_id") or r.get("parent_userid") or r.get("guardian_user_id") or ""),
                    "guardian_name": r.get("parent_name") or r.get("guardian_name") or "",
                    "relation": r.get("relation_name") or r.get("relation") or r.get("role") or r.get("relation_code"),
                })
            if not _page_has_more(data, len(rows)):
                break
            page_no += 1
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
                response = getattr(exc, "response", None)
                if response is not None:
                    logger.warning(
                        "DingTalk edu API HTTP 异常：method=%s url=%s status=%s body=%s",
                        method,
                        url,
                        response.status_code,
                        _response_body_for_log(response),
                    )
                wait = 2 ** attempt
                logger.warning("DingTalk edu API 重试 %s/%s after %ss：%s", attempt + 1, max_retries, wait, exc)
                time.sleep(wait)
        raise RuntimeError(f"DingTalk edu API 重试耗尽：{last_exc}")


def _ensure_dingtalk_success(data: dict, action: str):
    if not isinstance(data, dict):
        logger.error("%s失败：钉钉返回格式无效，响应：%r", action, data)
        raise RuntimeError(f"{action}失败：钉钉返回格式无效")
    errcode = data.get("errcode", 0)
    if errcode not in (0, "0", None):
        logger.error("%s失败：钉钉响应：%s", action, _safe_response_for_log(data))
        raise RuntimeError(f"{action}失败：{data}")


def _department_node_id(dept: dict) -> str:
    # Education department endpoints use dept_id/deptid/deptId. A generic "id"
    # may belong to another object in mixed responses and must not be promoted
    # to a department id; doing so can enqueue invalid ids such as -7.
    return str(dept.get("dept_id") or dept.get("deptid") or dept.get("deptId") or "").strip()


def _extract_items(data: dict, keys: tuple[str, ...]) -> list:
    result = data.get("result")
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in keys:
            value = result.get(key)
            if isinstance(value, list):
                return value
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _page_value(data: dict, *keys):
    result = data.get("result")
    if isinstance(result, dict):
        for key in keys:
            if key in result:
                return result.get(key)
    for key in keys:
        if key in data:
            return data.get(key)
    return None


def _page_has_more(data: dict, item_count: int) -> bool:
    value = _page_value(data, "has_more", "hasMore")
    if value is None:
        return item_count >= PAGE_SIZE
    if isinstance(value, str):
        return value.lower() in {"true", "1"}
    return bool(value)


def _parse_feature(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_response_for_log(data):
    redacted = deepcopy(data)
    _redact_sensitive_values(redacted)
    return redacted


def _redact_sensitive_values(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in {"access_token", "accesstoken"}:
                value[key] = "***"
            else:
                _redact_sensitive_values(item)
    elif isinstance(value, list):
        for item in value:
            _redact_sensitive_values(item)


def _response_body_for_log(response: requests.Response) -> object:
    try:
        return _safe_response_for_log(response.json())
    except ValueError:
        return (response.text or "")[:1000]


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


def _normalize_edu_role(role) -> str:
    value = str(role or "").lower()
    if "student" in value or "学生" in value:
        return "student"
    if "guardian" in value or "parent" in value or "家长" in value or "監護" in value or "监护" in value:
        return "parent"
    if "teacher" in value or "老师" in value or "教师" in value:
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
