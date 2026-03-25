import jwt
from datetime import datetime, timezone
from flask import current_app, request
from functools import wraps


def generate_token(user_id: int, role: str) -> str:
    config = current_app.config
    expires = datetime.now(timezone.utc) + config["JWT_ACCESS_TOKEN_EXPIRES"]
    payload = {
        "sub": str(user_id),
        "role": role,
        "exp": expires,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, config["SECRET_KEY"], algorithm=config["JWT_ALGORITHM"])


def decode_token(token: str) -> dict:
    config = current_app.config
    return jwt.decode(
        token,
        config["SECRET_KEY"],
        algorithms=[config["JWT_ALGORITHM"]],
    )


def get_current_user():
    from app.models import User
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[7:]
    try:
        payload = decode_token(token)
        user_id = int(payload["sub"])
        return User.query.get(user_id)
    except Exception:
        return None


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user or not user.is_active:
            return api_error("未授权，请重新登录", 401)
        request.current_user = user
        return f(*args, **kwargs)
    return decorated


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            user = get_current_user()
            if not user or not user.is_active:
                return api_error("未授权，请重新登录", 401)
            if user.role.value not in roles:
                return api_error("权限不足", 403)
            request.current_user = user
            return f(*args, **kwargs)
        return decorated
    return decorator


def api_error(message: str, status_code: int = 400):
    from flask import jsonify
    return jsonify({"code": status_code, "message": message, "data": None}), status_code


def api_ok(data=None, message: str = "ok"):
    from flask import jsonify
    return jsonify({"code": 0, "data": data, "message": message})
