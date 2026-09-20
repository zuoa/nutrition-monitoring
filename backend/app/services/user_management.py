"""Validate administrator-managed account fields before changing a user."""

from app.models import Department, RoleEnum, User


def validate_user_changes(data, user=None):
    if not isinstance(data, dict):
        raise ValueError("请求体必须为对象")
    values = {}
    for key, limit in (("name", 64), ("username", 64)):
        if key in data or user is None:
            value = data.get(key)
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
                raise ValueError(f"{'姓名' if key == 'name' else '登录名'}不能为空，最多 {limit} 个字符")
            values[key] = value.strip()
    if "username" in values:
        if any(char.isspace() for char in values["username"]):
            raise ValueError("登录名不能包含空白字符")
        existing = User.query.filter_by(username=values["username"]).first()
        if existing and (user is None or existing.id != user.id):
            raise ValueError("登录名已存在（包括停用用户），请使用其他登录名")
    if "role" in data or user is None:
        try:
            values["role"] = RoleEnum(data.get("role"))
        except (ValueError, TypeError):
            raise ValueError("无效角色") from None
    if "is_active" in data:
        if type(data["is_active"]) is not bool:
            raise ValueError("用户状态必须为布尔值")
        values["is_active"] = data["is_active"]
    if "dept_id" in data:
        dept_id = data["dept_id"]
        if dept_id in (None, ""):
            values.update(dept_id=None, dept_name=None)
        elif isinstance(dept_id, str):
            department = Department.query.filter_by(dingtalk_dept_id=dept_id, is_active=True).first()
            if not department:
                raise ValueError("所选部门不存在或已停用")
            values.update(dept_id=department.dingtalk_dept_id, dept_name=department.name)
        else:
            raise ValueError("部门格式无效")
    for field in ("managed_class_ids", "managed_grade_ids", "student_ids"):
        if field in data:
            ids = data[field]
            if not isinstance(ids, list) or any(type(value) not in (str, int) or not str(value).strip() for value in ids):
                raise ValueError(f"{field} 必须为有效 ID 数组")
            values[field] = ids
    password = data.get("password")
    if password is not None or user is None or ("username" in values and not user.password_hash):
        if not isinstance(password, str) or not 8 <= len(password) <= 128 or not password.strip():
            raise ValueError("密码长度应为 8–128 个字符")
    return values, password
