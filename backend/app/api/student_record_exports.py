"""Administrator download of original student event records."""

from datetime import date
from tempfile import SpooledTemporaryFile

from flask import Blueprint, current_app, request, send_file

from app.services.runtime_config import get_effective_config
from app.services.student_record_exports import (
    StudentRecordExportError, default_export_dates, export_timezone, write_student_records,
)
from app.utils.jwt_utils import api_error, api_ok, role_required

bp = Blueprint("student_record_exports", __name__)


@bp.get("/defaults")
@role_required("admin")
def get_defaults():
    tz = export_timezone(get_effective_config(current_app.config))
    start, end = default_export_dates(tz)
    return api_ok({"date_from": start.isoformat(), "date_to": end.isoformat(), "timezone": str(tz)})


@bp.post("/download")
@role_required("admin")
def download():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return api_error("请选择导出的开始日期和结束日期")
    try:
        start = date.fromisoformat(data.get("date_from"))
        end = date.fromisoformat(data.get("date_to"))
    except (TypeError, ValueError):
        return api_error("请选择有效的开始日期和结束日期")

    student_no = data.get("student_no", "")
    if not isinstance(student_no, str) or len(student_no.strip()) > 64:
        return api_error("学号最多 64 个字符")

    config = get_effective_config(current_app.config)
    output = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    try:
        counts = write_student_records(output, start, end, export_timezone(config), config, student_no=student_no.strip())
        if not any(counts.values()):
            output.close()
            return api_error("所选范围内暂无记录，请调整日期或学号", 404)
        size = output.tell()
        output.seek(0)
        response = send_file(
            output, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True, download_name=f"学生饮食运动明细_{start}至{end}.xlsx", conditional=False, max_age=0,
        )
        response.content_length = size
        response.cache_control.no_store = True
        return response
    except StudentRecordExportError as exc:
        output.close()
        return api_error(str(exc))
    except Exception:
        output.close()
        current_app.logger.exception("Student record export failed")
        return api_error("生成 Excel 失败，请稍后重试", 500)
