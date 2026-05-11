import logging
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from celery_app import celery
from app import db
from app.models import DailyMenu, Dish, DishSampleImage, TaskLog, User, RoleEnum
from app.models.menu import MEAL_SLOT_KEYS, normalize_meal_dish_ids
from app.services.runtime_config import get_effective_config

logger = logging.getLogger(__name__)

TASK_TYPE = "menu_sample_reminder"
ALERT_TYPE = "menu_or_sample_missing"
DEFAULT_MEAL_LABELS = {
    "breakfast": "早餐",
    "lunch": "午餐",
    "dinner": "晚餐",
    "late_night": "宵夜",
}
DEFAULT_MEAL_TIMES = {
    "breakfast": "05:00",
    "lunch": "10:30",
    "dinner": "17:00",
    "late_night": "21:00",
}


@celery.task(name="app.tasks.menu_reminders.check_menu_sample_reminders")
def check_menu_sample_reminders(now_iso: str | None = None):
    """Push DingTalk reminders when a meal menu or its sample images are missing."""
    from flask import current_app

    cfg = get_effective_config(current_app.config)
    if not _config_bool(cfg.get("MENU_REMINDER_ENABLED", True)):
        return {"checked": False, "reason": "disabled"}

    now = _resolve_now(cfg, now_iso)
    due_slots = _resolve_due_meal_slots(cfg, now)
    if not due_slots:
        return {"checked": True, "sent": 0, "due_slots": []}

    sent = 0
    results = []
    for meal_slot in due_slots:
        result = _check_and_send_meal_reminder(cfg, now.date(), meal_slot, now=now)
        results.append(result)
        if result.get("sent"):
            sent += 1

    return {
        "checked": True,
        "sent": sent,
        "due_slots": due_slots,
        "results": results,
    }


def _check_and_send_meal_reminder(cfg: dict, target_date: date, meal_slot: str, *, now: datetime | None = None) -> dict:
    if _has_sent_reminder(target_date, meal_slot):
        return {"sent": False, "reason": "already_sent", "meal_slot": meal_slot}

    issues = _build_menu_sample_issues(target_date, meal_slot)
    if not issues["missing_menu"] and not issues["missing_sample_dishes"]:
        return {"sent": False, "reason": "ok", "meal_slot": meal_slot}

    recipients = _resolve_responsible_users(cfg)
    if not recipients:
        task_log = _record_reminder_task(
            target_date,
            meal_slot,
            "failed",
            "菜单/样图提醒未配置可用责任人",
            issues,
            recipients=[],
            now=now,
        )
        return {
            "sent": False,
            "reason": "no_recipients",
            "meal_slot": meal_slot,
            "task_id": task_log.id,
        }

    message = _build_reminder_message(cfg, target_date, meal_slot, issues)
    try:
        _send_dingtalk_message(cfg, recipients, message)
    except Exception as e:
        logger.error("Failed to send menu sample reminder: %s", e, exc_info=True)
        task_log = _record_reminder_task(
            target_date,
            meal_slot,
            "failed",
            str(e)[:500],
            issues,
            recipients=recipients,
            now=now,
        )
        return {
            "sent": False,
            "reason": "send_failed",
            "meal_slot": meal_slot,
            "task_id": task_log.id,
        }

    task_log = _record_reminder_task(
        target_date,
        meal_slot,
        "success",
        message,
        issues,
        recipients=recipients,
        now=now,
    )
    logger.info("Menu sample reminder sent for %s %s to %s users", target_date, meal_slot, len(recipients))
    return {
        "sent": True,
        "meal_slot": meal_slot,
        "task_id": task_log.id,
        "recipient_count": len(recipients),
    }


def _resolve_due_meal_slots(cfg: dict, now: datetime) -> list[str]:
    before_minutes = _config_int(cfg.get("MENU_REMINDER_BEFORE_MINUTES", 30), 30)
    meal_times = _normalize_meal_times(cfg.get("MENU_REMINDER_MEAL_TIMES"))
    due_slots: list[str] = []
    current_minute = now.replace(second=0, microsecond=0)

    for meal_slot in MEAL_SLOT_KEYS:
        time_text = meal_times.get(meal_slot)
        if not time_text:
            continue
        try:
            hour_str, minute_str = time_text.split(":", 1)
            meal_dt = current_minute.replace(hour=int(hour_str), minute=int(minute_str))
        except (AttributeError, ValueError):
            logger.warning("Invalid menu reminder meal time for %s: %r", meal_slot, time_text)
            continue

        reminder_dt = meal_dt - timedelta(minutes=max(0, before_minutes))
        if current_minute == reminder_dt:
            due_slots.append(meal_slot)

    return due_slots


def _build_menu_sample_issues(target_date: date, meal_slot: str) -> dict:
    menu = DailyMenu.query.filter_by(menu_date=target_date).first()
    normalized = normalize_meal_dish_ids(menu.meal_dish_ids if menu else None)
    dish_ids = normalized.get(meal_slot) or []
    missing_menu = bool(not menu or menu.is_default or not dish_ids)
    missing_sample_dishes: list[dict] = []

    if dish_ids:
        dishes = Dish.query.filter(
            Dish.id.in_(dish_ids),
            Dish.is_active.is_(True),
        ).order_by(Dish.name.asc()).all()
        active_sample_counts = dict(
            db.session.query(
                DishSampleImage.dish_id,
                db.func.count(DishSampleImage.id),
            ).filter(
                DishSampleImage.dish_id.in_([dish.id for dish in dishes]),
                DishSampleImage.is_active.is_(True),
            ).group_by(DishSampleImage.dish_id).all()
        )
        for dish in dishes:
            if int(active_sample_counts.get(dish.id) or 0) == 0:
                missing_sample_dishes.append({"id": dish.id, "name": dish.name})

    return {
        "missing_menu": missing_menu,
        "dish_ids": dish_ids,
        "missing_sample_dishes": missing_sample_dishes,
    }


def _resolve_responsible_users(cfg: dict) -> list[User]:
    user_ids = _normalize_user_ids(cfg.get("MENU_REMINDER_RESPONSIBLE_USER_IDS"))
    query = User.query.filter(
        User.is_active.is_(True),
        User.dingtalk_user_id.isnot(None),
        User.dingtalk_user_id != "",
    )
    if user_ids:
        users = query.filter(User.id.in_(user_ids)).all()
        users_by_id = {user.id: user for user in users}
        return [users_by_id[user_id] for user_id in user_ids if user_id in users_by_id]

    return query.filter(User.role == RoleEnum.canteen_manager).order_by(User.name.asc()).all()


def _send_dingtalk_message(cfg: dict, recipients: list[User], message: str) -> None:
    from app.services.dingtalk import DingTalkService

    user_ids = [user.dingtalk_user_id for user in recipients if user.dingtalk_user_id]
    if not user_ids:
        raise ValueError("没有可用的钉钉 user_id")
    dt = DingTalkService(cfg)
    msg = {"msgtype": "text", "text": {"content": message}}
    for offset in range(0, len(user_ids), 100):
        result = dt.send_work_notification(user_ids[offset:offset + 100], msg)
        if result.get("errcode") != 0:
            raise RuntimeError(f"钉钉消息发送失败: {result}")


def _build_reminder_message(cfg: dict, target_date: date, meal_slot: str, issues: dict) -> str:
    meal_label = DEFAULT_MEAL_LABELS.get(meal_slot, meal_slot)
    lines = [
        f"[营养监测系统提醒] {target_date.isoformat()} {meal_label}即将开始，请补齐菜单和菜品样图。",
    ]
    if issues.get("missing_menu"):
        lines.append(f"- {meal_label}菜单未设置或未选择菜品")
    missing_sample_dishes = issues.get("missing_sample_dishes") or []
    if missing_sample_dishes:
        names = "、".join(item["name"] for item in missing_sample_dishes[:20])
        suffix = f" 等 {len(missing_sample_dishes)} 个菜品" if len(missing_sample_dishes) > 20 else ""
        lines.append(f"- 缺少菜品样图：{names}{suffix}")
    lines.append("请在菜单管理和样图采集页面处理。")
    system_entry_url = _build_system_entry_url(cfg)
    if system_entry_url:
        lines.append(f"系统入口：{system_entry_url}")
    return "\n".join(lines)


def _build_system_entry_url(cfg: dict) -> str:
    frontend_url = str(cfg.get("FRONTEND_URL") or "").strip()
    return frontend_url.rstrip("/")


def _record_reminder_task(
    target_date: date,
    meal_slot: str,
    status: str,
    message: str,
    issues: dict,
    *,
    recipients: list[User],
    now: datetime | None = None,
) -> TaskLog:
    if now is None:
        resolved_now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        resolved_now = now.replace(tzinfo=timezone.utc)
    else:
        resolved_now = now.astimezone(timezone.utc)
    task_log = TaskLog(
        task_type=TASK_TYPE,
        task_date=target_date,
        status=status,
        error_message=message if status == "failed" else None,
        error_count=1 if status == "failed" else 0,
        finished_at=resolved_now,
        meta={
            "alert_type": ALERT_TYPE,
            "meal_slot": meal_slot,
            "meal_label": DEFAULT_MEAL_LABELS.get(meal_slot, meal_slot),
            "status_text": message,
            "missing_menu": bool(issues.get("missing_menu")),
            "dish_ids": list(issues.get("dish_ids") or []),
            "missing_sample_dishes": list(issues.get("missing_sample_dishes") or []),
            "recipient_user_ids": [user.id for user in recipients],
            "recipient_dingtalk_user_ids": [user.dingtalk_user_id for user in recipients if user.dingtalk_user_id],
        },
    )
    db.session.add(task_log)
    db.session.commit()
    return task_log


def _has_sent_reminder(target_date: date, meal_slot: str) -> bool:
    logs = TaskLog.query.filter(
        TaskLog.task_type == TASK_TYPE,
        TaskLog.task_date == target_date,
        TaskLog.status == "success",
    ).all()
    return any((log.meta or {}).get("meal_slot") == meal_slot for log in logs)


def _resolve_now(cfg: dict, now_iso: str | None = None) -> datetime:
    tz = _resolve_timezone(cfg)
    if now_iso:
        parsed = datetime.fromisoformat(now_iso)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=tz)
        return parsed.astimezone(tz)
    return datetime.now(tz)


def _resolve_timezone(cfg: dict):
    timezone_label = str(cfg.get("APP_TIMEZONE") or cfg.get("VIDEO_TIMEZONE") or "Asia/Shanghai")
    try:
        return ZoneInfo(timezone_label)
    except Exception:
        return ZoneInfo("Asia/Shanghai")


def _normalize_meal_times(value) -> dict[str, str]:
    raw = value if isinstance(value, dict) else {}
    normalized = deepcopy(DEFAULT_MEAL_TIMES)
    for key in MEAL_SLOT_KEYS:
        text = str(raw.get(key) or normalized[key]).strip()
        normalized[key] = text
    return normalized


def _normalize_user_ids(value) -> list[int]:
    if not isinstance(value, (list, tuple, set)):
        return []

    result: list[int] = []
    seen: set[int] = set()
    for item in value:
        try:
            user_id = int(item)
        except (TypeError, ValueError):
            continue
        if user_id in seen:
            continue
        seen.add(user_id)
        result.append(user_id)
    return result


def _config_bool(value) -> bool:
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes"}
    return bool(value)


def _config_int(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback
