import json
import os
from datetime import timedelta
from urllib.parse import quote
from prompt_defaults import (
    NUTRITION_PROMPT_TEMPLATE as DEFAULT_NUTRITION_PROMPT_TEMPLATE,
    NUTRITION_SYSTEM_PROMPT as DEFAULT_NUTRITION_SYSTEM_PROMPT,
    QWEN_DESCRIPTION_SYSTEM_PROMPT as DEFAULT_QWEN_DESCRIPTION_SYSTEM_PROMPT,
    QWEN_DESCRIPTION_USER_PROMPT as DEFAULT_QWEN_DESCRIPTION_USER_PROMPT,
    QWEN_RECOGNITION_SYSTEM_PROMPT as DEFAULT_QWEN_RECOGNITION_SYSTEM_PROMPT,
    QWEN_RECOGNITION_USER_PROMPT_TEMPLATE as DEFAULT_QWEN_RECOGNITION_USER_PROMPT_TEMPLATE,
)


def _build_postgres_url(
    prefix="POSTGRES",
    default_host="localhost",
    default_port="5432",
    default_db="nutrition_db",
    default_user="nutrition",
):
    scheme = os.environ.get(f"{prefix}_SCHEME", "postgresql")
    host = os.environ.get(f"{prefix}_HOST", default_host)
    port = os.environ.get(f"{prefix}_PORT", default_port)
    db = os.environ.get(f"{prefix}_DB", default_db)
    user = os.environ.get(f"{prefix}_USER", default_user)
    password = os.environ.get(f"{prefix}_PASSWORD")

    auth = quote(user, safe="")
    if password is not None:
        auth = f"{auth}:{quote(password, safe='')}"

    return f"{scheme}://{auth}@{host}:{port}/{db}"


def _resolve_database_url(fallback=None):
    explicit_url = os.environ.get("DATABASE_URL")
    has_parts = any(
        os.environ.get(f"POSTGRES_{key}") is not None
        for key in ("HOST", "PORT", "DB", "USER", "PASSWORD", "SCHEME")
    )
    if has_parts:
        return _build_postgres_url()
    if explicit_url:
        return explicit_url
    return fallback or _build_postgres_url()


def _build_redis_url(prefix="REDIS", default_host="localhost", default_port="6379", default_db="0"):
    scheme = os.environ.get(f"{prefix}_SCHEME", "redis")
    host = os.environ.get(f"{prefix}_HOST", default_host)
    port = os.environ.get(f"{prefix}_PORT", default_port)
    db = os.environ.get(f"{prefix}_DB", default_db)
    username = os.environ.get(f"{prefix}_USERNAME", "")
    password = os.environ.get(f"{prefix}_PASSWORD")

    auth = ""
    if password is not None:
        encoded_password = quote(password, safe="")
        if username:
            auth = f"{quote(username, safe='')}:{encoded_password}@"
        else:
            auth = f":{encoded_password}@"
    elif username:
        auth = f"{quote(username, safe='')}@"

    return f"{scheme}://{auth}{host}:{port}/{db}"


def _resolve_redis_url(prefix="REDIS", fallback=None):
    explicit_url = os.environ.get(f"{prefix}_URL")
    has_parts = any(
        os.environ.get(f"{prefix}_{key}") is not None
        for key in ("HOST", "PORT", "DB", "USERNAME", "PASSWORD", "SCHEME")
    )
    if has_parts:
        return _build_redis_url(prefix=prefix)
    if explicit_url:
        return explicit_url
    return fallback or _build_redis_url(prefix=prefix)


def _load_json_env(name: str, default):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def _load_bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _load_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _load_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _load_ztk_env(name: str, default: str = "") -> str:
    return os.environ.get(f"ZTK_{name}", os.environ.get(f"ZYTK_{name}", default))


DEFAULT_VIDEO_SYNC_MEAL_WINDOWS = [
    {"start": "07:00", "end": "09:00"},
    {"start": "11:30", "end": "13:00"},
    {"start": "17:30", "end": "19:00"},
]

# Unified meal slot configuration. Replaces the separate video-sync windows,
# menu meal slots, and menu reminder times.
DEFAULT_MEAL_SLOTS = [
    {"key": "breakfast", "label": "早餐", "start": "05:00", "end": "09:30"},
    {"key": "lunch", "label": "午餐", "start": "10:30", "end": "13:30"},
    {"key": "dinner", "label": "晚餐", "start": "17:00", "end": "19:30"},
    {"key": "late_night", "label": "宵夜", "start": "21:00", "end": "23:59"},
]


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")
    SQLALCHEMY_DATABASE_URI = _resolve_database_url("postgresql://nutrition:nutrition@localhost:5432/nutrition_db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
        "pool_size": 10,
        "max_overflow": 20,
    }

    # Zhengyuan all-in-one card PLUS database sync
    ZTK_DB_HOST = _load_ztk_env("DB_HOST")
    ZTK_DB_PORT = _load_int_env("ZTK_DB_PORT", _load_int_env("ZYTK_DB_PORT", 1433))
    ZTK_DB_NAME = _load_ztk_env("DB_NAME", "ZYTK40_PLUS")
    ZTK_DB_USER = _load_ztk_env("DB_USER")
    ZTK_DB_PASSWORD = _load_ztk_env("DB_PASSWORD")
    ZTK_PAYMENT_BOOKS_TABLE = _load_ztk_env("PAYMENT_BOOKS_TABLE", "dbo.view_ac_paymentbooks")
    ZTK_ACCOUNTS_TABLE = _load_ztk_env("ACCOUNTS_TABLE", "dbo.ac_dict_Accounts")
    ZTK_SYNC_ENABLED = _load_bool_env("ZTK_SYNC_ENABLED", _load_bool_env("ZYTK_SYNC_ENABLED", False))
    ZTK_SYNC_INTERVAL_MINUTES = max(
        1,
        _load_int_env("ZTK_SYNC_INTERVAL_MINUTES", _load_int_env("ZYTK_SYNC_INTERVAL_MINUTES", 5)),
    )
    ZTK_SYNC_LOOKBACK_MINUTES = max(
        0,
        _load_int_env("ZTK_SYNC_LOOKBACK_MINUTES", _load_int_env("ZYTK_SYNC_LOOKBACK_MINUTES", 5)),
    )
    ZTK_SYNC_PAGE_SIZE = max(
        1,
        _load_int_env("ZTK_SYNC_PAGE_SIZE", _load_int_env("ZYTK_SYNC_PAGE_SIZE", 1000)),
    )
    # Per-run import cap. Each run commits its batch and advances the cursor, so
    # a generous cap lets one sync (manual trigger or beat tick) pull essentially
    # every record newer than the cursor instead of dribbling in 1000 at a time.
    # Bounded so a single run still finishes well inside the Celery soft time
    # limit; if more rows exist, the next run continues from the new cursor.
    ZTK_SYNC_MAX_ROWS_PER_RUN = max(
        1,
        _load_int_env("ZTK_SYNC_MAX_ROWS_PER_RUN", _load_int_env("ZYTK_SYNC_MAX_ROWS_PER_RUN", 50000)),
    )

    # Redis
    REDIS_URL = _resolve_redis_url("REDIS", "redis://localhost:6379/0")
    CELERY_BROKER_URL = _resolve_redis_url("CELERY_BROKER", REDIS_URL)
    CELERY_RESULT_BACKEND = _resolve_redis_url("CELERY_RESULT_BACKEND", REDIS_URL)

    # JWT
    JWT_ALGORITHM = "HS256"
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=7)
    JWT_REFRESH_WINDOW = timedelta(days=1)  # refresh in last 1 day

    # DingTalk
    DINGTALK_APP_KEY = os.environ.get("DINGTALK_APP_KEY", "")
    DINGTALK_APP_SECRET = os.environ.get("DINGTALK_APP_SECRET", "")
    DINGTALK_AGENT_ID = os.environ.get("DINGTALK_AGENT_ID", "")
    DINGTALK_CORP_ID = os.environ.get("DINGTALK_CORP_ID", "")
    DINGTALK_WEBHOOK_TOKEN = os.environ.get("DINGTALK_WEBHOOK_TOKEN", "")
    MENU_REMINDER_DINGTALK_MODE = os.environ.get("MENU_REMINDER_DINGTALK_MODE", "app").strip().lower()
    MENU_REMINDER_DINGTALK_WEBHOOK_URL = os.environ.get("MENU_REMINDER_DINGTALK_WEBHOOK_URL", "").strip()
    MENU_REMINDER_DINGTALK_WEBHOOK_PREFIX = os.environ.get(
        "MENU_REMINDER_DINGTALK_WEBHOOK_PREFIX", "[营养监测系统提醒]"
    ).strip()

    # Student synchronization provider. ``dingtalk`` keeps the standard
    # integration; ``rest_student_list`` enables a deployment-specific flat
    # roster API without coupling its schema to the student domain service.
    STUDENT_SYNC_PROVIDER = os.environ.get("STUDENT_SYNC_PROVIDER", "dingtalk").strip().lower()
    STUDENT_SYNC_REST_URL = os.environ.get("STUDENT_SYNC_REST_URL", "").strip()
    STUDENT_SYNC_REST_API_KEY = os.environ.get("STUDENT_SYNC_REST_API_KEY", "").strip()
    STUDENT_SYNC_REST_HTTP_METHOD = os.environ.get("STUDENT_SYNC_REST_HTTP_METHOD", "GET").strip().upper()
    STUDENT_SYNC_REST_TIMEOUT_SECONDS = max(1, _load_int_env("STUDENT_SYNC_REST_TIMEOUT_SECONDS", 15))
    STUDENT_SYNC_SCHOOL_NAME = os.environ.get("STUDENT_SYNC_SCHOOL_NAME", "默认学校").strip()
    STUDENT_SYNC_CAMPUS_NAME = os.environ.get("STUDENT_SYNC_CAMPUS_NAME", "默认校区").strip()
    STUDENT_SYNC_STAGE_NAME = os.environ.get("STUDENT_SYNC_STAGE_NAME", "默认学段").strip()
    STUDENT_SYNC_DEACTIVATE_MISSING = _load_bool_env("STUDENT_SYNC_DEACTIVATE_MISSING", False)

    # Frontend
    FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")

    # Qwen3-VL (multimodal for image recognition)
    QWEN_API_KEY = os.environ.get("QWEN_API_KEY", "")
    QWEN_API_URL = os.environ.get(
        "QWEN_API_URL",
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
    )
    QWEN_MODEL = os.environ.get("QWEN_MODEL", "qwen-vl-max")
    QWEN_TIMEOUT = _load_int_env("QWEN_TIMEOUT", 30)
    QWEN_MAX_QPS = _load_int_env("QWEN_MAX_QPS", 10)
    QWEN_TEMPERATURE = _load_float_env("QWEN_TEMPERATURE", 0.1)
    QWEN_RECOGNITION_SYSTEM_PROMPT = os.environ.get(
        "QWEN_RECOGNITION_SYSTEM_PROMPT",
        DEFAULT_QWEN_RECOGNITION_SYSTEM_PROMPT,
    )
    QWEN_RECOGNITION_USER_PROMPT_TEMPLATE = os.environ.get(
        "QWEN_RECOGNITION_USER_PROMPT_TEMPLATE",
        DEFAULT_QWEN_RECOGNITION_USER_PROMPT_TEMPLATE,
    )
    QWEN_DESCRIPTION_SYSTEM_PROMPT = os.environ.get(
        "QWEN_DESCRIPTION_SYSTEM_PROMPT",
        DEFAULT_QWEN_DESCRIPTION_SYSTEM_PROMPT,
    )
    QWEN_DESCRIPTION_USER_PROMPT = os.environ.get(
        "QWEN_DESCRIPTION_USER_PROMPT",
        DEFAULT_QWEN_DESCRIPTION_USER_PROMPT,
    )
    DISH_RECOGNITION_MODE = os.environ.get("DISH_RECOGNITION_MODE", "local_embedding")
    RECOGNITION_MENU_SCOPE = os.environ.get("RECOGNITION_MENU_SCOPE", "all")
    LOCAL_RECOGNITION_MODEL_VERSION = os.environ.get(
        "LOCAL_RECOGNITION_MODEL_VERSION",
        "qwen3_vl_embedding",
    )
    HF_ENDPOINT = os.environ.get("HF_ENDPOINT", "").strip()
    LOCAL_MODEL_STORAGE_PATH = os.environ.get("LOCAL_MODEL_STORAGE_PATH", "/data/models")
    LOCAL_RUNTIME_CONFIG_PATH = os.environ.get(
        "LOCAL_RUNTIME_CONFIG_PATH",
        os.path.join(LOCAL_MODEL_STORAGE_PATH, "runtime_config.json"),
    )
    LOCAL_QWEN3_VL_EMBEDDING_REPO_ID = os.environ.get(
        "LOCAL_QWEN3_VL_EMBEDDING_REPO_ID",
        "Qwen/Qwen3-VL-Embedding-2B",
    )
    LOCAL_QWEN3_VL_RERANKER_REPO_ID = os.environ.get(
        "LOCAL_QWEN3_VL_RERANKER_REPO_ID",
        "Qwen/Qwen3-VL-Reranker-2B",
    )
    LOCAL_SIGLIP2_REPO_ID = os.environ.get("LOCAL_SIGLIP2_REPO_ID", "google/siglip2-so400m-patch16-512")
    LOCAL_DINOV3_REPO_ID = os.environ.get("LOCAL_DINOV3_REPO_ID", "timm/vit_base_patch16_dinov3.lvd1689m")
    LOCAL_SIGLIP2_MODEL_PATH = os.environ.get("LOCAL_SIGLIP2_MODEL_PATH", os.path.join(LOCAL_MODEL_STORAGE_PATH, "siglip2-so400m-patch16-512"))
    LOCAL_DINOV3_MODEL_PATH = os.environ.get("LOCAL_DINOV3_MODEL_PATH", os.path.join(LOCAL_MODEL_STORAGE_PATH, "vit-base-patch16-dinov3-lvd1689m"))
    LOCAL_RETRIEVAL_PIPELINE = os.environ.get("LOCAL_RETRIEVAL_PIPELINE", "qwen")
    LOCAL_QWEN3_VL_EMBEDDING_MODEL_PATH = os.environ.get(
        "LOCAL_QWEN3_VL_EMBEDDING_MODEL_PATH",
        os.path.join(LOCAL_MODEL_STORAGE_PATH, "qwen3-vl-embedding-2b"),
    )
    LOCAL_QWEN3_VL_EMBEDDING_PRECISION = os.environ.get(
        "LOCAL_QWEN3_VL_EMBEDDING_PRECISION", "auto"
    )
    LOCAL_QWEN3_VL_RERANKER_MODEL_PATH = os.environ.get(
        "LOCAL_QWEN3_VL_RERANKER_MODEL_PATH",
        os.path.join(LOCAL_MODEL_STORAGE_PATH, "qwen3-vl-reranker-2b"),
    )
    LOCAL_QWEN3_VL_RERANKER_PRECISION = os.environ.get(
        "LOCAL_QWEN3_VL_RERANKER_PRECISION", "auto"
    )
    LOCAL_QWEN3_VL_EMBEDDING_INSTRUCTION = os.environ.get(
        "LOCAL_QWEN3_VL_EMBEDDING_INSTRUCTION",
        "",
    )
    LOCAL_QWEN3_VL_RERANKER_INSTRUCTION = os.environ.get(
        "LOCAL_QWEN3_VL_RERANKER_INSTRUCTION",
        "检索与当前餐盘菜区最相关的食堂菜品图片。",
    )
    LOCAL_EMBEDDING_INDEX_DIR = os.environ.get("LOCAL_EMBEDDING_INDEX_DIR", "/data/images/embedding_index")
    LOCAL_EMBEDDING_SIMILARITY_THRESHOLD = _load_float_env(
        "LOCAL_EMBEDDING_SIMILARITY_THRESHOLD", 0.35
    )
    LOCAL_EMBEDDING_TOPK = _load_int_env("LOCAL_EMBEDDING_TOPK", 5)
    LOCAL_EMBEDDING_MAX_PIXELS = _load_int_env("LOCAL_EMBEDDING_MAX_PIXELS", 786432)
    LOCAL_EMBEDDING_CROP_PADDING_RATIO = max(
        0.0,
        min(0.5, _load_float_env("LOCAL_EMBEDDING_CROP_PADDING_RATIO", 0.06)),
    )
    LOCAL_RERANK_TOPN = _load_int_env("LOCAL_RERANK_TOPN", 3)
    LOCAL_RERANK_SCORE_THRESHOLD = _load_float_env("LOCAL_RERANK_SCORE_THRESHOLD", 0.5)
    VISUAL_RECALL_TOPK = _load_int_env("VISUAL_RECALL_TOPK", 50)
    VISUAL_PATCH_TOPN = _load_int_env("VISUAL_PATCH_TOPN", 10)
    VISUAL_PATCH_MAX_TOKENS = _load_int_env("VISUAL_PATCH_MAX_TOKENS", 256)
    VISUAL_MAXSIM_THRESHOLD = _load_float_env("VISUAL_MAXSIM_THRESHOLD", 0.5)
    LOCAL_REBUILD_SAMPLE_EMBEDDINGS_ON_UPLOAD = os.environ.get(
        "LOCAL_REBUILD_SAMPLE_EMBEDDINGS_ON_UPLOAD",
        "true",
    ).lower() in {"1", "true", "yes"}
    INFERENCE_API_TOKEN = os.environ.get("INFERENCE_API_TOKEN", "")
    INFERENCE_API_TIMEOUT = _load_int_env("INFERENCE_API_TIMEOUT", 60)
    INFERENCE_CONTROL_TIMEOUT = _load_int_env("INFERENCE_CONTROL_TIMEOUT", 3)
    DETECTOR_API_BASE_URL = os.environ.get("DETECTOR_API_BASE_URL", "http://detector-api:5000")
    RETRIEVAL_API_BASE_URL = os.environ.get("RETRIEVAL_API_BASE_URL", "http://retrieval-api:5000")
    INFERENCE_SERVICE_ROLE = os.environ.get("INFERENCE_SERVICE_ROLE", "all")
    YOLO_MODEL_PATH = os.environ.get("YOLO_MODEL_PATH", "")
    YOLO_DEVICE = os.environ.get("YOLO_DEVICE", "")
    YOLO_CONF_THRESHOLD = _load_float_env("YOLO_CONF_THRESHOLD", 0.75)
    YOLO_IOU_THRESHOLD = _load_float_env("YOLO_IOU_THRESHOLD", 0.45)
    YOLO_MAX_REGIONS = _load_int_env("YOLO_MAX_REGIONS", 6)
    MAX_YOLO_MODEL_SIZE = _load_int_env("MAX_YOLO_MODEL_SIZE", 500 * 1024 * 1024)
    # OpenAI-compatible API (for dish nutrition analysis, default to DeepSeek)
    # Supports: DeepSeek, OpenAI, or any OpenAI-compatible API
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", os.environ.get("DEEPSEEK_API_KEY", ""))
    OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "deepseek-chat")
    OPENAI_TIMEOUT = _load_int_env("OPENAI_TIMEOUT", 30)
    NUTRITION_SYSTEM_PROMPT = os.environ.get("NUTRITION_SYSTEM_PROMPT", DEFAULT_NUTRITION_SYSTEM_PROMPT)
    NUTRITION_PROMPT_TEMPLATE = os.environ.get("NUTRITION_PROMPT_TEMPLATE", DEFAULT_NUTRITION_PROMPT_TEMPLATE)

    # Image storage
    IMAGE_STORAGE_PATH = os.environ.get("IMAGE_STORAGE_PATH", "/data/images")
    MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB
    ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png"}
    SAMPLE_IMAGE_MIN_EDGE = max(24, _load_int_env("SAMPLE_IMAGE_MIN_EDGE", 128))
    SAMPLE_IMAGE_MAX_PIXELS = max(
        1,
        _load_int_env("SAMPLE_IMAGE_MAX_PIXELS", 16_777_216),
    )
    SAMPLE_IMAGE_MAX_ASPECT_RATIO = max(
        1.0,
        _load_float_env("SAMPLE_IMAGE_MAX_ASPECT_RATIO", 3.0),
    )
    # ZIP dish import (Excel + per-dish image folders) safety limits
    MAX_IMPORT_ZIP_SIZE = _load_int_env("MAX_IMPORT_ZIP_SIZE", 2 * 1024 * 1024 * 1024)  # 2GB upload (nginx client_max_body_size 2g)
    MAX_ZIP_EXTRACTED_SIZE = _load_int_env("MAX_ZIP_EXTRACTED_SIZE", 4 * 1024 * 1024 * 1024)  # 4GB uncompressed
    MAX_ZIP_ENTRIES = _load_int_env("MAX_ZIP_ENTRIES", 5000)

    # Video analysis defaults
    # ROI for settlement area, e.g. {"x": 220, "y": 170, "w": 840, "h": 430}
    ROI_REGION = _load_json_env("ROI_REGION", None)
    # Deprecated: VIDEO_SYNC_MEAL_WINDOWS is superseded by MEAL_SLOTS.
    # Keep loading it for one release so existing runtime_config.json can be migrated.
    VIDEO_SYNC_MEAL_WINDOWS = _load_json_env(
        "VIDEO_SYNC_MEAL_WINDOWS",
        DEFAULT_VIDEO_SYNC_MEAL_WINDOWS,
    )
    MEAL_SLOTS = _load_json_env("MEAL_SLOTS", DEFAULT_MEAL_SLOTS)
    APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Asia/Shanghai")
    VIDEO_TIMEZONE = os.environ.get("VIDEO_TIMEZONE", APP_TIMEZONE)
    VIDEO_ANALYSIS_MAX_CONCURRENCY = _load_int_env("VIDEO_ANALYSIS_MAX_CONCURRENCY", 2)
    VIDEO_ANALYSIS_MAX_PENDING = _load_int_env("VIDEO_ANALYSIS_MAX_PENDING", 0)
    VIDEO_DISTRIBUTED_PIPELINE = _load_bool_env("VIDEO_DISTRIBUTED_PIPELINE", True)
    VIDEO_RECORDING_JOB_STALE_SECONDS = _load_int_env("VIDEO_RECORDING_JOB_STALE_SECONDS", 7200)
    VIDEO_EXTRACT_USE_SUBPROCESS = os.environ.get("VIDEO_EXTRACT_USE_SUBPROCESS", "true").lower() not in {"0", "false", "no"}
    VIDEO_EXTRACT_PROGRESS_STALL_SECONDS = _load_int_env("VIDEO_EXTRACT_PROGRESS_STALL_SECONDS", 900)
    VIDEO_EXTRACT_MAX_RUNTIME_SECONDS = _load_int_env("VIDEO_EXTRACT_MAX_RUNTIME_SECONDS", 1800)
    VIDEO_EXTRACT_FFMPEG_TIMEOUT_SECONDS = _load_int_env("VIDEO_EXTRACT_FFMPEG_TIMEOUT_SECONDS", 1800)
    VIDEO_EXTRACT_CPU_THREADS_PER_JOB = _load_int_env("VIDEO_EXTRACT_CPU_THREADS_PER_JOB", 1)
    VIDEO_EXTRACT_DECODE_BACKEND = os.environ.get("VIDEO_EXTRACT_DECODE_BACKEND", "opencv").strip().lower()
    VIDEO_EXTRACT_GPU_MAX_CONCURRENCY = _load_int_env("VIDEO_EXTRACT_GPU_MAX_CONCURRENCY", 2)
    VIDEO_EXTRACT_DEGRADE_BEFORE_SYNC_TIMEOUT_SECONDS = _load_int_env(
        "VIDEO_EXTRACT_DEGRADE_BEFORE_SYNC_TIMEOUT_SECONDS",
        1800,
    )
    VIDEO_EXTRACT_FALLBACK_INTERVAL_SECONDS = _load_int_env("VIDEO_EXTRACT_FALLBACK_INTERVAL_SECONDS", 30)
    VIDEO_EXTRACT_FALLBACK_MAX_FRAMES = _load_int_env("VIDEO_EXTRACT_FALLBACK_MAX_FRAMES", 500)
    VIDEO_ANALYSIS_MAX_EVENT_CANDIDATES = _load_int_env("VIDEO_ANALYSIS_MAX_EVENT_CANDIDATES", 120)
    VIDEO_ANALYSIS_MAX_SCAN_HISTORY = _load_int_env("VIDEO_ANALYSIS_MAX_SCAN_HISTORY", 10000)
    VIDEO_ANALYSIS_QUALITY_MAX_DIMENSION = _load_int_env("VIDEO_ANALYSIS_QUALITY_MAX_DIMENSION", 320)
    VIDEO_ANALYSIS_CANDIDATE_FPS = _load_float_env("VIDEO_ANALYSIS_CANDIDATE_FPS", 4.0)
    RECOGNITION_AUTO_CANDIDATE_FALLBACK = _load_bool_env("RECOGNITION_AUTO_CANDIDATE_FALLBACK", True)
    VIDEO_EXTRACT_MIN_DECODE_COMPLETION_RATIO = _load_float_env("VIDEO_EXTRACT_MIN_DECODE_COMPLETION_RATIO", 0.5)
    VIDEO_SYNC_TASK_SOFT_TIME_LIMIT = _load_int_env("VIDEO_SYNC_TASK_SOFT_TIME_LIMIT", 43200)
    VIDEO_SYNC_TASK_TIME_LIMIT = max(
        VIDEO_SYNC_TASK_SOFT_TIME_LIMIT + 300,
        _load_int_env("VIDEO_SYNC_TASK_TIME_LIMIT", 43800),
    )
    CELERY_VISIBILITY_TIMEOUT = max(
        VIDEO_SYNC_TASK_TIME_LIMIT + 3600,
        _load_int_env("CELERY_VISIBILITY_TIMEOUT", 86400),
    )
    EVENT_SCAN_FPS = _load_float_env("EVENT_SCAN_FPS", 12.0)
    MOTION_PIXEL_DELTA_THRESHOLD = _load_int_env("MOTION_PIXEL_DELTA_THRESHOLD", 25)
    MOTION_RATIO_THRESHOLD = _load_float_env("MOTION_RATIO_THRESHOLD", 0.015)
    STABLE_FRAMES_ENTER = _load_int_env("STABLE_FRAMES_ENTER", 5)
    STABLE_FRAMES_EXIT = _load_int_env("STABLE_FRAMES_EXIT", 3)
    BG_HISTORY = _load_int_env("BG_HISTORY", 500)
    BG_VAR_THRESHOLD = _load_float_env("BG_VAR_THRESHOLD", 16)
    BG_DETECT_SHADOWS = os.environ.get("BG_DETECT_SHADOWS", "").lower() in {"1", "true", "yes"}
    BG_WARMUP_FRAMES = _load_int_env("BG_WARMUP_FRAMES", 500)
    BG_EMPTY_LEARNING_RATE = _load_float_env("BG_EMPTY_LEARNING_RATE", 0.002)
    FG_RATIO_THRESHOLD = _load_float_env("FG_RATIO_THRESHOLD", 0.10)
    FG_MIN_COMPONENT_AREA = _load_int_env("FG_MIN_COMPONENT_AREA", 1500)
    PLATE_MIN_AREA_RATIO = _load_float_env("PLATE_MIN_AREA_RATIO", 0.12)
    PLATE_MAX_AREA_RATIO = _load_float_env("PLATE_MAX_AREA_RATIO", 0.85)
    PLATE_CENTER_MAX_RATIO = _load_float_env("PLATE_CENTER_MAX_RATIO", 0.95)
    PLATE_EDGE_TOUCH_MAX_RATIO = _load_float_env("PLATE_EDGE_TOUCH_MAX_RATIO", 0.25)
    QUICK_STABLE_FRAMES_MIN = _load_int_env("QUICK_STABLE_FRAMES_MIN", 2)
    STABLE_PRESENT_FRAMES_MIN = _load_int_env("STABLE_PRESENT_FRAMES_MIN", 1)
    STABLE_SAMPLE_INTERVAL = _load_int_env("STABLE_SAMPLE_INTERVAL", 3)
    BLUR_KERNEL_SIZE = _load_int_env("BLUR_KERNEL_SIZE", 5)
    MORPH_OPEN_KERNEL = _load_int_env("MORPH_OPEN_KERNEL", 3)
    MORPH_CLOSE_KERNEL = _load_int_env("MORPH_CLOSE_KERNEL", 7)
    SCORE_CLARITY_WEIGHT = _load_float_env("SCORE_CLARITY_WEIGHT", 0.6)
    SCORE_COMPLETENESS_WEIGHT = _load_float_env("SCORE_COMPLETENESS_WEIGHT", 0.4)
    EVENT_RECORD_FILENAME = os.environ.get("EVENT_RECORD_FILENAME", "event_records.jsonl")
    LEGACY_ANALYSIS_MAX_WIDTH = _load_int_env("LEGACY_ANALYSIS_MAX_WIDTH", 1280)
    LEGACY_ANALYSIS_MAX_HEIGHT = _load_int_env("LEGACY_ANALYSIS_MAX_HEIGHT", 720)
    LEGACY_QUICK_STABLE_FRAMES_MIN = _load_int_env("LEGACY_QUICK_STABLE_FRAMES_MIN", 1)
    LEGACY_MIN_EVENT_GAP_SECONDS = _load_float_env("LEGACY_MIN_EVENT_GAP_SECONDS", 0.8)
    # Post-processing plate filter (filters out images without plates)
    ENABLE_PLATE_FILTER = os.environ.get("ENABLE_PLATE_FILTER", "true").lower() in {"1", "true", "yes"}
    # Compatibility fallbacks for older deployments.
    DIFF_THRESHOLD = MOTION_PIXEL_DELTA_THRESHOLD
    OBJECT_ENTER_RATIO = FG_RATIO_THRESHOLD

    # Matching
    TIME_OFFSET_TOLERANCE = _load_int_env("TIME_OFFSET_TOLERANCE", 1)
    PRICE_TOLERANCE = _load_float_env("PRICE_TOLERANCE", 0.5)
    MATCHING_BATCH_CHUNK_SIZE = max(1, _load_int_env("MATCHING_BATCH_CHUNK_SIZE", 200))
    MATCHING_BATCH_TIME_BUDGET_SECONDS = max(
        1,
        _load_int_env("MATCHING_BATCH_TIME_BUDGET_SECONDS", 240),
    )
    # Calibration offset (seconds, float) added to consumption transaction_time
    # to align it with video captured_at before matching. Corrects systematic
    # clock skew between the POS/一卡通 clock and the NVR/camera clock.
    TIME_OFFSET_CALIBRATION = _load_float_env("TIME_OFFSET_CALIBRATION", 0.0)

    # Report schedule
    WEEKLY_REPORT_SCHEDULE = os.environ.get("WEEKLY_REPORT_SCHEDULE", "30 7 * * 1")  # Mon 7:30
    MONTHLY_REPORT_SCHEDULE = os.environ.get("MONTHLY_REPORT_SCHEDULE", "30 7 1 * *")  # 1st 7:30

    # CORS
    CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*").split(",")

    # Logging
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

    # Alert threshold
    ALERT_NO_EVENT_MINUTES = _load_int_env("ALERT_NO_EVENT_MINUTES", 30)


class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_ECHO = False


class ProductionConfig(Config):
    DEBUG = False
    SQLALCHEMY_ECHO = False


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    CELERY_TASK_ALWAYS_EAGER = True


config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": DevelopmentConfig,
}


def get_config():
    env = os.environ.get("FLASK_ENV", "development")
    return config_map.get(env, DevelopmentConfig)
