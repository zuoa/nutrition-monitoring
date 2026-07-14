import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from celery_app import celery
from app import db
from app.models import DailyMenu, Dish, DishSampleImage, TaskLog, User, RoleEnum
from app.models.menu import (
    RECOGNITION_MENU_SCOPE_ALL,
    get_meal_slot_keys,
    get_meal_slot_map,
    normalize_meal_dish_ids,
    normalize_recognition_menu_scope,
)
from app.services.runtime_config import get_effective_config
from app.services.dingtalk import (
    DEFAULT_DINGTALK_ROBOT_WEBHOOK_PREFIX,
    resolve_robot_webhook_prefix,
)

logger = logging.getLogger(__name__)

TASK_TYPE = "menu_sample_reminder"
ALERT_TYPE = "menu_or_sample_missing"
DINGTALK_MODE_APP = "app"
DINGTALK_MODE_WEBHOOK = "webhook"


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
    if _has_sent_reminder(cfg, target_date, meal_slot):
        return {"sent": False, "reason": "already_sent", "meal_slot": meal_slot}

    issues = _build_menu_sample_issues(cfg, target_date, meal_slot)
    if not issues["missing_menu"] and not issues["missing_sample_dishes"]:
        return {"sent": False, "reason": "ok", "meal_slot": meal_slot}

    delivery_mode = _resolve_dingtalk_delivery_mode(cfg)
    recipients = [] if delivery_mode == DINGTALK_MODE_WEBHOOK else _resolve_responsible_users(cfg)
    if delivery_mode == DINGTALK_MODE_APP and not recipients:
        task_log = _record_reminder_task(
            target_date,
            meal_slot,
            "failed",
            "菜单/样图提醒未配置可用责任人",
            issues,
            recipients=[],
            now=now,
            cfg=cfg,
        )
        return {
            "sent": False,
            "reason": "no_recipients",
            "meal_slot": meal_slot,
            "task_id": task_log.id,
        }

    message_prefix = (
        resolve_robot_webhook_prefix(cfg)
        if delivery_mode == DINGTALK_MODE_WEBHOOK
        else DEFAULT_DINGTALK_ROBOT_WEBHOOK_PREFIX
    )
    message = _build_reminder_message(cfg, target_date, meal_slot, issues, prefix=message_prefix)
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
            cfg=cfg,
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
        cfg=cfg,
    )
    logger.info(
        "Menu sample reminder sent for %s %s via %s (recipient_count=%s)",
        target_date,
        meal_slot,
        delivery_mode,
        len(recipients),
    )
    return {
        "sent": True,
        "meal_slot": meal_slot,
        "task_id": task_log.id,
        "delivery_mode": delivery_mode,
        "recipient_count": len(recipients),
    }


def _resolve_due_meal_slots(cfg: dict, now: datetime) -> list[str]:
    before_minutes = _config_int(cfg.get("MENU_REMINDER_BEFORE_MINUTES", 30), 30)
    due_slots: list[str] = []
    current_minute = now.replace(second=0, microsecond=0)

    for slot in get_meal_slot_keys(cfg):
        time_text = _normalize_meal_time(cfg, slot)
        if not time_text:
            continue
        try:
            hour_str, minute_str = time_text.split(":", 1)
            meal_dt = current_minute.replace(hour=int(hour_str), minute=int(minute_str))
        except (AttributeError, ValueError):
            logger.warning("Invalid menu reminder meal time for %s: %r", slot, time_text)
            continue

        reminder_dt = meal_dt - timedelta(minutes=max(0, before_minutes))
        if current_minute == reminder_dt:
            due_slots.append(slot)

    return due_slots


def _build_menu_sample_issues(cfg: dict, target_date: date, meal_slot: str) -> dict:
    menu_scope = normalize_recognition_menu_scope(cfg.get("RECOGNITION_MENU_SCOPE", "all"))

    # 全量库模式：菜单可选，识别会与所有启用菜品比对，
    # 因此只检查全库里缺样图的菜品，不再就“菜单未设置”提醒。
    if menu_scope == RECOGNITION_MENU_SCOPE_ALL:
        dishes = (
            Dish.query.filter(Dish.is_active.is_(True))
            .order_by(Dish.name.asc())
            .all()
        )
        return {
            "menu_scope": menu_scope,
            "missing_menu": False,
            "dish_ids": [dish.id for dish in dishes],
            "missing_sample_dishes": _dishes_without_samples(dishes),
        }

    # 当顿餐 / 当天模式：菜单必填，只检查菜单选中菜品的样图。
    menu = DailyMenu.query.filter_by(menu_date=target_date).first()
    normalized = normalize_meal_dish_ids(menu.meal_dish_ids if menu else None, cfg)
    dish_ids = normalized.get(meal_slot) or []
    missing_menu = bool(not menu or menu.is_default or not dish_ids)

    dishes = (
        Dish.query.filter(
            Dish.id.in_(dish_ids),
            Dish.is_active.is_(True),
        ).order_by(Dish.name.asc()).all()
        if dish_ids
        else []
    )

    return {
        "menu_scope": menu_scope,
        "missing_menu": missing_menu,
        "dish_ids": dish_ids,
        "missing_sample_dishes": _dishes_without_samples(dishes),
    }


def _dishes_without_samples(dishes: list[Dish]) -> list[dict]:
    if not dishes:
        return []

    active_sample_counts = dict(
        db.session.query(
            DishSampleImage.dish_id,
            db.func.count(DishSampleImage.id),
        ).filter(
            DishSampleImage.dish_id.in_([dish.id for dish in dishes]),
            DishSampleImage.is_active.is_(True),
        ).group_by(DishSampleImage.dish_id).all()
    )
    return [
        {"id": dish.id, "name": dish.name}
        for dish in dishes
        if int(active_sample_counts.get(dish.id) or 0) == 0
    ]


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

    dt = DingTalkService(cfg)
    msg = {"msgtype": "text", "text": {"content": message}}
    if _resolve_dingtalk_delivery_mode(cfg) == DINGTALK_MODE_WEBHOOK:
        result = dt.send_robot_webhook(msg)
        if result.get("errcode") != 0:
            raise RuntimeError(f"钉钉 Webhook 消息发送失败: {result}")
        return

    user_ids = [user.dingtalk_user_id for user in recipients if user.dingtalk_user_id]
    if not user_ids:
        raise ValueError("没有可用的钉钉 user_id")
    for offset in range(0, len(user_ids), 100):
        result = dt.send_work_notification(user_ids[offset:offset + 100], msg)
        if result.get("errcode") != 0:
            raise RuntimeError(f"钉钉消息发送失败: {result}")


def _build_reminder_message(
    cfg: dict,
    target_date: date,
    meal_slot: str,
    issues: dict,
    *,
    prefix: str = DEFAULT_DINGTALK_ROBOT_WEBHOOK_PREFIX,
) -> str:
    meal_label = get_meal_slot_map(cfg).get(meal_slot, {}).get("label") or meal_slot
    menu_scope = normalize_recognition_menu_scope(cfg.get("RECOGNITION_MENU_SCOPE", "all"))
    is_full_library = menu_scope == RECOGNITION_MENU_SCOPE_ALL

    lines: list[str] = []
    if is_full_library:
        # 全量库模式下菜单可选，只提示样图缺失。
        lines.append(
            f"{prefix} {target_date.isoformat()} 当前识别范围为“全量菜品库”，"
            "以下菜品缺少样图，可能影响识别准确率："
        )
    else:
        lines.append(
            f"{prefix} {target_date.isoformat()} {meal_label}即将开始，请补齐菜单和菜品样图。"
        )
        if issues.get("missing_menu"):
            lines.append(f"- {meal_label}菜单未设置或未选择菜品")

    missing_sample_dishes = issues.get("missing_sample_dishes") or []
    if missing_sample_dishes:
        names = "、".join(item["name"] for item in missing_sample_dishes[:20])
        suffix = f" 等 {len(missing_sample_dishes)} 个菜品" if len(missing_sample_dishes) > 20 else ""
        lines.append(f"- 缺少菜品样图：{names}{suffix}")

    lines.append("请在样图采集页面补齐。" if is_full_library else "请在菜单管理和样图采集页面处理。")

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
    cfg: dict | None = None,
) -> TaskLog:
    if now is None:
        resolved_now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        resolved_now = now.replace(tzinfo=timezone.utc)
    else:
        resolved_now = now.astimezone(timezone.utc)
    meal_label = (cfg and get_meal_slot_map(cfg).get(meal_slot, {}).get("label")) or meal_slot
    task_log = TaskLog(
        task_type=TASK_TYPE,
        task_date=target_date,
        status=status,
        error_message=message if status == "failed" else None,
        error_count=1 if status == "failed" else 0,
        finished_at=resolved_now,
        meta={
            "alert_type": ALERT_TYPE,
            "dingtalk_delivery_mode": _resolve_dingtalk_delivery_mode(cfg or {}),
            "meal_slot": meal_slot,
            "meal_label": meal_label,
            "menu_scope": normalize_recognition_menu_scope(
                issues.get("menu_scope") or RECOGNITION_MENU_SCOPE_ALL
            ),
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


def _has_sent_reminder(cfg: dict, target_date: date, meal_slot: str) -> bool:
    logs = TaskLog.query.filter(
        TaskLog.task_type == TASK_TYPE,
        TaskLog.task_date == target_date,
        TaskLog.status == "success",
    ).all()
    menu_scope = normalize_recognition_menu_scope(cfg.get("RECOGNITION_MENU_SCOPE", "all"))
    if menu_scope == RECOGNITION_MENU_SCOPE_ALL:
        # 全量库样图检查与具体餐次无关，同一天只提醒一次。
        return any((log.meta or {}).get("menu_scope") == RECOGNITION_MENU_SCOPE_ALL for log in logs)
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


def _normalize_meal_time(cfg: dict, slot_key: str) -> str:
    """Return the reminder trigger time (slot start) for a configured meal slot."""
    slot = get_meal_slot_map(cfg).get(slot_key)
    if not slot:
        return ""
    return str(slot.get("start") or "").strip()


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


def _resolve_dingtalk_delivery_mode(cfg: dict) -> str:
    value = str(cfg.get("MENU_REMINDER_DINGTALK_MODE") or DINGTALK_MODE_APP).strip().lower()
    return DINGTALK_MODE_WEBHOOK if value == DINGTALK_MODE_WEBHOOK else DINGTALK_MODE_APP


def _config_bool(value) -> bool:
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes"}
    return bool(value)


def _config_int(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback
