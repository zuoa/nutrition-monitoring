import logging
import json
import os
import tempfile
import uuid
from datetime import date, datetime, timezone
from flask import Blueprint, request, current_app
from sqlalchemy import func
from PIL import Image
from app import db
from app.models import (
    CapturedImage,
    CapturedImageRegion,
    DishRecognition,
    MatchResult,
    MatchStatusEnum,
    TaskLog,
    Dish,
    DishSampleImage,
    DailyMenu,
    EmbeddingStatusEnum,
    ImageStatusEnum,
    RegionRecognitionStatusEnum,
    RegionReviewStatusEnum,
)
from app.models.menu import (
    MENU_NOT_CONFIGURED_ALERT_TYPE,
    RECOGNITION_MENU_SCOPE_ALL,
    get_meal_slot_keys,
    is_menu_configured,
    menu_not_configured_message,
    normalize_recognition_menu_scope,
    resolve_meal_slot_for_datetime,
)
from app.services.embedding_jobs import trigger_local_embedding_rebuild
from app.services.inference_client import (
    InferenceServiceError,
    make_detector_client,
    make_retrieval_client,
)
from app.services.runtime_config import get_effective_config
from app.services.video_sources import VideoSourceConfigError, VideoSourceManager, build_channel_display_name_map
from app.services.region_candidates import bind_region_candidate
from app.utils.jwt_utils import login_required, role_required, api_ok, api_error
from app.utils.pagination import paginate, paginated_response

bp = Blueprint("analysis", __name__)
logger = logging.getLogger(__name__)

MAX_DISH_SAMPLE_IMAGES = 12
MIN_ANNOTATION_EDGE = 24
ANALYSIS_TASK_TYPES = ("video_source_sync", "nvr_download", "ai_recognition", "manual_upload", "region_proposal")
MATCHED_IMAGE_MATCH_STATUSES = (
    MatchStatusEnum.matched,
    MatchStatusEnum.time_matched_only,
    MatchStatusEnum.confirmed,
)


def _build_image_match_summaries(image_ids: list[int]) -> dict[int, dict]:
    normalized_ids = [int(image_id) for image_id in image_ids if int(image_id) > 0]
    if not normalized_ids:
        return {}

    matches = MatchResult.query.filter(
        MatchResult.image_id.in_(normalized_ids),
        MatchResult.consumption_record_id.isnot(None),
        MatchResult.status.in_(MATCHED_IMAGE_MATCH_STATUSES),
    ).order_by(
        MatchResult.created_at.desc(),
        MatchResult.id.desc(),
    ).all()

    summaries: dict[int, dict] = {}
    for match in matches:
        if not match.image_id:
            continue
        summary = summaries.setdefault(match.image_id, {
            "is_matched": True,
            "match_count": 0,
            "statuses": [],
            "latest_status": None,
            "latest_match_id": None,
        })
        status = match.status.value if match.status else None
        summary["match_count"] += 1
        if status and status not in summary["statuses"]:
            summary["statuses"].append(status)
        if summary["latest_status"] is None:
            summary["latest_status"] = status
            summary["latest_match_id"] = match.id

    return summaries


def _calc_recognition_price_total(recognitions: list[DishRecognition]) -> float:
    total = 0.0
    for recognition in recognitions:
        if recognition.is_low_confidence:
            continue
        if recognition.dish_id and recognition.dish and recognition.dish.price is not None:
            total += float(recognition.dish.price)
    return round(total, 2)


def _attach_image_recognition_data(data: dict, recognitions: list[DishRecognition]) -> dict:
    data["recognitions"] = [recognition.to_dict() for recognition in recognitions]
    data["recognition_price_total"] = _calc_recognition_price_total(recognitions)
    return data


def _safe_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _task_elapsed_seconds(task: TaskLog) -> float | None:
    meta = task.meta if isinstance(task.meta, dict) else {}
    duration = _safe_float(meta.get("analysis_duration_seconds"))
    if duration is None and task.started_at and task.finished_at:
        try:
            duration = (task.finished_at - task.started_at).total_seconds()
        except TypeError:
            duration = (
                task.finished_at.replace(tzinfo=None)
                - task.started_at.replace(tzinfo=None)
            ).total_seconds()
    if duration is None:
        return None
    return round(max(0.0, duration), 3)


def _task_processed_image_count(task: TaskLog) -> int:
    meta = task.meta if isinstance(task.meta, dict) else {}
    try:
        return max(0, int(meta.get("processed_image_count")))
    except (TypeError, ValueError):
        return max(0, int(task.success_count or 0) + int(task.error_count or 0))


def _aggregate_image_analysis_duration(start_date: date, end_date: date) -> dict:
    tasks = TaskLog.query.filter(
        TaskLog.task_type == "ai_recognition",
        TaskLog.task_date >= start_date,
        TaskLog.task_date <= end_date,
        TaskLog.status.in_(("success", "partial")),
        TaskLog.finished_at.isnot(None),
    ).all()

    task_count = 0
    processed_images = 0
    total_duration = 0.0
    image_duration_total = 0.0

    for task in tasks:
        processed_count = _task_processed_image_count(task)
        if processed_count <= 0:
            continue

        duration = _task_elapsed_seconds(task)
        if duration is None:
            continue

        meta = task.meta if isinstance(task.meta, dict) else {}
        image_duration = _safe_float(meta.get("image_processing_duration_seconds"))
        if image_duration is None:
            image_duration = duration

        task_count += 1
        processed_images += processed_count
        total_duration += duration
        image_duration_total += max(0.0, image_duration)

    return {
        "image_analysis_task_count": task_count,
        "image_analysis_processed_images": processed_images,
        "image_analysis_duration_seconds": round(total_duration, 3),
        "image_analysis_avg_seconds": (
            round(image_duration_total / processed_images, 3)
            if processed_images > 0 else None
        ),
    }


# Statuses rolled up into the "待处理" (pending) bucket — kept in sync with the
# overall `pending` count above so both dimensions share the same definition.
_PENDING_STATUS_VALUES = (
    ImageStatusEnum.pending,
    ImageStatusEnum.queued,
    ImageStatusEnum.processing,
    ImageStatusEnum.retry_wait,
)


def _build_channel_breakdown(date_filters) -> list[dict]:
    """Per-channel image counts bucketed by status, for the overview's multi-dimension view.

    One GROUP BY query over (channel_id, status), pivoted into five buckets that match
    the by-status panel: pending / identified / matched / invalid / error. The five
    buckets sum to each channel's displayed total, so the stacked bar always fills 100%.
    Channel ids are resolved to friendly names via the video-source channel catalog.
    """
    rows = (
        db.session.query(
            CapturedImage.channel_id,
            CapturedImage.status,
            func.count(CapturedImage.id),
        )
        .filter(*date_filters)
        .group_by(CapturedImage.channel_id, CapturedImage.status)
        .all()
    )

    buckets_by_channel: dict[str, dict[str, int]] = {}
    for channel_id, status, count in rows:
        if not channel_id:
            continue
        buckets = buckets_by_channel.setdefault(channel_id, {
            "pending": 0,
            "identified": 0,
            "matched": 0,
            "invalid": 0,
            "error": 0,
        })
        count = int(count or 0)
        if status in _PENDING_STATUS_VALUES:
            buckets["pending"] += count
        elif status == ImageStatusEnum.identified:
            buckets["identified"] += count
        elif status == ImageStatusEnum.matched:
            buckets["matched"] += count
        elif status == ImageStatusEnum.invalid:
            buckets["invalid"] += count
        elif status == ImageStatusEnum.error:
            buckets["error"] += count
        # Any future status is excluded from the displayed buckets (and thus from
        # the total below) to keep the stacked-bar math consistent.

    display_names = build_channel_display_name_map()
    breakdown = []
    for channel_id, buckets in buckets_by_channel.items():
        total = sum(buckets.values())
        if total <= 0:
            continue
        breakdown.append({
            "channel_id": channel_id,
            "channel_name": display_names.get(channel_id, channel_id),
            "total": total,
            **buckets,
        })

    breakdown.sort(key=lambda item: item["total"], reverse=True)
    return breakdown


def _record_menu_not_configured_alert(task_type: str, target_date: date) -> TaskLog:
    message = menu_not_configured_message(target_date)
    existing = TaskLog.query.filter(
        TaskLog.task_type == task_type,
        TaskLog.task_date == target_date,
        TaskLog.status == "failed",
        TaskLog.meta["alert_type"].as_string() == MENU_NOT_CONFIGURED_ALERT_TYPE,
    ).order_by(TaskLog.id.desc()).first()
    if existing:
        return existing

    task_log = TaskLog(
        task_type=task_type,
        task_date=target_date,
        status="failed",
        error_message=message,
        error_count=1,
        finished_at=datetime.utcnow(),
        meta={
            "alert_type": MENU_NOT_CONFIGURED_ALERT_TYPE,
            "status_text": message,
        },
    )
    db.session.add(task_log)
    db.session.commit()
    logger.warning(message)
    return task_log


def _requires_configured_menu_for_recognition() -> bool:
    cfg = get_effective_config(current_app.config)
    return normalize_recognition_menu_scope(
        cfg.get("RECOGNITION_MENU_SCOPE", "all"),
    ) != RECOGNITION_MENU_SCOPE_ALL


def _parse_task_types(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_candidate_dish_ids(value) -> list[int]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        raise ValueError("candidate_dish_ids 必须是数组")
    return [int(item) for item in value]


def _parse_int_id_list(value) -> list[int]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        parts = [item.strip() for item in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        parts = list(value)
    else:
        raise ValueError("ID 列表格式无效")

    result: list[int] = []
    for item in parts:
        if item in (None, ""):
            continue
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            raise ValueError("ID 列表格式无效")
    return result


def _safe_unlink_local_file(path: str | None) -> bool:
    if not path:
        return False
    normalized = str(path).strip()
    if not normalized or normalized.startswith(("http://", "https://", "/images/")):
        return False
    if not os.path.isfile(normalized):
        return False
    try:
        os.unlink(normalized)
        return True
    except OSError as e:
        logger.warning("Failed to delete captured image file %s: %s", normalized, e)
        return False


def _delete_captured_images(image_ids: list[int]) -> dict:
    normalized_ids = list(dict.fromkeys(int(image_id) for image_id in image_ids if int(image_id) > 0))
    if not normalized_ids:
        return {
            "requested_count": 0,
            "deleted_count": 0,
            "missing_ids": [],
            "deleted_file_count": 0,
        }

    images = CapturedImage.query.filter(CapturedImage.id.in_(normalized_ids)).all()
    image_by_id = {image.id: image for image in images}
    missing_ids = [image_id for image_id in normalized_ids if image_id not in image_by_id]
    file_paths: list[str] = []

    for image in images:
        file_paths.append(image.image_path)
        regions = CapturedImageRegion.query.filter_by(image_id=image.id).all()
        for region in regions:
            file_paths.append(region.image_path)
            db.session.delete(region)

        DishRecognition.query.filter_by(image_id=image.id).delete(synchronize_session=False)
        MatchResult.query.filter_by(image_id=image.id).delete(synchronize_session=False)
        db.session.delete(image)

    db.session.commit()

    deleted_file_count = 0
    for path in dict.fromkeys(file_paths):
        if _safe_unlink_local_file(path):
            deleted_file_count += 1

    return {
        "requested_count": len(normalized_ids),
        "deleted_count": len(images),
        "missing_ids": missing_ids,
        "deleted_file_count": deleted_file_count,
    }


def _parse_pipeline_bboxes(value) -> list[dict[str, int]]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        raise ValueError("bboxes 必须是数组")
    parsed = []
    for item in value:
        parsed.append({
            "x1": int(round(float(item["x1"]))),
            "y1": int(round(float(item["y1"]))),
            "x2": int(round(float(item["x2"]))),
            "y2": int(round(float(item["y2"]))),
        })
    return parsed


def _resolve_video_storage_path() -> str:
    try:
        runtime_source = VideoSourceManager(current_app.config).get_active_runtime_source()
        source_config = runtime_source.get("config") or {}
        return str(source_config.get("local_storage_path") or "/data/nvr_cache")
    except VideoSourceConfigError:
        return "/data/nvr_cache"


def _resolve_pipeline_input():
    cleanup = False
    captured_image = None

    if request.content_type and request.content_type.startswith("multipart/form-data"):
        payload = request.form.to_dict(flat=True)
        image_id = payload.get("image_id")
        image_path = payload.get("image_path")
        has_file = "image_file" in request.files and bool(request.files["image_file"].filename)
    else:
        payload = request.get_json(silent=True) or {}
        image_id = payload.get("image_id")
        image_path = payload.get("image_path")
        has_file = False

    source_count = int(bool(image_id)) + int(bool(image_path)) + int(has_file)
    if source_count != 1:
        raise ValueError("image_id、image_path、image_file 三者必须且只能提供一个")

    resolved_path = None
    if image_id:
        captured_image = CapturedImage.query.get_or_404(int(image_id))
        resolved_path = captured_image.image_path
    elif image_path:
        resolved_path = str(image_path).strip()
    else:
        file_storage = request.files["image_file"]
        suffix = os.path.splitext(file_storage.filename or "")[1] or ".jpg"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            file_storage.save(tmp.name)
            resolved_path = tmp.name
            cleanup = True

    if not resolved_path:
        raise ValueError("图片路径不存在")
    if not os.path.exists(resolved_path):
        raise FileNotFoundError("图片文件不存在")
    return payload, resolved_path, cleanup, captured_image


def _build_candidate_dishes_for_pipeline(
    *,
    captured_image: CapturedImage | None,
    candidate_dish_ids: list[int],
) -> list[dict]:
    def _ordered_active_dishes(dish_ids: list[int]) -> list[Dish]:
        if not dish_ids:
            return []
        dishes = Dish.query.filter(
            Dish.id.in_(dish_ids),
            Dish.is_active.is_(True),
        ).all()
        dish_by_id = {dish.id: dish for dish in dishes}
        return [dish_by_id[dish_id] for dish_id in dish_ids if dish_id in dish_by_id]

    if candidate_dish_ids:
        dishes = _ordered_active_dishes(candidate_dish_ids)
    elif captured_image:
        cfg = get_effective_config(current_app.config)
        menu_scope = normalize_recognition_menu_scope(
            cfg.get("RECOGNITION_MENU_SCOPE", "all"),
        )
        if menu_scope == RECOGNITION_MENU_SCOPE_ALL:
            dishes = Dish.query.filter_by(is_active=True).all()
            return [
                {"id": dish.id, "name": dish.name, "description": dish.description or ""}
                for dish in dishes
            ]

        menu = DailyMenu.query.filter_by(menu_date=captured_image.capture_date).first()
        if not is_menu_configured(menu, cfg):
            raise ValueError(menu_not_configured_message(captured_image.capture_date))
        if menu:
            meal_slot = resolve_meal_slot_for_datetime(
                captured_image.captured_at,
                timezone_name=cfg.get("VIDEO_TIMEZONE")
                or cfg.get("APP_TIMEZONE", "Asia/Shanghai"),
                config=cfg,
            )
            menu_scope = normalize_recognition_menu_scope(
                cfg.get("RECOGNITION_MENU_SCOPE", "all"),
            )
            dishes = _ordered_active_dishes(menu.dish_ids_for_recognition(meal_slot, menu_scope, config=cfg))
        else:
            dishes = Dish.query.filter_by(is_active=True).all()
    else:
        dishes = Dish.query.filter_by(is_active=True).all()

    return [
        {"id": dish.id, "name": dish.name, "description": dish.description or ""}
        for dish in dishes
    ]


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
    if normalized_prompt:
        raise ValueError("当前检测服务不支持自定义提示词，请留空后重试")
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
    menu = DailyMenu.query.filter_by(menu_date=capture_date).first()
    if _requires_configured_menu_for_recognition() and not is_menu_configured(menu, get_effective_config(current_app.config)):
        _record_menu_not_configured_alert("manual_upload", capture_date)
        return api_error(menu_not_configured_message(capture_date))

    # Save uploaded file
    storage_path = _resolve_video_storage_path()
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
    task_log = TaskLog(
        task_type="manual_upload",
        task_date=capture_date,
        status="pending",
        meta={
            "channel_id": channel_id,
            "source_video": safe_filename,
            "source_video_path": video_path,
            "status_text": "视频已上传，等待后台提取图片",
        },
    )
    db.session.add(task_log)
    db.session.commit()

    try:
        from app.tasks.video import process_manual_video_upload

        image_path = current_app.config.get("IMAGE_STORAGE_PATH", "/data/images")
        output_dir = os.path.join(image_path, str(capture_date), channel_id)
        process_manual_video_upload.delay(
            task_log.id,
            video_path,
            output_dir,
            video_start_time.isoformat(),
            channel_id,
            safe_filename,
        )
        task_log.meta = {
            **(task_log.meta or {}),
            "output_dir": output_dir,
            "status_text": "视频已上传，后台任务已提交",
        }
        db.session.commit()

        logger.info("Manual video upload queued: %s task=%s", safe_filename, task_log.id)

        return api_ok({
            "message": "视频上传成功，后台正在提取图片",
            "task_id": task_log.id,
            "video_filename": safe_filename,
            "capture_date": str(capture_date),
        })

    except Exception as e:
        logger.error(f"Manual video upload failed: {e}", exc_info=True)
        task_log.status = "failed"
        task_log.error_message = str(e)
        task_log.finished_at = datetime.utcnow()
        task_log.meta = {
            **(task_log.meta or {}),
            "status_text": "视频处理失败",
        }
        db.session.commit()
        return api_error(f"视频处理失败: {str(e)}")


@bp.route("/tasks", methods=["GET"])
@login_required
def list_tasks():
    from app.tasks.video import _mark_stale_active_sync_tasks

    _mark_stale_active_sync_tasks()
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
    from app.tasks.video import _mark_stale_active_sync_tasks

    _mark_stale_active_sync_tasks()
    task = TaskLog.query.get_or_404(task_id)
    return api_ok(task.to_dict())


@bp.route("/tasks/<int:task_id>/retry", methods=["POST"])
@role_required("admin")
def retry_task(task_id):
    task = TaskLog.query.get_or_404(task_id)
    if task.status not in ("failed", "partial"):
        return api_error("只能重试失败或部分完成的任务")

    if task.task_type in ("video_source_sync", "nvr_download"):
        from app.tasks.video import has_active_sync_task, retry_failed_video_recording_jobs, sync_video_source_media

        if has_active_sync_task():
            return api_error("当前已有视频同步任务在执行，请等待完成后再重试")
        menu = DailyMenu.query.filter_by(menu_date=task.task_date).first()
        if _requires_configured_menu_for_recognition() and not is_menu_configured(menu, get_effective_config(current_app.config)):
            _record_menu_not_configured_alert(task.task_type, task.task_date)
            return api_error(menu_not_configured_message(task.task_date))
        retried_count = retry_failed_video_recording_jobs(task.id)
        if retried_count <= 0:
            sync_video_source_media.delay(task.task_date.isoformat())
    elif task.task_type == "ai_recognition":
        from app.tasks.recognition import run_recognition_batch
        menu = DailyMenu.query.filter_by(menu_date=task.task_date).first()
        if _requires_configured_menu_for_recognition() and not is_menu_configured(menu, get_effective_config(current_app.config)):
            _record_menu_not_configured_alert(task.task_type, task.task_date)
            return api_error(menu_not_configured_message(task.task_date))
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


@bp.route("/tasks/<int:task_id>/cancel", methods=["POST"])
@role_required("admin")
def cancel_task(task_id):
    task = TaskLog.query.get_or_404(task_id)
    if task.task_type not in ("video_source_sync", "nvr_download"):
        return api_error("当前任务类型不支持手动结束")
    if task.status not in ("pending", "running"):
        return api_error("只能结束待处理或运行中的任务")

    from app.tasks.video import mark_sync_task_failed

    mark_sync_task_failed(task, "任务已由管理员手动结束")
    db.session.commit()
    return api_ok(task.to_dict(), "任务已结束")


@bp.route("/tasks/trigger", methods=["POST"])
@role_required("admin")
def trigger_analysis():
    """Manually trigger video source synchronization for a date."""
    data = request.get_json() or {}
    from app.tasks.video import _resolve_target_date, has_active_sync_task, sync_video_source_media

    target_date = _resolve_target_date(current_app.config, data.get("date"))
    menu = DailyMenu.query.filter_by(menu_date=target_date).first()
    if _requires_configured_menu_for_recognition() and not is_menu_configured(menu, get_effective_config(current_app.config)):
        _record_menu_not_configured_alert("video_source_sync", target_date)
        return api_error(menu_not_configured_message(target_date))
    date_str = target_date.isoformat()
    if has_active_sync_task():
        return api_error("当前已有视频同步任务在执行，请等待完成后再触发")
    sync_video_source_media.delay(date_str)
    return api_ok({"message": f"已触发 {date_str} 的视频分析任务"})


@bp.route("/tasks/rerun-recognition", methods=["POST"])
@role_required("admin")
def rerun_recognition():
    """Re-run AI recognition for every non-candidate image on a given date."""
    data = request.get_json() or {}
    from app.tasks.video import _resolve_target_date

    target_date = _resolve_target_date(current_app.config, data.get("date"))
    menu = DailyMenu.query.filter_by(menu_date=target_date).first()
    if _requires_configured_menu_for_recognition() and not is_menu_configured(menu, get_effective_config(current_app.config)):
        _record_menu_not_configured_alert("ai_recognition", target_date)
        return api_error(menu_not_configured_message(target_date))

    active = TaskLog.query.filter(
        TaskLog.task_type == "ai_recognition",
        TaskLog.task_date == target_date,
        TaskLog.status == "running",
    ).first()
    if active:
        return api_error("当日已有正在运行的 AI 识别任务，请等待完成后再重新识别")

    date_str = target_date.isoformat()
    from app.tasks.recognition import run_recognition_batch

    run_recognition_batch.delay(date_str, force_rerun=True)
    return api_ok({"message": f"已触发 {date_str} 的全量重新识别"})


@bp.route("/images", methods=["GET"])
@login_required
def list_images():
    q = CapturedImage.query.order_by(CapturedImage.captured_at.desc())
    if image_ids_param := request.args.get("image_ids"):
        try:
            image_ids = _parse_int_id_list(image_ids_param)
        except ValueError as e:
            return api_error(str(e))
        if not image_ids:
            return api_ok(paginated_response([], 0, 1, 1))
        q = q.filter(CapturedImage.id.in_(image_ids))
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
    if source_video := request.args.get("source_video"):
        q = q.filter(CapturedImage.source_video == source_video)
    candidate_filter = request.args.get("is_candidate")
    if candidate_filter is not None:
        normalized_candidate_filter = str(candidate_filter).strip().lower()
        if normalized_candidate_filter in ("1", "true", "yes"):
            q = q.filter(CapturedImage.is_candidate.is_(True))
        elif normalized_candidate_filter in ("0", "false", "no"):
            q = q.filter(CapturedImage.is_candidate.is_(False))
        else:
            return api_error("候选帧筛选参数无效")
    else:
        include_candidates = str(request.args.get("include_candidates", "true")).strip().lower()
        if include_candidates not in ("1", "true", "yes", "all"):
            q = q.filter(CapturedImage.is_candidate.is_(False))

    items, total, page, page_size = paginate(q)
    match_summaries = _build_image_match_summaries([img.id for img in items])

    result = []
    for img in items:
        d = img.to_dict()
        d["match_summary"] = match_summaries.get(img.id, {
            "is_matched": False,
            "match_count": 0,
            "statuses": [],
            "latest_status": None,
            "latest_match_id": None,
        })
        # Include recognition results
        recs = DishRecognition.query.filter_by(image_id=img.id).all()
        _attach_image_recognition_data(d, recs)
        result.append(d)

    return api_ok(paginated_response(result, total, page, page_size))


@bp.route("/images/<int:image_id>", methods=["GET"])
@login_required
def get_image(image_id):
    img = CapturedImage.query.get_or_404(image_id)
    data = img.to_dict()
    data["match_summary"] = _build_image_match_summaries([image_id]).get(image_id, {
        "is_matched": False,
        "match_count": 0,
        "statuses": [],
        "latest_status": None,
        "latest_match_id": None,
    })
    recs = DishRecognition.query.filter_by(image_id=image_id).all()
    _attach_image_recognition_data(data, recs)
    return api_ok(data)


@bp.route("/images/<int:image_id>", methods=["DELETE"])
@role_required("admin")
def delete_image(image_id):
    img = CapturedImage.query.get_or_404(image_id)
    result = _delete_captured_images([img.id])
    return api_ok(result, "采集图片已删除")


@bp.route("/images", methods=["DELETE"])
@role_required("admin")
def delete_images():
    data = request.get_json(silent=True) or {}
    raw_ids = data.get("image_ids", request.args.get("image_ids"))
    try:
        image_ids = _parse_int_id_list(raw_ids)
    except ValueError as e:
        return api_error(str(e))
    if not image_ids:
        return api_error("请选择要删除的采集图片")
    if len(image_ids) > 500:
        return api_error("一次最多删除 500 张采集图片")

    result = _delete_captured_images(image_ids)
    return api_ok(result, f"已删除 {result['deleted_count']} 张采集图片")


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
    img.recognition_finished_at = datetime.now(timezone.utc)
    img.recognition_lease_expires_at = None
    img.recognition_error_code = None
    img.recognition_error_message = None
    db.session.commit()

    # Re-trigger matching
    from app.tasks.matching import match_single_image
    match_single_image.delay(image_id)

    data = img.to_dict()
    recs = DishRecognition.query.filter_by(image_id=image_id).all()
    _attach_image_recognition_data(data, recs)
    return api_ok(data)


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
    if prompt:
        return api_error("当前检测服务不支持自定义提示词，请留空后重试")

    try:
        task_log = _enqueue_region_proposal_task(img, prompt=prompt)
    except Exception as e:
        logger.error("Failed to enqueue region proposal task for captured image %s: %s", image_id, e, exc_info=True)
        return api_error(f"提交菜区提议任务失败: {str(e)}"), 500

    return api_ok({
        "image_id": img.id,
        "task": task_log.to_dict(),
    }, message="菜区提议任务已提交"), 202


@bp.route("/pipeline", methods=["POST"])
@role_required("admin")
def pipeline():
    cleanup = False
    image_path = None
    try:
        payload, image_path, cleanup, captured_image = _resolve_pipeline_input()
        mode = str(payload.get("mode") or "full").strip().lower()
        if mode not in {"detect", "embed", "full"}:
            return api_error("mode 仅支持 detect、embed、full")

        detector_client = make_detector_client(current_app.config)
        retrieval_client = make_retrieval_client(current_app.config)

        if mode == "detect":
            detector_result = detector_client.post_file(
                "/v1/detect",
                image_path=image_path,
                data={
                    "conf_threshold": payload.get("conf_threshold"),
                    "iou_threshold": payload.get("iou_threshold"),
                    "max_regions": payload.get("max_regions"),
                },
            )
            return api_ok({
                "mode": mode,
                "source": "image_id" if captured_image else ("image_path" if payload.get("image_path") else "upload"),
                **detector_result,
            })

        if mode == "embed":
            retrieval_result = retrieval_client.post_file(
                "/v1/embed",
                image_path=image_path,
                data={
                    "bboxes": _parse_pipeline_bboxes(payload.get("bboxes")),
                    "instruction": payload.get("instruction"),
                },
            )
            return api_ok({
                "mode": mode,
                "source": "image_id" if captured_image else ("image_path" if payload.get("image_path") else "upload"),
                **retrieval_result,
            })

        candidate_dish_ids = _parse_candidate_dish_ids(payload.get("candidate_dish_ids"))
        candidate_dishes = _build_candidate_dishes_for_pipeline(
            captured_image=captured_image,
            candidate_dish_ids=candidate_dish_ids,
        )
        detector_backend = "full_image"
        regions = []
        try:
            detector_result = detector_client.post_file(
                "/v1/detect",
                image_path=image_path,
                data={
                    "conf_threshold": payload.get("conf_threshold"),
                    "iou_threshold": payload.get("iou_threshold"),
                    "max_regions": payload.get("max_regions"),
                },
            )
            regions = detector_result.get("regions", [])
            detector_backend = detector_result.get("backend") if regions else "full_image"
        except InferenceServiceError as e:
            logger.warning("Pipeline detector unavailable, fallback to full-image retrieval: %s", e)

        retrieval_result = retrieval_client.post_file(
            "/v1/full",
            image_path=image_path,
            data={
                "candidate_dishes": candidate_dishes,
                **({"regions": [region.get("bbox") for region in regions]} if regions else {}),
            },
        )
        return api_ok({
            "mode": mode,
            "source": "image_id" if captured_image else ("image_path" if payload.get("image_path") else "upload"),
            "detector_backend": detector_backend,
            "regions": regions,
            **retrieval_result,
        })
    except (ValueError, FileNotFoundError) as e:
        return api_error(str(e))
    except InferenceServiceError as e:
        return api_error(str(e), getattr(e, "status_code", 502))
    finally:
        if cleanup and image_path and os.path.exists(image_path):
            try:
                os.unlink(image_path)
            except OSError:
                pass


@bp.route("/images/<int:image_id>/recognize", methods=["POST"])
@role_required("admin")
def recognize_image(image_id):
    """Trigger AI recognition for a single image."""
    img = CapturedImage.query.get_or_404(image_id)

    if img.status not in (
        ImageStatusEnum.pending,
        ImageStatusEnum.error,
        ImageStatusEnum.identified,
        ImageStatusEnum.matched,
        ImageStatusEnum.invalid,
    ):
        return api_error("当前图片状态不支持重新识别")

    has_manual_review = DishRecognition.query.filter_by(
        image_id=image_id,
        is_manual=True,
    ).first()
    if has_manual_review:
        return api_error("该图片已有人工复核结果，不能重新发起 AI 识别")

    menu = DailyMenu.query.filter_by(menu_date=img.capture_date).first()
    if _requires_configured_menu_for_recognition() and not is_menu_configured(menu, get_effective_config(current_app.config)):
        _record_menu_not_configured_alert("ai_recognition", img.capture_date)
        return api_error(menu_not_configured_message(img.capture_date))

    from app.tasks.recognition import recognize_single_image

    # Allow candidate frames to be recognized on demand, even though batch recognition skips them.
    DishRecognition.query.filter_by(image_id=image_id, is_manual=False).delete()
    img.status = ImageStatusEnum.pending
    img.recognition_task_id = None
    img.recognition_task_log_id = None
    img.recognition_started_at = None
    img.recognition_finished_at = None
    img.recognition_lease_expires_at = None
    img.recognition_error_code = None
    img.recognition_error_message = None
    db.session.commit()

    recognize_single_image.delay(image_id)

    data = img.to_dict()
    recs = DishRecognition.query.filter_by(image_id=image_id).all()
    _attach_image_recognition_data(data, recs)
    return api_ok(data)


def _parse_region_enum(enum_cls, value: str | None, field_name: str):
    if value in (None, ""):
        return None
    try:
        return enum_cls(value)
    except ValueError:
        allowed = ", ".join(item.value for item in enum_cls)
        raise ValueError(f"{field_name} 无效，可选：{allowed}")


def _parse_region_meal_slot(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    if value not in get_meal_slot_keys(current_app.config):
        allowed = ", ".join(get_meal_slot_keys(current_app.config))
        raise ValueError(f"meal_slot 无效，可选：{allowed}")
    return value


def _paginate_loaded_items(items: list):
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(100, max(1, int(request.args.get("page_size", 20))))
    except (ValueError, TypeError):
        page, page_size = 1, 20

    total = len(items)
    start = (page - 1) * page_size
    return items[start:start + page_size], total, page, page_size


@bp.route("/regions", methods=["GET"])
@login_required
def list_image_regions():
    try:
        recognition_status = _parse_region_enum(
            RegionRecognitionStatusEnum,
            request.args.get("recognition_status"),
            "recognition_status",
        )
        review_status = _parse_region_enum(
            RegionReviewStatusEnum,
            request.args.get("review_status"),
            "review_status",
        )
        meal_slot = _parse_region_meal_slot(request.args.get("meal_slot"))
    except ValueError as e:
        return api_error(str(e))

    q = CapturedImageRegion.query.join(CapturedImage).order_by(
        CapturedImage.captured_at.desc(),
        CapturedImageRegion.region_index.asc(),
    )
    if date_str := request.args.get("date"):
        try:
            q = q.filter(CapturedImage.capture_date == date.fromisoformat(date_str))
        except ValueError:
            return api_error("日期格式无效")
    if image_id := request.args.get("image_id"):
        try:
            q = q.filter(CapturedImageRegion.image_id == int(image_id))
        except (TypeError, ValueError):
            return api_error("image_id 格式无效")
    if recognition_status:
        q = q.filter(CapturedImageRegion.recognition_status == recognition_status)
    if review_status:
        q = q.filter(CapturedImageRegion.review_status == review_status)
    if suggested_dish_id := request.args.get("suggested_dish_id"):
        try:
            q = q.filter(CapturedImageRegion.suggested_dish_id == int(suggested_dish_id))
        except (TypeError, ValueError):
            return api_error("suggested_dish_id 格式无效")

    if meal_slot:
        timezone_name = current_app.config.get("VIDEO_TIMEZONE") or current_app.config.get("APP_TIMEZONE", "Asia/Shanghai")
        effective_cfg = get_effective_config(current_app.config)
        loaded_items = [
            item for item in q.all()
            if item.image and resolve_meal_slot_for_datetime(
                item.image.captured_at,
                timezone_name=timezone_name,
                config=effective_cfg,
            ) == meal_slot
        ]
        items, total, page, page_size = _paginate_loaded_items(loaded_items)
    else:
        items, total, page, page_size = paginate(q)

    return api_ok(paginated_response(
        [item.to_dict(include_source_image=True) for item in items],
        total,
        page,
        page_size,
    ))


@bp.route("/regions/<int:region_id>/bind", methods=["POST"])
@role_required("admin")
def bind_image_region(region_id):
    region = CapturedImageRegion.query.get_or_404(region_id)
    data = request.get_json() or {}
    try:
        dish_id = int(data.get("dish_id") or region.suggested_dish_id or 0)
    except (TypeError, ValueError):
        dish_id = 0
    if not dish_id:
        return api_error("请选择要关联的菜品")

    dish = Dish.query.get(dish_id)
    if not dish or not dish.is_active:
        return api_error("目标菜品不存在或已停用")

    try:
        sample_image = bind_region_candidate(region, dish)
        db.session.commit()
    except ValueError as e:
        db.session.rollback()
        return api_error(str(e))
    except Exception as e:
        db.session.rollback()
        logger.error("Failed to bind region candidate %s to dish %s: %s", region_id, dish_id, e, exc_info=True)
        return api_error(f"绑定候选图失败: {str(e)}"), 500

    rebuild_triggered = False
    try:
        rebuild_triggered = trigger_local_embedding_rebuild(
            current_app.config,
            reason="region candidate bind",
        )
    except Exception as e:
        logger.warning("Failed to trigger local embedding rebuild after region bind: %s", e)

    return api_ok({
        "message": "候选图已保存为菜品样图" + ("，并已提交 embedding 重建任务" if rebuild_triggered else ""),
        "region": region.to_dict(include_source_image=True),
        "dish": dish.to_dict(),
        "sample_image": sample_image.to_dict(),
    })


@bp.route("/regions/<int:region_id>/ignore", methods=["POST"])
@role_required("admin")
def ignore_image_region(region_id):
    region = CapturedImageRegion.query.get_or_404(region_id)
    if region.review_status == RegionReviewStatusEnum.bound:
        return api_error("已绑定候选图不能忽略")
    region.review_status = RegionReviewStatusEnum.ignored
    db.session.commit()
    return api_ok({"region": region.to_dict(include_source_image=True)})


@bp.route("/regions/batch-bind", methods=["POST"])
@role_required("admin")
def batch_bind_image_regions():
    data = request.get_json() or {}
    region_ids = data.get("region_ids") or []
    if not isinstance(region_ids, list) or not region_ids:
        return api_error("region_ids 必须是非空数组")

    success = []
    errors = []
    for raw_id in region_ids:
        try:
            region_id = int(raw_id)
        except (TypeError, ValueError):
            errors.append({"region_id": raw_id, "message": "ID 格式无效"})
            continue

        region = CapturedImageRegion.query.get(region_id)
        if not region:
            errors.append({"region_id": region_id, "message": "候选图不存在"})
            continue
        if region.review_status != RegionReviewStatusEnum.pending:
            errors.append({"region_id": region_id, "message": "候选图不是待处理状态"})
            continue
        if not region.suggested_dish_id:
            errors.append({"region_id": region_id, "message": "候选图没有建议菜品"})
            continue

        dish = Dish.query.get(region.suggested_dish_id)
        if not dish or not dish.is_active:
            errors.append({"region_id": region_id, "message": "建议菜品不存在或已停用"})
            continue

        try:
            sample_image = bind_region_candidate(region, dish)
            db.session.commit()
            success.append({
                "region_id": region.id,
                "dish_id": dish.id,
                "sample_image_id": sample_image.id,
            })
        except Exception as e:
            db.session.rollback()
            errors.append({"region_id": region_id, "message": str(e)})

    rebuild_triggered = False
    if success:
        try:
            rebuild_triggered = trigger_local_embedding_rebuild(
                current_app.config,
                reason="region candidate batch bind",
            )
        except Exception as e:
            logger.warning("Failed to trigger local embedding rebuild after region batch bind: %s", e)

    return api_ok({
        "success_count": len(success),
        "error_count": len(errors),
        "success": success,
        "errors": errors,
        "rebuild_triggered": rebuild_triggered,
    })


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
            "QWEN_TEMPERATURE": current_app.config.get("QWEN_TEMPERATURE", 0.1),
        })

        result = qwen.describe_dishes(img.image_path)
        return api_ok({
            "description": result.get("description", ""),
            "structured_description": result.get("structured_description", {}),
            "notes": result.get("notes", ""),
            "descriptions": result.get("descriptions", []),
        })

    except Exception as e:
        logger.error(f"Failed to describe image {image_id}: {e}", exc_info=True)
        return api_error(f"生成描述失败: {str(e)}")


@bp.route("/summary", methods=["GET"])
@login_required
def get_daily_summary():
    """Get analysis summary for a date or date range."""
    start_date_str = request.args.get("start_date")
    end_date_str = request.args.get("end_date")
    date_str = request.args.get("date", date.today().isoformat())

    try:
        if start_date_str or end_date_str:
            if not start_date_str or not end_date_str:
                return api_error("开始日期和结束日期必须同时提供")
            start_date = date.fromisoformat(start_date_str)
            end_date = date.fromisoformat(end_date_str)
        else:
            start_date = date.fromisoformat(date_str)
            end_date = start_date
    except ValueError:
        return api_error("日期格式无效")

    if start_date > end_date:
        return api_error("开始日期不能晚于结束日期")

    date_filters = (
        CapturedImage.capture_date >= start_date,
        CapturedImage.capture_date <= end_date,
    )

    total = CapturedImage.query.filter(*date_filters).count()
    pending = CapturedImage.query.filter(
        *date_filters,
        CapturedImage.status.in_((
            ImageStatusEnum.pending,
            ImageStatusEnum.queued,
            ImageStatusEnum.processing,
            ImageStatusEnum.retry_wait,
        )),
    ).count()
    queued = CapturedImage.query.filter(*date_filters, CapturedImage.status == ImageStatusEnum.queued).count()
    processing = CapturedImage.query.filter(*date_filters, CapturedImage.status == ImageStatusEnum.processing).count()
    retry_wait = CapturedImage.query.filter(*date_filters, CapturedImage.status == ImageStatusEnum.retry_wait).count()
    identified = CapturedImage.query.filter(*date_filters, CapturedImage.status == ImageStatusEnum.identified).count()
    matched = CapturedImage.query.filter(*date_filters, CapturedImage.status == ImageStatusEnum.matched).count()
    invalid = CapturedImage.query.filter(*date_filters, CapturedImage.status == ImageStatusEnum.invalid).count()
    error = CapturedImage.query.filter(*date_filters, CapturedImage.status == ImageStatusEnum.error).count()

    low_conf = DishRecognition.query.join(CapturedImage).filter(
        *date_filters,
        DishRecognition.is_low_confidence.is_(True),
    ).count()

    by_channel = _build_channel_breakdown(date_filters)

    return api_ok({
        "date": end_date.isoformat(),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "total_images": total,
        "pending": pending,
        "queued": queued,
        "processing": processing,
        "retry_wait": retry_wait,
        "identified": identified,
        "matched": matched,
        "invalid": invalid,
        "error": error,
        "low_confidence_recognitions": low_conf,
        "by_channel": by_channel,
        **_aggregate_image_analysis_duration(start_date, end_date),
    })
