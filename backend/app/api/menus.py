import logging
from datetime import date, timedelta
from flask import Blueprint, request
from app import db
from app.models import DailyMenu, Dish
from app.utils.jwt_utils import login_required, role_required, api_ok, api_error

bp = Blueprint("menus", __name__)
logger = logging.getLogger(__name__)

ALLOWED_ROLES_WRITE = ("admin", "canteen_manager")
MAX_FUTURE_DAYS = 30
MAX_PAST_DAYS = 7


@bp.route("/<string:menu_date>", methods=["GET"])
@login_required
def get_menu(menu_date):
    try:
        d = date.fromisoformat(menu_date)
    except ValueError:
        return api_error("日期格式无效，请使用 YYYY-MM-DD")

    menu = DailyMenu.query.filter_by(menu_date=d).first()
    if not menu:
        # Return default (all active dishes)
        all_dishes = Dish.query.filter_by(is_active=True).all()
        return api_ok({
            "menu_date": menu_date,
            "dish_ids": [d.id for d in all_dishes],
            "dishes": [d.to_dict() for d in all_dishes],
            "is_default": True,
        })

    dishes = Dish.query.filter(Dish.id.in_(menu.dish_ids or [])).all()
    data = menu.to_dict()
    data["dishes"] = [d.to_dict() for d in dishes]
    return api_ok(data)


@bp.route("/<string:menu_date>", methods=["POST", "PUT"])
@role_required(*ALLOWED_ROLES_WRITE)
def upsert_menu(menu_date):
    try:
        d = date.fromisoformat(menu_date)
    except ValueError:
        return api_error("日期格式无效，请使用 YYYY-MM-DD")

    today = date.today()
    if d > today + timedelta(days=MAX_FUTURE_DAYS):
        return api_error(f"最多可提前 {MAX_FUTURE_DAYS} 天配置菜单")
    if d < today - timedelta(days=MAX_PAST_DAYS):
        return api_error(f"不允许修改 {MAX_PAST_DAYS} 天前的历史菜单")

    data = request.get_json() or {}
    dish_ids = data.get("dish_ids", [])

    # Validate dish ids
    if dish_ids:
        valid = Dish.query.filter(
            Dish.id.in_(dish_ids), Dish.is_active
        ).count()
        if valid != len(set(dish_ids)):
            return api_error("包含无效或已停用的菜品 ID")

    menu = DailyMenu.query.filter_by(menu_date=d).first()
    user = request.current_user

    # Warn if analysis already started for today
    if menu and d == today:
        from app.models import TaskLog
        running = TaskLog.query.filter_by(
            task_type="ai_recognition", task_date=d, status="running"
        ).first()
        if running:
            logger.warning(f"Menu updated while analysis running for {d}")

    if not menu:
        menu = DailyMenu(
            menu_date=d,
            created_by=user.id,
        )
        db.session.add(menu)

    menu.dish_ids = dish_ids
    menu.is_default = len(dish_ids) == 0
    db.session.commit()
    return api_ok(menu.to_dict())


@bp.route("/", methods=["GET"])
@login_required
def list_menus():
    """List menus for a date range."""
    start_str = request.args.get("start")
    end_str = request.args.get("end")
    try:
        start = date.fromisoformat(start_str) if start_str else date.today() - timedelta(days=7)
        end = date.fromisoformat(end_str) if end_str else date.today() + timedelta(days=7)
    except ValueError:
        return api_error("日期格式无效")

    menus = DailyMenu.query.filter(
        DailyMenu.menu_date >= start,
        DailyMenu.menu_date <= end,
    ).order_by(DailyMenu.menu_date).all()

    return api_ok([m.to_dict() for m in menus])
