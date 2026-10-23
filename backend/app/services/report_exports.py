"""Private PNG/ZIP artifacts and class export jobs."""

import csv
import io
import logging
import math
import re
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import current_app

from app import db
from app.models import Report, Student, TaskLog
from app.services.report_presentation import render_group_html, render_personal_html, serialize_personal_reports

logger = logging.getLogger(__name__)
EXPORT_TASK_TYPE = "class_report_export"
_pdfium_lock = threading.Lock()


def safe_filename(value):
    return re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]', "_", str(value)).strip(" .")[:90] or "report"


def export_root():
    root = Path(current_app.config.get("REPORT_EXPORT_STORAGE_PATH") or "/data/report_exports")
    root.mkdir(parents=True, exist_ok=True)
    return root


def artifact_path(task_id):
    return export_root() / f"{int(task_id)}.zip"


def render_personal_png(content, *, report_format="detailed"):
    return _render_report_png(lambda **kwargs: render_personal_html(content, report_format=report_format, **kwargs))


def render_group_png(content):
    return _render_report_png(lambda **kwargs: render_group_html(content, **kwargs))


def _render_report_png(render_html):
    """Measure the complete paper, then render one page at twice CSS resolution."""
    from weasyprint import HTML
    from weasyprint.urls import default_url_fetcher
    import pypdfium2 as pdfium
    from PIL import Image

    def local_fetcher(url, *args, **kwargs):
        if not url.startswith("data:"):
            raise ValueError("报告仅使用内置资源")
        return default_url_fetcher(url, *args, **kwargs)

    document = HTML(string=render_html(), url_fetcher=local_fetcher).render()
    # The measuring page has ample room; never silently discard overflow pages.
    if len(document.pages) != 1:
        raise ValueError("报告内容过长，无法生成完整图片")
    body = document.pages[0]._page_box.children[0].children[0]
    height = math.ceil(body.border_height()) + 2
    if height > 14000:
        raise ValueError("报告内容过长，无法生成完整图片")
    pdf = HTML(string=render_html(page_height=height), url_fetcher=local_fetcher).write_pdf()
    with _pdfium_lock, pdfium.PdfDocument(pdf) as doc:
        images = []
        try:
            for page in doc:
                try:
                    bitmap = page.render(scale=1600 / page.get_width())
                    try:
                        images.append(bitmap.to_pil().convert("RGB"))
                    finally:
                        bitmap.close()
                finally:
                    page.close()
            if not images:
                raise ValueError("报告图片生成结果为空")
            with Image.new("RGB", (1600, sum(img.height for img in images)), "white") as combined:
                y = 0
                for img in images:
                    combined.paste(img, (0, y))
                    y += img.height
                output = io.BytesIO()
                combined.save(output, format="PNG", optimize=True)
                return output.getvalue()
        finally:
            for img in images:
                img.close()


def class_report_selection(class_id, start, end, report_type="personal_weekly"):
    students = Student.query.filter_by(class_id=class_id, is_active=True, enrollment_status="enrolled").order_by(Student.student_no, Student.id).all()
    ids = [str(student.id) for student in students]
    reports = Report.query.filter(
        Report.report_type == report_type, Report.target_id.in_(ids),
        Report.period_start == start, Report.period_end == end,
    ).order_by(Report.created_at.desc(), Report.id.desc()).all() if ids else []
    latest = {}
    for report in reports:
        latest.setdefault(report.target_id, report)
    return [{
        "student_id": student.id, "student_no": student.student_no, "student_name": student.name,
        "report_id": latest[str(student.id)].id if str(student.id) in latest else None,
    } for student in students]


def export_task_payload(task):
    meta = task.meta or {}
    expires = meta.get("expires_at")
    expired = bool(expires and datetime.fromisoformat(expires) <= datetime.now(timezone.utc))
    ready = task.status in ("success", "partial") and not expired and artifact_path(task.id).is_file()
    return {
        "id": task.id, "status": task.status, "class_name": meta.get("class_name"),
        "period_start": meta.get("period_start"), "period_end": meta.get("period_end"),
        "report_type": meta.get("report_type", "personal_weekly"),
        "report_format": meta.get("report_format", "detailed"),
        "total_count": task.total_count, "success_count": task.success_count,
        "missing_count": task.invalid_count, "error_count": task.error_count,
        "processed_count": (task.success_count or 0) + (task.error_count or 0) + (task.invalid_count or 0),
        "error_message": task.error_message, "expires_at": expires, "expired": expired,
        "download_ready": ready,
    }


def build_class_export(task_id):
    task = db.session.get(TaskLog, task_id)
    if task is None or task.task_type != EXPORT_TASK_TYPE:
        return
    # Serialize duplicate/re-delivered tasks. A completed export is immutable.
    claimed = TaskLog.query.filter_by(id=task_id, status="pending").update({"status": "running"})
    db.session.commit()
    if not claimed:
        return
    part = None
    try:
        part = export_root() / f"{task_id}.zip.part"
        task = db.session.get(TaskLog, task_id)
        meta = dict(task.meta or {})
        selections = meta["students"]
        report_ids = [s["report_id"] for s in selections if s["report_id"]]
        reports = Report.query.filter(Report.id.in_(report_ids)).all() if report_ids else []
        # One snapshot of all consumption-derived statistics for this job.
        data = {r["id"]: r["content"] for r in serialize_personal_reports(reports)}
        manifest = io.StringIO(newline="")
        writer = csv.writer(manifest)
        writer.writerow(["学号", "姓名", "周期开始", "周期结束", "状态", "文件名", "说明"])
        task.success_count = task.error_count = task.invalid_count = 0
        with zipfile.ZipFile(part, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for student in selections:
                filename = ""
                reason = ""
                if student["report_id"] not in data:
                    status, reason = "缺失", "所选周期无报告"
                    task.invalid_count += 1
                else:
                    try:
                        content = data[student["report_id"]]
                        if int(content.get("student_id", student["student_id"])) != student["student_id"]:
                            raise ValueError("报告对象不一致")
                        filename = safe_filename(f"{student['student_id']}_{str(student['student_no'])[:24]}_{student['student_name'][:20]}_{meta['period_start']}_{meta['period_end']}") + ".png"
                        if meta.get("report_format") == "brief":
                            filename = filename[:-4] + "_简报.png"
                            png = render_personal_png(content, report_format="brief")
                        else:
                            png = render_personal_png(content)
                        archive.writestr(filename, png)
                        status = "成功"
                        task.success_count += 1
                    except Exception:
                        logger.exception("Failed to render report %s in export %s", student["report_id"], task_id)
                        status, reason, filename = "失败", "图片生成失败，请单独重试该报告", ""
                        task.error_count += 1
                # Prevent spreadsheet formula evaluation in the downloadable manifest.
                cells = [student["student_no"], student["student_name"], meta["period_start"], meta["period_end"], status, filename, reason]
                writer.writerow([("'" + str(c)) if str(c).startswith(("=", "+", "-", "@", "\t", "\r")) else c for c in cells])
                db.session.commit()
            archive.writestr("导出清单.csv", manifest.getvalue().encode("utf-8-sig"))
        task.finished_at = datetime.now(timezone.utc)
        if task.success_count:
            part.replace(artifact_path(task_id))
            task.status = "partial" if task.error_count or task.invalid_count else "success"
            task.meta = {**meta, "expires_at": (task.finished_at + timedelta(hours=24)).isoformat()}
        else:
            part.unlink(missing_ok=True)
            task.status = "failed"
            task.error_message = "没有成功导出的报告，请检查缺失报告或重试"
        db.session.commit()
    except Exception:
        logger.exception("Class report export failed: %s", task_id)
        db.session.rollback()
        if part is not None:
            part.unlink(missing_ok=True)
        task = db.session.get(TaskLog, task_id)
        task.status = "failed"
        task.error_message = "班级导出失败，请重新创建导出任务"
        task.finished_at = datetime.now(timezone.utc)
        db.session.commit()


def cleanup_report_exports():
    now = datetime.now(timezone.utc)
    tasks = TaskLog.query.filter(TaskLog.task_type == EXPORT_TASK_TYPE).all()
    cleaned = 0
    for task in tasks:
        meta = task.meta or {}
        if meta.get("expires_at") and datetime.fromisoformat(meta["expires_at"]) <= now:
            path = artifact_path(task.id)
            if path.exists():
                path.unlink()
                cleaned += 1
        started = task.started_at
        if started and started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if task.status in ("pending", "running") and started and started < now - timedelta(hours=2):
            task.status = "failed"
            task.error_message = "导出任务超时，请重新创建"
            task.finished_at = now
            (export_root() / f"{task.id}.zip.part").unlink(missing_ok=True)
    db.session.commit()
    return cleaned
