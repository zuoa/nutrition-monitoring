import logging
from flask import Blueprint, request, current_app
from app import db
from app.models import User
from app.utils.jwt_utils import login_required, role_required, api_ok, api_error

bp = Blueprint("sync", __name__)
logger = logging.getLogger(__name__)


@bp.route("/dingtalk/status", methods=["GET"])
@role_required("admin")
def sync_status():
    last_sync = db.session.query(db.func.max(User.sync_at)).scalar()
    total_users = User.query.filter_by(is_active=True).count()
    return api_ok({
        "last_sync": last_sync.isoformat() if last_sync else None,
        "active_users": total_users,
    })


@bp.route("/dingtalk/trigger", methods=["POST"])
@role_required("admin")
def trigger_sync():
    from app.tasks.sync import sync_dingtalk_org
    sync_dingtalk_org.delay()
    return api_ok({"message": "钉钉组织同步任务已提交"})


@bp.route("/students/import", methods=["POST"])
@role_required("admin")
def import_students():
    """Import student list (CSV/Excel)."""
    if "file" not in request.files:
        return api_error("请上传文件")

    file = request.files["file"]
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ("csv", "xls", "xlsx"):
        return api_error("仅支持 CSV、XLS、XLSX 格式")

    content = file.read()
    from app.services.import_service import StudentImportService
    svc = StudentImportService()
    try:
        result = svc.import_file(content, ext)
    except Exception as e:
        logger.error(f"Student import failed: {e}", exc_info=True)
        return api_error(f"导入失败：{str(e)}")
    return api_ok(result)
