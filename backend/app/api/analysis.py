import logging
import os
import uuid
from datetime import date, datetime
from flask import Blueprint, request, current_app
from PIL import Image
from app import db
from app.models import (
    CapturedImage,
    DishRecognition,
    TaskLog,
    Dish,
    DishSampleImage,
    EmbeddingStatusEnum,
    ImageStatusEnum,
)
from app.services.embedding_jobs import trigger_local_embedding_rebuild
from app.utils.jwt_utils import login_required, role_required, api_ok, api_error
from app.utils.pagination import paginate, paginated_response

bp = Blueprint("analysis", __name__)
logger = logging.getLogger(__name__)

MAX_DISH_SAMPLE_IMAGES = 12
MIN_ANNOTATION_EDGE = 24
ANALYSIS_TASK_TYPES = ("nvr_download", "ai_recognition", "manual_upload", "region_proposal")


def _parse_task_types(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _normalize_bbox(bbox: dict) -> tuple[int, int, int, int]:
    try:
        x1 = int(round(float(bbox["x1"])))
        y1 = int(round(float(bbox["y1"])))
        x2 = int(round(float(bbox["x2"])))
        y2 = int(round(float(bbox["y2"])))
    except (KeyError, TypeError, ValueError):
        raise ValueError("标注框参数无效")

    left = min(x1, x2)
    top = min(y1, y2)
    right = max(x1, x2)
    bottom = max(y1, y2)
    return left, top, right, bottom


def _create_sample_image_from_crop(
    *,
    source_image: CapturedImage,
    dish: Dish,
    bbox: tuple[int, int, int, int],
) -> tuple[DishSampleImage, dict[str, int], str]:
    image_root = current_app.config.get("IMAGE_STORAGE_PATH", "/data/images")
    dest_dir = os.path.join(image_root, "dish_samples", str(dish.id))
    os.makedirs(dest_dir, exist_ok=True)

    with Image.open(source_image.image_path) as source:
        rgb = source.convert("RGB")
        width, height = rgb.size
        left = max(0, min(bbox[0], width - 1))
        top = max(0, min(bbox[1], height - 1))
        right = max(left + 1, min(bbox[2], width))
        bottom = max(top + 1, min(bbox[3], height))

        if right - left < MIN_ANNOTATION_EDGE or bottom - top < MIN_ANNOTATION_EDGE:
            raise ValueError(f"标注区域太小，宽高至少需要 {MIN_ANNOTATION_EDGE}px")

        crop = rgb.crop((left, top, right, bottom))
        stored_name = f"{uuid.uuid4().hex}.jpg"
        dest_path = os.path.join(dest_dir, stored_name)
        crop.save(dest_path, format="JPEG", quality=95)

    current_max_sort = db.session.query(db.func.max(DishSampleImage.sort_order)).filter(
        DishSampleImage.dish_id == dish.id,
        DishSampleImage.is_active.is_(True),
    ).scalar() or 0
    has_cover = db.session.query(DishSampleImage.id).filter(
        DishSampleImage.dish_id == dish.id,
        DishSampleImage.is_cover.is_(True),
        DishSampleImage.is_active.is_(True),
    ).first()

    sample_image = DishSampleImage(
        dish_id=dish.id,
        image_path=dest_path,
        original_filename=(
            f"captured_{source_image.id}_{left}_{top}_{right}_{bottom}.jpg"
        ),
        sort_order=int(current_max_sort) + 1,
        is_cover=not bool(has_cover),
        embedding_status=EmbeddingStatusEnum.pending,
    )
    return sample_image, {"x1": left, "y1": top, "x2": right, "y2": bottom}, dest_path


def _enqueue_region_proposal_task(img: CapturedImage, prompt: str | None = None) -> TaskLog:
    from app.tasks.region_proposal import propose_regions_for_image

    normalized_prompt = (prompt or "").strip() or None
    task_log = TaskLog(
        task_type="region_proposal",
        task_date=img.capture_date,
        meta={
            "image_id": img.id,
            "image_path": img.image_path,
            "prompt": normalized_prompt or "",
            "status_text": "任务已提交，等待执行",
        },
    )
    db.session.add(task_log)
    db.session.commit()

    try:
        celery_task = propose_regions_for_image.delay(task_log.id, img.id, normalized_prompt)
        task_log.meta = {
            **(task_log.meta or {}),
            "celery_task_id": celery_task.id,
            "status_text": "任务已提交，等待执行",
        }
        db.session.commit()
    except Exception as e:
        task_log.status = "failed"
        task_log.error_count = 1
        task_log.error_message = str(e)
        task_log.finished_at = datetime.utcnow()
        task_log.meta = {
            **(task_log.meta or {}),
            "status_text": "任务提交失败",
        }
        db.session.commit()
        raise

    return task_log


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
    elif task_types := _parse_task_types(request.args.get("task_types")):
        q = q.filter(TaskLog.task_type.in_(task_types))
    elif request.args.get("scope") == "analysis":
        q = q.filter(TaskLog.task_type.in_(ANALYSIS_TASK_TYPES))
    if status := request.args.get("status"):
        q = q.filter(TaskLog.status == status)
    items, total, page, page_size = paginate(q)
    return api_ok(paginated_response([t.to_dict() for t in items], total, page, page_size))


@bp.route("/tasks/<int:task_id>", methods=["GET"])
@login_required
def get_task(task_id):
    task = TaskLog.query.get_or_404(task_id)
    return api_ok(task.to_dict())


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
    elif task.task_type == "region_proposal":
        image_id = int((task.meta or {}).get("image_id") or 0)
        if not image_id:
            return api_error("缺少原始图片信息，无法重试")
        img = CapturedImage.query.get_or_404(image_id)
        _enqueue_region_proposal_task(img, prompt=(task.meta or {}).get("prompt"))
    else:
        return api_error("当前任务类型不支持重试")

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


@bp.route("/images/<int:image_id>/annotations", methods=["POST"])
@role_required("admin")
def create_image_annotation(image_id):
    img = CapturedImage.query.get_or_404(image_id)
    data = request.get_json() or {}

    try:
        dish_id = int(data.get("dish_id"))
    except (TypeError, ValueError):
        dish_id = 0
    if not dish_id:
        return api_error("请选择要关联的菜品")

    dish = Dish.query.get(dish_id)
    if not dish or not dish.is_active:
        return api_error("目标菜品不存在或已停用")

    bbox_data = data.get("bbox") or {}
    try:
        bbox = _normalize_bbox(bbox_data)
    except ValueError as e:
        return api_error(str(e))

    if not img.image_path:
        return api_error("图片路径不存在")
    if not os.path.exists(img.image_path):
        return api_error("图片文件不存在")

    active_count = DishSampleImage.query.filter_by(dish_id=dish.id, is_active=True).count()
    if active_count >= MAX_DISH_SAMPLE_IMAGES:
        return api_error(f"每个菜品最多上传 {MAX_DISH_SAMPLE_IMAGES} 张样图")

    created_path = None
    rebuild_triggered = False

    try:
        sample_image, normalized_bbox, created_path = _create_sample_image_from_crop(
            source_image=img,
            dish=dish,
            bbox=bbox,
        )
        db.session.add(sample_image)
        db.session.commit()

        try:
            rebuild_triggered = trigger_local_embedding_rebuild(
                current_app.config,
                reason="captured image annotation crop",
            )
        except Exception as e:
            logger.warning("Failed to trigger local embedding rebuild after annotation crop: %s", e)
    except ValueError as e:
        db.session.rollback()
        if created_path and os.path.exists(created_path):
            try:
                os.unlink(created_path)
            except OSError:
                pass
        return api_error(str(e))
    except Exception as e:
        db.session.rollback()
        if created_path and os.path.exists(created_path):
            try:
                os.unlink(created_path)
            except OSError:
                pass
        logger.error("Failed to create sample image from captured image %s: %s", image_id, e, exc_info=True)
        return api_error(f"保存标注失败: {str(e)}"), 500

    return api_ok({
        "message": "标注已保存为菜品样图" + ("，并已提交 embedding 重建任务" if rebuild_triggered else ""),
        "source_image_id": img.id,
        "dish": dish.to_dict(),
        "bbox": normalized_bbox,
        "sample_image": sample_image.to_dict(),
        "sample_image_count": DishSampleImage.query.filter_by(dish_id=dish.id, is_active=True).count(),
    }), 201


@bp.route("/images/<int:image_id>/region-proposals", methods=["POST"])
@role_required("admin")
def propose_image_regions(image_id):
    img = CapturedImage.query.get_or_404(image_id)
    data = request.get_json(silent=True) or {}

    if not img.image_path:
        return api_error("图片路径不存在")
    if not os.path.exists(img.image_path):
        return api_error("图片文件不存在")

    prompt = str(data.get("prompt") or "").strip() or None

    try:
        task_log = _enqueue_region_proposal_task(img, prompt=prompt)
    except Exception as e:
        logger.error("Failed to enqueue region proposal task for captured image %s: %s", image_id, e, exc_info=True)
        return api_error(f"提交菜区提议任务失败: {str(e)}"), 500

    return api_ok({
        "image_id": img.id,
        "task": task_log.to_dict(),
    }, message="菜区提议任务已提交"), 202


@bp.route("/images/<int:image_id>/recognize", methods=["POST"])
@role_required("admin")
def recognize_image(image_id):
    """Trigger AI recognition for a single image."""
    img = CapturedImage.query.get_or_404(image_id)

    if img.is_candidate:
        return api_error("候选帧不支持单独识别")

    if img.status not in (
        ImageStatusEnum.pending,
        ImageStatusEnum.error,
        ImageStatusEnum.identified,
        ImageStatusEnum.matched,
    ):
        return api_error("当前图片状态不支持重新识别")

    has_manual_review = DishRecognition.query.filter_by(
        image_id=image_id,
        is_manual=True,
    ).first()
    if has_manual_review:
        return api_error("该图片已有人工复核结果，不能重新发起 AI 识别")

    from app.tasks.recognition import recognize_single_image

    # Clear previous AI recognition result so the UI reflects the rerun immediately.
    DishRecognition.query.filter_by(image_id=image_id, is_manual=False).delete()
    img.status = ImageStatusEnum.pending
    db.session.commit()

    recognize_single_image.delay(image_id)

    data = img.to_dict()
    recs = DishRecognition.query.filter_by(image_id=image_id).all()
    data["recognitions"] = [r.to_dict() for r in recs]
    return api_ok(data)


@bp.route("/images/<int:image_id>/describe", methods=["POST"])
@role_required("admin")
def describe_image(image_id):
    """Use VL model to describe dishes in image for better dish description writing.

    This endpoint is admin-only and returns a visual description of dishes
    in the image without identifying them.
    """
    img = CapturedImage.query.get_or_404(image_id)

    if not img.image_path:
        return api_error("图片路径不存在")

    import os
    if not os.path.exists(img.image_path):
        return api_error("图片文件不存在")

    try:
        from app.services.qwen_vl import QwenVLService

        qwen = QwenVLService({
            "QWEN_API_KEY": current_app.config.get("QWEN_API_KEY"),
            "QWEN_API_URL": current_app.config.get("QWEN_API_URL"),
            "QWEN_MODEL": current_app.config.get("QWEN_MODEL"),
            "QWEN_TIMEOUT": current_app.config.get("QWEN_TIMEOUT", 60),
            "QWEN_MAX_QPS": current_app.config.get("QWEN_MAX_QPS", 10),
        })

        result = qwen.describe_dishes(img.image_path)
        return api_ok({
            "description": result.get("description", ""),
        })

    except Exception as e:
        logger.error(f"Failed to describe image {image_id}: {e}", exc_info=True)
        return api_error(f"生成描述失败: {str(e)}")


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
