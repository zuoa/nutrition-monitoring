from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from flask import current_app

from app import db

MENU_NOT_CONFIGURED_ALERT_TYPE = "menu_not_configured"
RECOGNITION_MENU_SCOPE_MEAL = "meal"
RECOGNITION_MENU_SCOPE_DAY = "day"
RECOGNITION_MENU_SCOPE_ALL = "all"
RECOGNITION_MENU_SCOPES = (
    RECOGNITION_MENU_SCOPE_MEAL,
    RECOGNITION_MENU_SCOPE_DAY,
    RECOGNITION_MENU_SCOPE_ALL,
)

# Unified meal slot defaults. Replaces the previous hardcoded MEAL_SLOT_KEYS and
# DEFAULT_MEAL_SLOT_WINDOWS. Config is loaded from the MEAL_SLOTS runtime config.
DEFAULT_MEAL_SLOTS = [
    {"key": "breakfast", "label": "早餐", "start": "05:00", "end": "09:30"},
    {"key": "lunch", "label": "午餐", "start": "10:30", "end": "13:30"},
    {"key": "dinner", "label": "晚餐", "start": "17:00", "end": "19:30"},
    {"key": "late_night", "label": "宵夜", "start": "21:00", "end": "23:59"},
]


def _current_config() -> dict | None:
    """Return current Flask app config when available, otherwise None."""
    try:
        return current_app.config
    except RuntimeError:
        return None


def get_meal_slots(config: dict | None = None) -> list[dict]:
    """Return configured meal slots, falling back to defaults."""
    cfg = config or _current_config()
    if cfg is None:
        return [dict(slot) for slot in DEFAULT_MEAL_SLOTS]
    raw = cfg.get("MEAL_SLOTS")
    if not isinstance(raw, list) or not raw:
        return [dict(slot) for slot in DEFAULT_MEAL_SLOTS]
    normalized: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        label = str(item.get("label") or "").strip()
        start = str(item.get("start") or "").strip()
        end = str(item.get("end") or "").strip()
        if not key or not label or not start or not end:
            continue
        normalized.append({"key": key, "label": label, "start": start, "end": end})
    return normalized or [dict(slot) for slot in DEFAULT_MEAL_SLOTS]


def get_meal_slot_keys(config: dict | None = None) -> tuple[str, ...]:
    return tuple(slot["key"] for slot in get_meal_slots(config))


def get_meal_slot_map(config: dict | None = None) -> dict[str, dict]:
    return {slot["key"]: slot for slot in get_meal_slots(config)}


def _normalize_dish_ids(value) -> list[int]:
    if not isinstance(value, (list, tuple, set)):
        return []

    result: list[int] = []
    seen: set[int] = set()
    for item in value:
        try:
            dish_id = int(item)
        except (TypeError, ValueError):
            continue
        if dish_id in seen:
            continue
        seen.add(dish_id)
        result.append(dish_id)
    return result


def empty_meal_dish_ids(config: dict | None = None) -> dict[str, list[int]]:
    return {key: [] for key in get_meal_slot_keys(config)}


def normalize_meal_dish_ids(value, config: dict | None = None) -> dict[str, list[int]]:
    normalized = empty_meal_dish_ids(config)

    if isinstance(value, dict):
        for key in get_meal_slot_keys(config):
            normalized[key] = _normalize_dish_ids(value.get(key))
    return normalized


def aggregate_meal_dish_ids(meal_dish_ids, config: dict | None = None) -> list[int]:
    normalized = normalize_meal_dish_ids(meal_dish_ids, config)
    aggregated: list[int] = []
    seen: set[int] = set()
    for key in get_meal_slot_keys(config):
        for dish_id in normalized[key]:
            if dish_id in seen:
                continue
            seen.add(dish_id)
            aggregated.append(dish_id)
    return aggregated


def is_menu_configured(menu, config: dict | None = None) -> bool:
    return bool(menu and not menu.is_default and menu.aggregated_dish_ids(config))


def menu_not_configured_message(menu_date) -> str:
    return f"{menu_date} 未配置菜单，已停止视频分析，请先配置当天菜单后重试"


def resolve_meal_slot_for_datetime(
    captured_at,
    timezone_name: str = "Asia/Shanghai",
    config: dict | None = None,
) -> str | None:
    if captured_at is None:
        return None

    timezone_label = str(timezone_name or "Asia/Shanghai")
    try:
        tz = ZoneInfo(timezone_label)
    except Exception:
        tz = ZoneInfo("Asia/Shanghai")

    if captured_at.tzinfo is None:
        local_dt = captured_at.replace(tzinfo=tz)
    else:
        local_dt = captured_at.astimezone(tz)
    local_time = local_dt.time()

    for slot in get_meal_slots(config):
        try:
            start = time.fromisoformat(slot["start"])
            end = time.fromisoformat(slot["end"])
        except ValueError:
            continue
        if start <= local_time <= end:
            return slot["key"]
    return None


def normalize_recognition_menu_scope(value) -> str:
    normalized = str(value or RECOGNITION_MENU_SCOPE_ALL).strip().lower()
    if normalized not in RECOGNITION_MENU_SCOPES:
        return RECOGNITION_MENU_SCOPE_ALL
    return normalized


class DailyMenu(db.Model):
    __tablename__ = "daily_menus"

    id = db.Column(db.Integer, primary_key=True)
    menu_date = db.Column(db.Date, unique=True, nullable=False, index=True)
    meal_dish_ids = db.Column(db.JSON, default=lambda: empty_meal_dish_ids())
    is_default = db.Column(db.Boolean, default=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def normalized_meal_dish_ids(self, config: dict | None = None) -> dict[str, list[int]]:
        return normalize_meal_dish_ids(self.meal_dish_ids, config)

    def aggregated_dish_ids(self, config: dict | None = None) -> list[int]:
        normalized_slots = self.normalized_meal_dish_ids(config)
        return aggregate_meal_dish_ids(normalized_slots, config)

    def dish_ids_for_meal(self, meal_slot: str | None, config: dict | None = None) -> list[int]:
        if not meal_slot:
            return self.aggregated_dish_ids(config)

        normalized_slots = self.normalized_meal_dish_ids(config)
        slot_ids = normalized_slots.get(meal_slot) or []
        if slot_ids:
            return list(slot_ids)
        return self.aggregated_dish_ids(config)

    def dish_ids_for_recognition(
        self,
        meal_slot: str | None,
        menu_scope: str | None = None,
        config: dict | None = None,
    ) -> list[int]:
        if normalize_recognition_menu_scope(menu_scope) == RECOGNITION_MENU_SCOPE_DAY:
            return self.aggregated_dish_ids(config)
        return self.dish_ids_for_meal(meal_slot, config)

    def to_dict(self, config: dict | None = None):
        meal_dish_ids = self.normalized_meal_dish_ids(config)
        return {
            "id": self.id,
            "menu_date": self.menu_date.isoformat() if self.menu_date else None,
            "meal_dish_ids": meal_dish_ids,
            "is_default": self.is_default,
            "created_by": self.created_by,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f"<DailyMenu {self.menu_date}>"
