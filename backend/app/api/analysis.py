import logging
import os
from datetime import date, datetime
from flask import Blueprint, request, current_app
from app import db
from app.models import CapturedImage, DishRecognition, TaskLog, Dish, ImageStatusEnum
from app.utils.jwt_utils import login_required, role_required, api_ok, api_error
from app.utils.pagination import paginate, paginated_response

bp = Blueprint("analysis", __name__)
logger = logging.getLogger(__name__)


@bp.route("/upload-video", methods=["POST"])
@role_required("admin")
def upload_video():
    """Upload a video file manually and extract frames.

    Request:
        - video_file: video file (multipart/form-data)
        - video_start_time: ISO datetime string (e.g., "2024-03-25T12:00:00")
        - channel_id: optional channel identifier (default: "manual")
    """
    if "video_file" not in request.files:
        return api_error("请上传视频文件")

    video_file = request.files["video_file"]
    if video_file.filename == "":
        return api_error("请选择视频文件")

    # Validate file extension
    allowed_extensions = {".mp4", ".avi", ".mov", ".mkv", ".wmv"}
    file_ext = os.path.splitext(video_file.filename)[1].lower()
    if file_ext not in allowed_extensions:
        return api_error(f"不支持的文件格式，请上传: {', '.join(allowed_extensions)}")

    # Parse video start time
    video_start_time_str = request.form.get("video_start_time")
    if not video_start_time_str:
        return api_error("请提供录像起始时间")

    try:
        video_start_time = datetime.fromisoformat(video_start_time_str.replace("Z", "+00:00"))
    except ValueError:
        return api_error("录像起始时间格式无效，请使用 ISO 格式 (YYYY-MM-DDTHH:MM:SS)")

    channel_id = request.form.get("channel_id", "manual")
    capture_date = video_start_time.date()

    # Save uploaded file
    storage_path = current_app.config.get("NVR_LOCAL_STORAGE_PATH", "/data/nvr_cache")
    upload_dir = os.path.join(storage_path, str(capture_date), "manual_uploads")
    os.makedirs(upload_dir, exist_ok=True)

    safe_filename = f"{channel_id}_{int(video_start_time.timestamp())}{file_ext}"
    video_path = os.path.join(upload_dir, safe_filename)

    try:
        video_file.save(video_path)
    except Exception as e:
        logger.error(f"Failed to save uploaded video: {e}")
        return api_error("保存视频文件失败")

    # Create task log
    task_log = TaskLog(task_type="manual_upload", task_date=capture_date)
    db.session.add(task_log)
    db.session.commit()

    try:
        # Extract frames using VideoAnalyzer
        from app.services.video_analyzer import VideoAnalyzer
        from app.tasks.recognition import run_recognition_batch

        image_path = current_app.config.get("IMAGE_STORAGE_PATH", "/data/images")
        analyzer = VideoAnalyzer(current_app.config)
        output_dir = os.path.join(image_path, str(capture_date), channel_id)

        frames = analyzer.extract_frames(
            video_path, output_dir, video_start_time, channel_id
        )

        total_images = 0
        for frame in frames:
            img = CapturedImage(
                capture_date=capture_date,
                channel_id=channel_id,
                captured_at=frame["captured_at"],
                image_path=frame["image_path"],
                status=ImageStatusEnum.pending,
                source_video=safe_filename,
                diff_score=frame.get("diff_score"),
                is_candidate=frame.get("is_candidate", False),
            )
            db.session.add(img)
            total_images += 1

        task_log.status = "success"
        task_log.total_count = total_images
        task_log.success_count = total_images
        task_log.finished_at = datetime.utcnow()
        db.session.commit()

        # Trigger recognition if images were extracted
        if total_images > 0:
            run_recognition_batch.delay(str(capture_date))

        logger.info(f"Manual video upload complete: {safe_filename}, extracted {total_images} frames")

        return api_ok({
            "message": f"视频上传成功，提取了 {total_images} 张图片",
            "video_filename": safe_filename,
            "frames_extracted": total_images,
            "capture_date": str(capture_date),
        })

    except Exception as e:
        logger.error(f"Manual video upload failed: {e}", exc_info=True)
        task_log.status = "failed"
        task_log.error_message = str(e)
        task_log.finished_at = datetime.utcnow()
        db.session.commit()
        return api_error(f"视频处理失败: {str(e)}")


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
        DishRecognition.is_low_confidence.is_(True),
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
