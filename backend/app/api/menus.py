import logging
from datetime import date, timedelta
from flask import Blueprint, request
from app import db
from app.models import DailyMenu, Dish
from app.models.menu import MEAL_SLOT_KEYS, empty_meal_dish_ids, normalize_meal_dish_ids
from app.utils.jwt_utils import login_required, role_required, api_ok, api_error

bp = Blueprint("menus", __name__)
logger = logging.getLogger(__name__)

ALLOWED_ROLES_WRITE = ("admin", "canteen_manager")
MAX_FUTURE_DAYS = 30
MAX_PAST_DAYS = 7


def _ordered_active_dishes_by_ids(dish_ids: list[int]) -> list[Dish]:
    if not dish_ids:
        return []

    dishes = Dish.query.filter(
        Dish.id.in_(dish_ids),
        Dish.is_active.is_(True),
    ).all()
    dish_by_id = {dish.id: dish for dish in dishes}
    return [dish_by_id[dish_id] for dish_id in dish_ids if dish_id in dish_by_id]


def _default_menu_payload(menu_date: str) -> dict:
    all_dishes = Dish.query.filter_by(is_active=True).all()
    return {
        "menu_date": menu_date,
        "meal_dish_ids": empty_meal_dish_ids(),
        "dishes": [dish.to_dict() for dish in all_dishes],
        "is_default": True,
    }


def _parse_menu_payload(data: dict) -> dict[str, list[int]]:
    raw_meal_dish_ids = data.get("meal_dish_ids")
    if not isinstance(raw_meal_dish_ids, dict):
        raise ValueError("meal_dish_ids 必须是对象")
    for key in MEAL_SLOT_KEYS:
        value = raw_meal_dish_ids.get(key)
        if value is not None and not isinstance(value, (list, tuple, set)):
            raise ValueError(f"{key} 菜品列表格式无效")
    return normalize_meal_dish_ids(raw_meal_dish_ids)


@bp.route("/<string:menu_date>", methods=["GET"])
@login_required
def get_menu(menu_date):
    try:
        d = date.fromisoformat(menu_date)
    except ValueError:
        return api_error("日期格式无效，请使用 YYYY-MM-DD")

    menu = DailyMenu.query.filter_by(menu_date=d).first()
    if not menu:
        return api_ok(_default_menu_payload(menu_date))

    dishes = _ordered_active_dishes_by_ids(menu.aggregated_dish_ids())
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
    try:
        meal_dish_ids = _parse_menu_payload(data)
    except (TypeError, ValueError):
        return api_error("meal_dish_ids 格式无效")
    aggregated_dish_ids: list[int] = []
    for key in MEAL_SLOT_KEYS:
        aggregated_dish_ids.extend(meal_dish_ids.get(key) or [])
    dish_ids = list(dict.fromkeys(aggregated_dish_ids))

    # Validate dish ids
    if dish_ids:
        valid = Dish.query.filter(
            Dish.id.in_(dish_ids), Dish.is_active.is_(True)
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

    menu.meal_dish_ids = {
        key: list(meal_dish_ids.get(key) or [])
        for key in MEAL_SLOT_KEYS
    }
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
