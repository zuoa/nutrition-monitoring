import logging
import uuid
import redis
from flask import Blueprint, request, current_app
from app import db
from app.models import User, RoleEnum
from app.utils.jwt_utils import generate_token, api_ok, api_error, login_required
from app.utils.captcha import generate_captcha
from app.services.dingtalk import DingTalkService

bp = Blueprint("auth", __name__)
logger = logging.getLogger(__name__)


def get_redis_client():
    """Get Redis client from app config."""
    return redis.from_url(current_app.config["REDIS_URL"], decode_responses=True)


@bp.route("/captcha", methods=["GET"])
def get_captcha():
    """Generate and return a CAPTCHA."""
    try:
        code, image_base64 = generate_captcha()
        captcha_id = str(uuid.uuid4())
        # Store in Redis with 5 minute expiry
        r = get_redis_client()
        r.setex(f"captcha:{captcha_id}", 300, code)
        return api_ok({
            "captcha_id": captcha_id,
            "captcha_image": image_base64,
        })
    except Exception as e:
        logger.error(f"Captcha generation failed: {e}")
        return api_error("验证码生成失败，请稍后重试", 500)


@bp.route("/login", methods=["POST"])
def login():
    """Login with username and password."""
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    captcha_id = data.get("captcha_id", "")
    captcha_code = data.get("captcha_code", "").upper()

    if not username or not password:
        return api_error("请输入账号和密码")

    # Verify CAPTCHA
    if not captcha_id or not captcha_code:
        return api_error("请输入验证码")

    r = get_redis_client()
    stored_code = r.get(f"captcha:{captcha_id}")
    if not stored_code:
        return api_error("验证码已过期，请刷新重试")

    if stored_code != captcha_code:
        return api_error("验证码错误")

    # Delete used captcha
    r.delete(f"captcha:{captcha_id}")

    # Find user by username
    user = User.query.filter_by(username=username).first()
    if not user:
        return api_error("账号或密码错误")

    if not user.check_password(password):
        return api_error("账号或密码错误")

    if not user.is_active:
        return api_error("您的账号已被禁用，请联系管理员", 403)

    token = generate_token(user.id, user.role.value)
    return api_ok({
        "token": token,
        "user": user.to_dict(),
    })


@bp.route("/dingtalk-login", methods=["POST"])
def dingtalk_login():
    data = request.get_json() or {}
    auth_code = data.get("authCode") or data.get("auth_code")
    if not auth_code:
        return api_error("缺少 authCode 参数")

    dt = DingTalkService(current_app.config)
    try:
        user_info = dt.get_user_info_by_code(auth_code)
    except Exception as e:
        logger.warning(f"DingTalk login failed: {e}")
        return api_error("钉钉登录失败，请稍后重试", 503)

    dingtalk_user_id = _primary_dingtalk_identifier(user_info)
    if not dingtalk_user_id:
        return api_error("无法获取钉钉用户信息")

    user = _resolve_dingtalk_login_user(user_info)
    if not user:
        # First time: try to sync or create basic user
        user = User(
            dingtalk_user_id=dingtalk_user_id,
            name=user_info.get("name") or "Unknown",
            role=RoleEnum.teacher,  # default, admin should update
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()

    if not user.is_active:
        return api_error("您的账号已被禁用，请联系管理员", 403)

    token = generate_token(user.id, user.role.value)
    return api_ok({
        "token": token,
        "user": user.to_dict(),
    })


def _resolve_dingtalk_login_user(user_info: dict) -> User | None:
    identifiers = _dingtalk_identifiers(user_info)
    if not identifiers:
        return None

    for identifier in identifiers:
        user = User.query.filter_by(dingtalk_user_id=identifier, is_active=True).first()
        if user:
            return user

    inactive_user = None
    for identifier in identifiers:
        inactive_user = User.query.filter_by(dingtalk_user_id=identifier).first()
        if inactive_user:
            break
    if not inactive_user:
        return None

    active_synced_user = _find_unique_active_synced_user_by_name(str(user_info.get("name") or "").strip(), inactive_user.id)
    if _is_login_placeholder(inactive_user) and active_synced_user:
        return active_synced_user

    return inactive_user


def _primary_dingtalk_identifier(user_info: dict) -> str:
    identifiers = _dingtalk_identifiers(user_info)
    return identifiers[0] if identifiers else ""


def _dingtalk_identifiers(user_info: dict) -> list[str]:
    result: list[str] = []
    for field in ("userid", "userId", "unionid", "unionId", "openId", "openid"):
        value = str(user_info.get(field) or "").strip()
        if value and value not in result:
            result.append(value)
    return result


def _is_login_placeholder(user: User) -> bool:
    return (
        user.username is None
        and not user.dept_id
        and not user.dept_name
        and user.sync_at is None
    )


def _find_unique_active_synced_user_by_name(name: str, exclude_user_id: int) -> User | None:
    if not name:
        return None

    users = User.query.filter(
        User.id != exclude_user_id,
        User.name == name,
        User.is_active.is_(True),
    ).all()
    synced_users = [
        user
        for user in users
        if user.sync_at or user.dept_id or user.dept_name
    ]
    if len(synced_users) == 1:
        return synced_users[0]
    return None


@bp.route("/dingtalk-config", methods=["GET"])
def dingtalk_config():
    app_key = str(current_app.config.get("DINGTALK_APP_KEY", "") or "").strip()
    return api_ok({
        "enabled": bool(app_key),
        "client_id": app_key,
    })


@bp.route("/refresh", methods=["POST"])
@login_required
def refresh_token():
    user = request.current_user
    token = generate_token(user.id, user.role.value)
    return api_ok({"token": token})


@bp.route("/me", methods=["GET"])
@login_required
def get_me():
    return api_ok(request.current_user.to_dict())


@bp.route("/dingtalk-callback", methods=["POST"])
def dingtalk_callback():
    """Handle DingTalk event callbacks (user changes, auth revocation)."""
    # In production: verify callback signature
    data = request.get_json() or {}
    event_type = data.get("EventType")
    logger.info(f"DingTalk callback event: {event_type}")

    if event_type == "user_leave_org":
        user_id = data.get("UserId")
        if user_id:
            user = User.query.filter_by(dingtalk_user_id=user_id).first()
            if user:
                user.is_active = False
                db.session.commit()

    return {"errcode": 0, "errmsg": "ok"}
