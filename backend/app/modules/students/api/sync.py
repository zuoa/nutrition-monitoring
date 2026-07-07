"""钉钉家校通讯录同步触发 / 状态 / webhook 入口。"""
import logging

from flask import Blueprint, current_app

from app import db
from app.models import TaskLog
from app.utils.jwt_utils import role_required, api_ok, api_error

bp = Blueprint("students_sync", __name__)
logger = logging.getLogger(__name__)


@bp.route("/trigger", methods=["POST"])
@role_required("admin")
def trigger_sync():
    from app.modules.students.tasks import sync_dingtalk_school
    sync_dingtalk_school.delay()
    return api_ok({"message": "钉钉家校通讯录同步任务已提交"})


@bp.route("/status", methods=["GET"])
@role_required("admin")
def sync_status():
    last = (
        TaskLog.query.filter_by(task_type="student_sync")
        .order_by(TaskLog.started_at.desc())
        .first()
    )
    edu_mock = bool(current_app.config.get("DINGTALK_EDU_MOCK")) or not current_app.config.get("DINGTALK_APP_KEY")
    return api_ok({
        "last": last.to_dict() if last else None,
        "edu_mock": edu_mock,
    })


@bp.route("/webhook", methods=["POST"])
@role_required("admin")
def webhook():
    """家校通讯录事件回调入口（本轮占位，默认走定时全量）。"""
    logger.info("收到家校通讯录 webhook（暂未处理增量）")
    return api_ok({"received": True})
