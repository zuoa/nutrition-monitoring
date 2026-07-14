"""学生 Provider 同步触发 / 状态 / webhook 入口。"""
import logging

from flask import Blueprint, current_app

from app.models import TaskLog
from app.utils.jwt_utils import role_required, api_error, api_ok

bp = Blueprint("students_sync", __name__)
logger = logging.getLogger(__name__)


@bp.route("/trigger", methods=["POST"])
@role_required("admin")
def trigger_sync():
    from app.modules.students.services.rest_student_provider import StudentRosterProviderError
    from app.modules.students.services.sync_backends import get_student_sync_backend
    from app.modules.students.tasks import sync_students

    try:
        backend = get_student_sync_backend(current_app.config)
    except (ValueError, StudentRosterProviderError) as exc:
        return api_error(str(exc), 400)
    if not backend.can_sync:
        return api_error(f"{backend.label}未完成配置", 400)
    sync_students.delay()
    return api_ok({"message": f"{backend.label}同步任务已提交"})


@bp.route("/status", methods=["GET"])
@role_required("admin")
def sync_status():
    from app.modules.students.services.rest_student_provider import StudentRosterProviderError
    from app.modules.students.services.sync_backends import get_student_sync_backend

    last = (
        TaskLog.query.filter_by(task_type="student_sync")
        .order_by(TaskLog.started_at.desc())
        .first()
    )
    try:
        backend = get_student_sync_backend(current_app.config)
        provider = {
            "key": backend.key,
            "label": backend.label,
            "configured": backend.configured,
            "mock": backend.mock,
            "can_sync": backend.can_sync,
        }
    except (ValueError, StudentRosterProviderError) as exc:
        provider = {
            "key": str(current_app.config.get("STUDENT_SYNC_PROVIDER") or ""),
            "label": "未知 Provider",
            "configured": False,
            "mock": False,
            "can_sync": False,
            "error": str(exc),
        }
    return api_ok({
        "last": last.to_dict() if last else None,
        "provider": provider,
        # 兼容旧前端，后续版本可移除。
        "edu_mock": provider["key"] == "dingtalk" and provider["mock"],
    })


@bp.route("/webhook", methods=["POST"])
@role_required("admin")
def webhook():
    """钉钉家校通讯录事件回调入口（占位，默认走定时全量）。"""
    logger.info("收到家校通讯录 webhook（暂未处理增量）")
    return api_ok({"received": True})
