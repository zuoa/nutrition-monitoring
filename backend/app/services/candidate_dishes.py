from app.models import DailyMenu, Dish, DishMealSlot
from app.models.menu import (
    RECOGNITION_MENU_SCOPE_ALL,
    RECOGNITION_MENU_SCOPE_DAY,
    get_meal_slot_keys,
    get_meal_slot_map,
    menu_not_configured_message,
    normalize_recognition_menu_scope,
    resolve_meal_slot_for_datetime,
)


FIXED_MEAL_POOL_EMPTY = "fixed_meal_pool_empty"
MEAL_MENU_NOT_CONFIGURED = "meal_menu_not_configured"


class CandidateDishResolutionError(ValueError):
    def __init__(self, message: str, *, code: str, meal_slot: str | None = None):
        super().__init__(message)
        self.code = code
        self.meal_slot = meal_slot


def normalize_fixed_candidate_meal_slots(value, config: dict) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    allowed = set(get_meal_slot_keys(config))
    result: list[str] = []
    for item in value:
        meal_slot = str(item or "").strip()
        if meal_slot in allowed and meal_slot not in result:
            result.append(meal_slot)
    return result


def fixed_candidate_meal_slots(config: dict) -> list[str]:
    return normalize_fixed_candidate_meal_slots(
        config.get("FIXED_CANDIDATE_MEAL_SLOTS"),
        config,
    )


def requires_date_menu_precheck(config: dict) -> bool:
    """Keep legacy date-level gating unless at least one fixed pool is enabled.

    Hybrid mode must defer validation until each image's meal slot is known so a
    missing lunch menu cannot prevent breakfast images from being processed.
    """
    menu_scope = normalize_recognition_menu_scope(config.get("RECOGNITION_MENU_SCOPE", "all"))
    return menu_scope != RECOGNITION_MENU_SCOPE_ALL and not fixed_candidate_meal_slots(config)


def _ordered_active_dishes(dish_ids: list[int]) -> list[Dish]:
    if not dish_ids:
        return []
    dishes = Dish.query.filter(
        Dish.id.in_(dish_ids),
        Dish.is_active.is_(True),
    ).all()
    dish_by_id = {dish.id: dish for dish in dishes}
    return [dish_by_id[dish_id] for dish_id in dish_ids if dish_id in dish_by_id]


def _fixed_pool_dishes(meal_slot: str) -> list[Dish]:
    return (
        Dish.query.join(DishMealSlot)
        .filter(
            Dish.is_active.is_(True),
            DishMealSlot.meal_slot == meal_slot,
        )
        .order_by(Dish.category.asc(), Dish.name.asc())
        .all()
    )


def resolve_candidate_dishes(captured_image, config: dict) -> list[Dish]:
    meal_slot = resolve_meal_slot_for_datetime(
        captured_image.captured_at,
        timezone_name=config.get("VIDEO_TIMEZONE") or config.get("APP_TIMEZONE", "Asia/Shanghai"),
        config=config,
    )

    if meal_slot and meal_slot in fixed_candidate_meal_slots(config):
        dishes = _fixed_pool_dishes(meal_slot)
        if not dishes:
            meal_label = get_meal_slot_map(config).get(meal_slot, {}).get("label") or meal_slot
            raise CandidateDishResolutionError(
                f"{captured_image.capture_date} {meal_label}固定菜品池为空，请先为菜品添加对应餐次标签",
                code=FIXED_MEAL_POOL_EMPTY,
                meal_slot=meal_slot,
            )
        return dishes

    menu_scope = normalize_recognition_menu_scope(config.get("RECOGNITION_MENU_SCOPE", "all"))
    if menu_scope == RECOGNITION_MENU_SCOPE_ALL:
        return Dish.query.filter(Dish.is_active.is_(True)).all()

    menu = DailyMenu.query.filter_by(menu_date=captured_image.capture_date).first()
    if not menu or menu.is_default:
        raise CandidateDishResolutionError(
            menu_not_configured_message(captured_image.capture_date),
            code=MEAL_MENU_NOT_CONFIGURED,
            meal_slot=meal_slot,
        )

    if menu_scope == RECOGNITION_MENU_SCOPE_DAY or not meal_slot:
        dish_ids = menu.aggregated_dish_ids(config)
    else:
        dish_ids = menu.normalized_meal_dish_ids(config).get(meal_slot) or []

    dishes = _ordered_active_dishes(dish_ids)
    if not dishes:
        meal_label = get_meal_slot_map(config).get(meal_slot, {}).get("label") if meal_slot else None
        meal_label = meal_label or meal_slot or "当前餐次"
        raise CandidateDishResolutionError(
            f"{captured_image.capture_date} {meal_label} 未配置菜单或菜单菜品均已停用，已停止该餐识别",
            code=MEAL_MENU_NOT_CONFIGURED,
            meal_slot=meal_slot,
        )
    return dishes
