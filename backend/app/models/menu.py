from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo
from app import db

MEAL_SLOT_KEYS = ("breakfast", "lunch", "dinner", "late_night")
DEFAULT_MEAL_SLOT_WINDOWS = (
    ("breakfast", "05:00", "09:30"),
    ("lunch", "10:30", "13:30"),
    ("dinner", "17:00", "19:30"),
    ("late_night", "21:00", "23:59"),
)


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


def empty_meal_dish_ids() -> dict[str, list[int]]:
    return {key: [] for key in MEAL_SLOT_KEYS}


def normalize_meal_dish_ids(value) -> dict[str, list[int]]:
    normalized = empty_meal_dish_ids()

    if isinstance(value, dict):
        for key in MEAL_SLOT_KEYS:
            normalized[key] = _normalize_dish_ids(value.get(key))
    return normalized


def aggregate_meal_dish_ids(meal_dish_ids) -> list[int]:
    normalized = normalize_meal_dish_ids(meal_dish_ids)
    aggregated: list[int] = []
    seen: set[int] = set()
    for key in MEAL_SLOT_KEYS:
        for dish_id in normalized[key]:
            if dish_id in seen:
                continue
            seen.add(dish_id)
            aggregated.append(dish_id)
    return aggregated


def resolve_meal_slot_for_datetime(captured_at, timezone_name: str = "Asia/Shanghai") -> str | None:
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

    for slot_key, start_str, end_str in DEFAULT_MEAL_SLOT_WINDOWS:
        start = time.fromisoformat(start_str)
        end = time.fromisoformat(end_str)
        if start <= local_time <= end:
            return slot_key
    return None


class DailyMenu(db.Model):
    __tablename__ = "daily_menus"

    id = db.Column(db.Integer, primary_key=True)
    menu_date = db.Column(db.Date, unique=True, nullable=False, index=True)
    meal_dish_ids = db.Column(db.JSON, default=empty_meal_dish_ids)
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

    def normalized_meal_dish_ids(self) -> dict[str, list[int]]:
        return normalize_meal_dish_ids(self.meal_dish_ids)

    def aggregated_dish_ids(self) -> list[int]:
        normalized_slots = self.normalized_meal_dish_ids()
        return aggregate_meal_dish_ids(normalized_slots)

    def dish_ids_for_meal(self, meal_slot: str | None) -> list[int]:
        if not meal_slot:
            return self.aggregated_dish_ids()

        normalized_slots = self.normalized_meal_dish_ids()
        slot_ids = normalized_slots.get(meal_slot) or []
        if slot_ids:
            return list(slot_ids)
        return self.aggregated_dish_ids()

    def to_dict(self):
        meal_dish_ids = self.normalized_meal_dish_ids()
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
