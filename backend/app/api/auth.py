import logging
from flask import Blueprint, request, current_app
from app import db
from app.models import User, RoleEnum
from app.utils.jwt_utils import generate_token, decode_token, api_ok, api_error, login_required
from app.services.dingtalk import DingTalkService

bp = Blueprint("auth", __name__)
logger = logging.getLogger(__name__)


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

    dingtalk_user_id = user_info.get("userid") or user_info.get("unionid")
    if not dingtalk_user_id:
        return api_error("无法获取钉钉用户信息")

    user = User.query.filter_by(dingtalk_user_id=dingtalk_user_id).first()
    if not user:
        # First time: try to sync or create basic user
        user = User(
            dingtalk_user_id=dingtalk_user_id,
            name=user_info.get("name", "Unknown"),
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
