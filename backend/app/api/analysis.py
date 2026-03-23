import logging
from datetime import date
from flask import Blueprint, request
from app import db
from app.models import CapturedImage, DishRecognition, TaskLog, Dish, ImageStatusEnum
from app.utils.jwt_utils import login_required, role_required, api_ok, api_error
from app.utils.pagination import paginate, paginated_response

bp = Blueprint("analysis", __name__)
logger = logging.getLogger(__name__)


@bp.route("/tasks", methods=["GET"])
@login_required
def list_tasks():
    q = TaskLog.query.order_by(TaskLog.started_at.desc())
    if task_type := request.args.get("task_type"):
        q = q.filter(TaskLog.task_type == task_type)
    if status := request.args.get("status"):
        q = q.filter(TaskLog.status == status)
    items, total, page, page_size = paginate(q)
    return api_ok(paginated_response([t.to_dict() for t in items], total, page, page_size))


@bp.route("/tasks/<int:task_id>/retry", methods=["POST"])
@role_required("admin")
def retry_task(task_id):
    task = TaskLog.query.get_or_404(task_id)
    if task.status not in ("failed", "partial"):
        return api_error("只能重试失败或部分完成的任务")

    if task.task_type == "nvr_download":
        from app.tasks.video import download_nvr_videos
        download_nvr_videos.delay(task.task_date.isoformat())
    elif task.task_type == "ai_recognition":
        from app.tasks.recognition import run_recognition_batch
        run_recognition_batch.delay(task.task_date.isoformat())

    return api_ok({"message": "重试任务已提交"})


@bp.route("/tasks/trigger", methods=["POST"])
@role_required("admin")
def trigger_analysis():
    """Manually trigger NVR download for a date."""
    data = request.get_json() or {}
    date_str = data.get("date", date.today().isoformat())
    from app.tasks.video import download_nvr_videos
    download_nvr_videos.delay(date_str)
    return api_ok({"message": f"已触发 {date_str} 的视频分析任务"})


@bp.route("/images", methods=["GET"])
@login_required
def list_images():
    q = CapturedImage.query.order_by(CapturedImage.captured_at.desc())
    if date_str := request.args.get("date"):
        try:
            d = date.fromisoformat(date_str)
            q = q.filter(CapturedImage.capture_date == d)
        except ValueError:
            return api_error("日期格式无效")
    if status := request.args.get("status"):
        q = q.filter(CapturedImage.status == status)
    if channel := request.args.get("channel_id"):
        q = q.filter(CapturedImage.channel_id == channel)

    items, total, page, page_size = paginate(q)

    result = []
    for img in items:
        d = img.to_dict()
        # Include recognition results
        recs = DishRecognition.query.filter_by(image_id=img.id).all()
        d["recognitions"] = [r.to_dict() for r in recs]
        result.append(d)

    return api_ok(paginated_response(result, total, page, page_size))


@bp.route("/images/<int:image_id>", methods=["GET"])
@login_required
def get_image(image_id):
    img = CapturedImage.query.get_or_404(image_id)
    data = img.to_dict()
    recs = DishRecognition.query.filter_by(image_id=image_id).all()
    data["recognitions"] = [r.to_dict() for r in recs]
    return api_ok(data)


@bp.route("/images/<int:image_id>/review", methods=["PUT"])
@role_required("admin")
def review_image(image_id):
    """Manual review: correct dish recognitions for an image."""
    img = CapturedImage.query.get_or_404(image_id)
    data = request.get_json() or {}
    dish_ids = data.get("dish_ids", [])

    # Delete existing recognitions and create manual ones
    DishRecognition.query.filter_by(image_id=image_id).delete()

    for dish_id in dish_ids:
        dish = Dish.query.get(dish_id)
        if not dish:
            continue
        rec = DishRecognition(
            image_id=image_id,
            dish_id=dish_id,
            dish_name_raw=dish.name,
            confidence=1.0,
            is_low_confidence=False,
            is_manual=True,
            model_version="manual",
        )
        db.session.add(rec)

    img.status = ImageStatusEnum.identified
    db.session.commit()

    # Re-trigger matching
    from app.tasks.matching import match_single_image
    match_single_image.delay(image_id)

    return api_ok(img.to_dict())


@bp.route("/summary", methods=["GET"])
@login_required
def get_daily_summary():
    """Get analysis summary for a date."""
    date_str = request.args.get("date", date.today().isoformat())
    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        return api_error("日期格式无效")

    total = CapturedImage.query.filter_by(capture_date=d).count()
    pending = CapturedImage.query.filter_by(capture_date=d, status=ImageStatusEnum.pending).count()
    identified = CapturedImage.query.filter_by(capture_date=d, status=ImageStatusEnum.identified).count()
    matched = CapturedImage.query.filter_by(capture_date=d, status=ImageStatusEnum.matched).count()
    error = CapturedImage.query.filter_by(capture_date=d, status=ImageStatusEnum.error).count()

    low_conf = DishRecognition.query.join(CapturedImage).filter(
        CapturedImage.capture_date == d,
        DishRecognition.is_low_confidence == True,
    ).count()

    return api_ok({
        "date": date_str,
        "total_images": total,
        "pending": pending,
        "identified": identified,
        "matched": matched,
        "error": error,
        "low_confidence_recognitions": low_conf,
    })
