import logging
import os
from flask import Blueprint, request
from app import db
from app.models import User, Student, RoleEnum
from app.services.local_model_manager import (
    EMBEDDING_MODEL_TYPE,
    REGION_PROPOSAL_MODEL_TYPE,
    RERANKER_MODEL_TYPE,
    SAM_MODEL_TYPE,
    MODEL_VARIANTS,
    get_local_model_spec,
    has_model_variants,
    is_local_model_ready,
)
from app.services.runtime_config import get_effective_config, persist_runtime_overrides
from app.utils.jwt_utils import role_required, api_ok, api_error
from app.utils.pagination import paginate, paginated_response

bp = Blueprint("admin", __name__)
logger = logging.getLogger(__name__)


@bp.route("/users", methods=["GET"])
@role_required("admin")
def list_users():
    q = User.query.order_by(User.name)
    if role := request.args.get("role"):
        q = q.filter(User.role == role)
    if request.args.get("active_only") != "false":
        q = q.filter(User.is_active)
    items, total, page, page_size = paginate(q)
    return api_ok(paginated_response([u.to_dict() for u in items], total, page, page_size))


@bp.route("/users/<int:user_id>", methods=["PUT"])
@role_required("admin")
def update_user(user_id):
    user = User.query.get_or_404(user_id)
    data = request.get_json() or {}

    for field in ["role", "managed_class_ids", "managed_grade_ids", "student_ids", "is_active"]:
        if field in data:
            if field == "role":
                try:
                    setattr(user, field, RoleEnum(data[field]))
                except ValueError:
                    return api_error(f"无效角色：{data[field]}")
            else:
                setattr(user, field, data[field])

    db.session.commit()
    return api_ok(user.to_dict())


@bp.route("/students", methods=["GET"])
@role_required("admin", "teacher", "grade_leader")
def list_students():
    q = Student.query.filter_by(is_active=True)
    user = request.current_user

    # Scope filter
    if user.role.value == "teacher":
        class_ids = user.managed_class_ids or []
        q = q.filter(Student.class_id.in_(class_ids))
    elif user.role.value == "grade_leader":
        grade_ids = user.managed_grade_ids or []
        q = q.filter(Student.grade_id.in_(grade_ids))

    if class_id := request.args.get("class_id"):
        q = q.filter(Student.class_id == class_id)
    if grade_id := request.args.get("grade_id"):
        q = q.filter(Student.grade_id == grade_id)
    if search := request.args.get("search"):
        q = q.filter(db.or_(
            Student.name.ilike(f"%{search}%"),
            Student.student_no.ilike(f"%{search}%"),
        ))

    q = q.order_by(Student.class_id, Student.name)
    items, total, page, page_size = paginate(q)
    return api_ok(paginated_response([s.to_dict() for s in items], total, page, page_size))


@bp.route("/students/<int:student_id>", methods=["PUT"])
@role_required("admin")
def update_student(student_id):
    student = Student.query.get_or_404(student_id)
    data = request.get_json() or {}
    for field in ["student_no", "name", "class_id", "class_name", "grade_id", "grade_name", "card_no", "is_active"]:
        if field in data:
            setattr(student, field, data[field])
    db.session.commit()
    return api_ok(student.to_dict())


@bp.route("/config", methods=["GET"])
@role_required("admin")
def get_config():
    from flask import current_app
    cfg = get_effective_config(current_app.config)
    embedding_spec = get_local_model_spec(cfg, EMBEDDING_MODEL_TYPE)
    reranker_spec = get_local_model_spec(cfg, RERANKER_MODEL_TYPE)
    region_proposal_spec = get_local_model_spec(cfg, REGION_PROPOSAL_MODEL_TYPE)
    sam_spec = get_local_model_spec(cfg, SAM_MODEL_TYPE)
    # Only expose safe, non-secret config
    return api_ok({
        "nvr_host": cfg.get("NVR_HOST", ""),
        "nvr_port": cfg.get("NVR_PORT", 8080),
        "nvr_channel_ids": cfg.get("NVR_CHANNEL_IDS", []),
        "nvr_meal_windows": cfg.get("NVR_MEAL_WINDOWS", "[]"),
        "roi_region": cfg.get("ROI_REGION"),
        "roi_polygon": cfg.get("ROI_POLYGON"),
        "video_timezone": cfg.get("VIDEO_TIMEZONE", cfg.get("APP_TIMEZONE", "Asia/Shanghai")),
        "video_analysis_method": cfg.get("VIDEO_ANALYSIS_METHOD", "legacy"),
        "motion_pixel_delta_threshold": cfg.get("MOTION_PIXEL_DELTA_THRESHOLD", 25),
        "motion_ratio_threshold": cfg.get("MOTION_RATIO_THRESHOLD", 0.015),
        "stable_frames_enter": cfg.get("STABLE_FRAMES_ENTER", 8),
        "stable_frames_exit": cfg.get("STABLE_FRAMES_EXIT", 5),
        "bg_history": cfg.get("BG_HISTORY", 500),
        "bg_var_threshold": cfg.get("BG_VAR_THRESHOLD", 16.0),
        "bg_detect_shadows": cfg.get("BG_DETECT_SHADOWS", False),
        "bg_warmup_frames": cfg.get("BG_WARMUP_FRAMES", 500),
        "bg_empty_learning_rate": cfg.get("BG_EMPTY_LEARNING_RATE", 0.002),
        "fg_ratio_threshold": cfg.get("FG_RATIO_THRESHOLD", 0.15),
        "fg_min_component_area": cfg.get("FG_MIN_COMPONENT_AREA", 1500),
        "plate_min_area_ratio": cfg.get("PLATE_MIN_AREA_RATIO", 0.12),
        "plate_max_area_ratio": cfg.get("PLATE_MAX_AREA_RATIO", 0.85),
        "plate_center_max_ratio": cfg.get("PLATE_CENTER_MAX_RATIO", 0.95),
        "plate_edge_touch_max_ratio": cfg.get("PLATE_EDGE_TOUCH_MAX_RATIO", 0.25),
        "quick_stable_frames_min": cfg.get("QUICK_STABLE_FRAMES_MIN", 2),
        "stable_present_frames_min": cfg.get("STABLE_PRESENT_FRAMES_MIN", 1),
        "stable_sample_interval": cfg.get("STABLE_SAMPLE_INTERVAL", 3),
        "blur_kernel_size": cfg.get("BLUR_KERNEL_SIZE", 5),
        "morph_open_kernel": cfg.get("MORPH_OPEN_KERNEL", 3),
        "morph_close_kernel": cfg.get("MORPH_CLOSE_KERNEL", 7),
        "score_clarity_weight": cfg.get("SCORE_CLARITY_WEIGHT", 0.6),
        "score_completeness_weight": cfg.get("SCORE_COMPLETENESS_WEIGHT", 0.4),
        "tray_orange_ratio_threshold": cfg.get("TRAY_ORANGE_RATIO_THRESHOLD", 0.05),
        "tray_center_margin": cfg.get("TRAY_CENTER_MARGIN", 0.15),
        "tray_motion_threshold": cfg.get("TRAY_MOTION_THRESHOLD", 500),
        "tray_window_size": cfg.get("TRAY_WINDOW_SIZE", 20),
        "tray_min_laplacian": cfg.get("TRAY_MIN_LAPLACIAN", 50.0),
        "tray_roi_expand": cfg.get("TRAY_ROI_EXPAND", 0),
        "tray_leave_motion_threshold": cfg.get("TRAY_LEAVE_MOTION_THRESHOLD", 1500),
        "tray_leave_motion_frames": cfg.get("TRAY_LEAVE_MOTION_FRAMES", 6),
        "tray_dedup_threshold": cfg.get("TRAY_DEDUP_THRESHOLD", 0.75),
        "time_offset_tolerance": cfg.get("TIME_OFFSET_TOLERANCE", 1),
        "price_tolerance": cfg.get("PRICE_TOLERANCE", 0.5),
        "qwen_model": cfg.get("QWEN_MODEL", "qwen-vl-max"),
        "dish_recognition_mode": cfg.get("DISH_RECOGNITION_MODE", "local_embedding"),
        "local_recognition_model_version": cfg.get("LOCAL_RECOGNITION_MODEL_VERSION", "grounding_dino+sam+qwen3_vl_embedding"),
        "hf_endpoint": cfg.get("HF_ENDPOINT", ""),
        "local_model_storage_path": cfg.get("LOCAL_MODEL_STORAGE_PATH", "/data/models"),
        "local_runtime_config_path": cfg.get("LOCAL_RUNTIME_CONFIG_PATH", ""),
        "local_model_variants": list(MODEL_VARIANTS),
        "local_qwen3_vl_embedding_active_variant": embedding_spec["active_variant"],
        "local_qwen3_vl_embedding_repo_id": embedding_spec["repo_id"],
        "local_qwen3_vl_embedding_model_path": cfg.get("LOCAL_QWEN3_VL_EMBEDDING_MODEL_PATH", ""),
        "local_qwen3_vl_embedding_model_downloaded": is_local_model_ready(embedding_spec["path"]),
        "local_qwen3_vl_reranker_active_variant": reranker_spec["active_variant"],
        "local_qwen3_vl_reranker_repo_id": reranker_spec["repo_id"],
        "local_qwen3_vl_reranker_model_path": cfg.get("LOCAL_QWEN3_VL_RERANKER_MODEL_PATH", ""),
        "local_qwen3_vl_reranker_model_downloaded": is_local_model_ready(reranker_spec["path"]),
        "local_qwen3_vl_reranker_instruction": cfg.get("LOCAL_QWEN3_VL_RERANKER_INSTRUCTION", ""),
        "local_embedding_index_dir": cfg.get("LOCAL_EMBEDDING_INDEX_DIR", "/data/images/embedding_index"),
        "local_embedding_similarity_threshold": cfg.get("LOCAL_EMBEDDING_SIMILARITY_THRESHOLD", 0.35),
        "local_embedding_topk": cfg.get("LOCAL_EMBEDDING_TOPK", 5),
        "local_rerank_topn": cfg.get("LOCAL_RERANK_TOPN", 5),
        "local_rerank_score_threshold": cfg.get("LOCAL_RERANK_SCORE_THRESHOLD", 0.5),
        "local_rebuild_sample_embeddings_on_upload": cfg.get("LOCAL_REBUILD_SAMPLE_EMBEDDINGS_ON_UPLOAD", True),
        "local_region_proposal_repo_id": region_proposal_spec["repo_id"],
        "local_region_proposal_model_path": cfg.get("LOCAL_REGION_PROPOSAL_MODEL_PATH", ""),
        "local_region_proposal_model_downloaded": is_local_model_ready(region_proposal_spec["path"]),
        "local_region_proposal_device": cfg.get("LOCAL_REGION_PROPOSAL_DEVICE", ""),
        "local_region_proposal_text_prompt": cfg.get("LOCAL_REGION_PROPOSAL_TEXT_PROMPT", ""),
        "local_region_proposal_box_threshold": cfg.get("LOCAL_REGION_PROPOSAL_BOX_THRESHOLD", 0.28),
        "local_region_proposal_text_threshold": cfg.get("LOCAL_REGION_PROPOSAL_TEXT_THRESHOLD", 0.2),
        "local_region_proposal_nms_threshold": cfg.get("LOCAL_REGION_PROPOSAL_NMS_THRESHOLD", 0.55),
        "local_region_proposal_max_regions": cfg.get("LOCAL_REGION_PROPOSAL_MAX_REGIONS", 8),
        "local_sam_model_repo_id": sam_spec["repo_id"],
        "local_sam_model_path": cfg.get("LOCAL_SAM_MODEL_PATH", ""),
        "local_sam_model_downloaded": is_local_model_ready(sam_spec["path"]),
        "local_sam_model_device": cfg.get("LOCAL_SAM_MODEL_DEVICE", ""),
        "qwen_max_qps": cfg.get("QWEN_MAX_QPS", 10),
        "qwen_recognition_system_prompt": cfg.get("QWEN_RECOGNITION_SYSTEM_PROMPT", ""),
        "qwen_recognition_user_prompt_template": cfg.get("QWEN_RECOGNITION_USER_PROMPT_TEMPLATE", ""),
        "qwen_description_system_prompt": cfg.get("QWEN_DESCRIPTION_SYSTEM_PROMPT", ""),
        "qwen_description_user_prompt": cfg.get("QWEN_DESCRIPTION_USER_PROMPT", ""),
        "nutrition_system_prompt": cfg.get("NUTRITION_SYSTEM_PROMPT", ""),
        "nutrition_prompt_template": cfg.get("NUTRITION_PROMPT_TEMPLATE", ""),
    })


@bp.route("/config/local-models/<model_type>/download", methods=["POST"])
@role_required("admin")
def download_local_model(model_type):
    from flask import current_app
    from app.tasks.local_models import download_local_model as download_local_model_task

    if model_type not in {EMBEDDING_MODEL_TYPE, RERANKER_MODEL_TYPE, REGION_PROPOSAL_MODEL_TYPE, SAM_MODEL_TYPE}:
        return api_error("不支持的模型类型")

    data = request.get_json() or {}
    variant = data.get("variant") if has_model_variants(model_type) else None
    try:
        spec = get_local_model_spec(get_effective_config(current_app.config), model_type, variant=variant)
    except ValueError as e:
        return api_error(str(e))

    target_path = spec["path"]
    if not target_path:
        return api_error("模型下载路径未配置")

    parent_dir = os.path.dirname(target_path) or "."
    try:
        os.makedirs(parent_dir, exist_ok=True)
    except OSError as e:
        logger.error("Failed to create local model parent dir %s: %s", parent_dir, e, exc_info=True)
        return api_error(f"无法创建模型目录: {parent_dir}")

    download_local_model_task.delay(model_type, spec["variant"] or None)
    return api_ok({
        "message": f"已提交 {spec['label']}" + (f" {spec['variant']}" if spec["variant"] else "") + " 模型下载任务",
        "model_type": model_type,
        "variant": spec["variant"],
        "repo_id": spec["repo_id"],
        "target_path": target_path,
    })


@bp.route("/config/local-models/<model_type>/activate", methods=["POST"])
@role_required("admin")
def activate_local_model(model_type):
    from flask import current_app

    if model_type not in {EMBEDDING_MODEL_TYPE, RERANKER_MODEL_TYPE, REGION_PROPOSAL_MODEL_TYPE, SAM_MODEL_TYPE}:
        return api_error("不支持的模型类型")

    data = request.get_json() or {}
    variant = data.get("variant") if has_model_variants(model_type) else None
    try:
        effective_config = get_effective_config(current_app.config)
        spec = get_local_model_spec(effective_config, model_type, variant=variant)
    except ValueError as e:
        return api_error(str(e))

    if not is_local_model_ready(spec["path"]):
        return api_error(f"{spec['label']}" + (f" {spec['variant']}" if spec["variant"] else "") + " 模型尚未下载完成")

    if model_type == EMBEDDING_MODEL_TYPE:
        updates = {
            "LOCAL_QWEN3_VL_EMBEDDING_REPO_ID": spec["repo_id"],
            "LOCAL_QWEN3_VL_EMBEDDING_MODEL_PATH": spec["path"],
        }
    elif model_type == RERANKER_MODEL_TYPE:
        updates = {
            "LOCAL_QWEN3_VL_RERANKER_REPO_ID": spec["repo_id"],
            "LOCAL_QWEN3_VL_RERANKER_MODEL_PATH": spec["path"],
        }
    elif model_type == REGION_PROPOSAL_MODEL_TYPE:
        updates = {
            "LOCAL_REGION_PROPOSAL_REPO_ID": spec["repo_id"],
            "LOCAL_REGION_PROPOSAL_MODEL_PATH": spec["path"],
        }
    else:
        updates = {
            "LOCAL_SAM_MODEL_REPO_ID": spec["repo_id"],
            "LOCAL_SAM_MODEL_PATH": spec["path"],
        }

    runtime_config_path = persist_runtime_overrides(current_app.config, updates)
    current_app.config.update(updates)
    current_app.config["LOCAL_RUNTIME_CONFIG_PATH"] = runtime_config_path

    return api_ok({
        "message": f"已切换当前 {spec['label']}" + (f" 模型到 {spec['variant']}" if spec["variant"] else " 模型"),
        "model_type": model_type,
        "variant": spec["variant"],
        "repo_id": spec["repo_id"],
        "target_path": spec["path"],
        "runtime_config_path": runtime_config_path,
    })
