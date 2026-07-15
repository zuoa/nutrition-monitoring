import logging
from datetime import date
from celery_app import celery
from app.models import Student

logger = logging.getLogger(__name__)


@celery.task(name="app.tasks.nutrition.compute_nutrition_log")
def compute_nutrition_log(student_id: int, date_str: str):
    from app.services.nutrition_service import NutritionService
    d = date.fromisoformat(date_str)
    svc = NutritionService()
    svc.compute_daily_log(student_id, d)
    logger.debug(f"Computed nutrition log for student {student_id} on {date_str}")


@celery.task(name="app.tasks.nutrition.check_all_alerts")
def check_all_alerts():
    from app.services.nutrition_service import NutritionService
    from app.services.dingtalk import DingTalkService
    from flask import current_app
    from app.models import User, RoleEnum
    from app.services.runtime_config import get_effective_config

    cfg = get_effective_config(current_app.config)
    if not cfg.get("NUTRITION_ALERT_NOTIFICATION_ENABLED", True):
        return {"checked": False, "reason": "notification_disabled"}

    svc = NutritionService()
    dt = DingTalkService(cfg)

    students = Student.query.filter_by(is_active=True).all()

    notified = 0
    for student in students:
        try:
            alerts = svc._check_student_alerts(student.id)
            if not alerts:
                continue

            # Notify parents
            parents = User.query.filter(
                User.role == RoleEnum.parent,
                User.is_active.is_(True),
            ).all()

            for parent in parents:
                if student.id not in (parent.student_ids or []):
                    continue
                for alert in alerts:
                    try:
                        dt.send_work_notification(
                            [parent.dingtalk_user_id],
                            {
                                "msgtype": "text",
                                "text": {
                                    "content": f"[营养预警] {student.name}: {alert['message']}"
                                },
                            },
                        )
                        notified += 1
                    except Exception as e:
                        logger.error(f"Failed to notify parent for alert: {e}")
        except Exception as e:
            logger.error(f"Alert check failed for student {student.id}: {e}")
    return {"checked": True, "students": len(students), "notified": notified}
