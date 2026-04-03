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

    # Qwen3-VL (multimodal for image recognition)
    QWEN_API_KEY = os.environ.get("QWEN_API_KEY", "")
    QWEN_API_URL = os.environ.get(
        "QWEN_API_URL",
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
    )
    QWEN_MODEL = os.environ.get("QWEN_MODEL", "qwen-vl-max")
    QWEN_TIMEOUT = int(os.environ.get("QWEN_TIMEOUT", "30"))
    QWEN_MAX_QPS = int(os.environ.get("QWEN_MAX_QPS", "10"))
    QWEN_TEMPERATURE = float(os.environ.get("QWEN_TEMPERATURE", "0.1"))
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
    LOCAL_QWEN3_VL_EMBEDDING_MODEL_PATH = os.environ.get(
        "LOCAL_QWEN3_VL_EMBEDDING_MODEL_PATH",
        os.path.join(LOCAL_MODEL_STORAGE_PATH, "qwen3-vl-embedding-2b"),
    )
    LOCAL_QWEN3_VL_RERANKER_MODEL_PATH = os.environ.get(
        "LOCAL_QWEN3_VL_RERANKER_MODEL_PATH",
        os.path.join(LOCAL_MODEL_STORAGE_PATH, "qwen3-vl-reranker-2b"),
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
    LOCAL_EMBEDDING_SIMILARITY_THRESHOLD = float(
        os.environ.get("LOCAL_EMBEDDING_SIMILARITY_THRESHOLD", "0.35")
    )
    LOCAL_EMBEDDING_TOPK = int(os.environ.get("LOCAL_EMBEDDING_TOPK", "5"))
    LOCAL_RERANK_TOPN = int(os.environ.get("LOCAL_RERANK_TOPN", "5"))
    LOCAL_RERANK_SCORE_THRESHOLD = float(os.environ.get("LOCAL_RERANK_SCORE_THRESHOLD", "0.5"))
    LOCAL_REBUILD_SAMPLE_EMBEDDINGS_ON_UPLOAD = os.environ.get(
        "LOCAL_REBUILD_SAMPLE_EMBEDDINGS_ON_UPLOAD",
        "true",
    ).lower() in {"1", "true", "yes"}
    INFERENCE_API_TOKEN = os.environ.get("INFERENCE_API_TOKEN", "")
    INFERENCE_API_TIMEOUT = int(os.environ.get("INFERENCE_API_TIMEOUT", "180"))
    INFERENCE_CONTROL_TIMEOUT = int(os.environ.get("INFERENCE_CONTROL_TIMEOUT", "3"))
    DETECTOR_API_BASE_URL = os.environ.get("DETECTOR_API_BASE_URL", "http://detector-api:5000")
    RETRIEVAL_API_BASE_URL = os.environ.get("RETRIEVAL_API_BASE_URL", "http://retrieval-api:5000")
    INFERENCE_SERVICE_ROLE = os.environ.get("INFERENCE_SERVICE_ROLE", "all")
    YOLO_MODEL_PATH = os.environ.get("YOLO_MODEL_PATH", "")
    YOLO_DEVICE = os.environ.get("YOLO_DEVICE", "")
    YOLO_CONF_THRESHOLD = float(os.environ.get("YOLO_CONF_THRESHOLD", "0.75"))
    YOLO_IOU_THRESHOLD = float(os.environ.get("YOLO_IOU_THRESHOLD", "0.45"))
    YOLO_MAX_REGIONS = int(os.environ.get("YOLO_MAX_REGIONS", "6"))
    # OpenAI-compatible API (for dish nutrition analysis, default to DeepSeek)
    # Supports: DeepSeek, OpenAI, or any OpenAI-compatible API
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", os.environ.get("DEEPSEEK_API_KEY", ""))
    OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "deepseek-chat")
    OPENAI_TIMEOUT = int(os.environ.get("OPENAI_TIMEOUT", "30"))
    NUTRITION_SYSTEM_PROMPT = os.environ.get("NUTRITION_SYSTEM_PROMPT", DEFAULT_NUTRITION_SYSTEM_PROMPT)
    NUTRITION_PROMPT_TEMPLATE = os.environ.get("NUTRITION_PROMPT_TEMPLATE", DEFAULT_NUTRITION_PROMPT_TEMPLATE)

    # Image storage
    IMAGE_STORAGE_PATH = os.environ.get("IMAGE_STORAGE_PATH", "/data/images")
    MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB
    ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png"}

    # Video analysis defaults
    # ROI for settlement area, e.g. {"x": 220, "y": 170, "w": 840, "h": 430}
    ROI_REGION = _load_json_env("ROI_REGION", None)
    ROI_POLYGON = _load_json_env("ROI_POLYGON", None)
    APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Asia/Shanghai")
    VIDEO_TIMEZONE = os.environ.get("VIDEO_TIMEZONE", APP_TIMEZONE)
    VIDEO_ANALYSIS_METHOD = os.environ.get("VIDEO_ANALYSIS_METHOD", "legacy")
    MOTION_PIXEL_DELTA_THRESHOLD = int(os.environ.get("MOTION_PIXEL_DELTA_THRESHOLD", "25"))
    MOTION_RATIO_THRESHOLD = float(os.environ.get("MOTION_RATIO_THRESHOLD", "0.015"))
    STABLE_FRAMES_ENTER = int(os.environ.get("STABLE_FRAMES_ENTER", "8"))
    STABLE_FRAMES_EXIT = int(os.environ.get("STABLE_FRAMES_EXIT", "5"))
    BG_HISTORY = int(os.environ.get("BG_HISTORY", "500"))
    BG_VAR_THRESHOLD = float(os.environ.get("BG_VAR_THRESHOLD", "16"))
    BG_DETECT_SHADOWS = os.environ.get("BG_DETECT_SHADOWS", "").lower() in {"1", "true", "yes"}
    BG_WARMUP_FRAMES = int(os.environ.get("BG_WARMUP_FRAMES", "500"))
    BG_EMPTY_LEARNING_RATE = float(os.environ.get("BG_EMPTY_LEARNING_RATE", "0.002"))
    FG_RATIO_THRESHOLD = float(os.environ.get("FG_RATIO_THRESHOLD", "0.15"))
    FG_MIN_COMPONENT_AREA = int(os.environ.get("FG_MIN_COMPONENT_AREA", "1500"))
    PLATE_MIN_AREA_RATIO = float(os.environ.get("PLATE_MIN_AREA_RATIO", "0.12"))
    PLATE_MAX_AREA_RATIO = float(os.environ.get("PLATE_MAX_AREA_RATIO", "0.85"))
    PLATE_CENTER_MAX_RATIO = float(os.environ.get("PLATE_CENTER_MAX_RATIO", "0.95"))
    PLATE_EDGE_TOUCH_MAX_RATIO = float(os.environ.get("PLATE_EDGE_TOUCH_MAX_RATIO", "0.25"))
    QUICK_STABLE_FRAMES_MIN = int(os.environ.get("QUICK_STABLE_FRAMES_MIN", "2"))
    STABLE_PRESENT_FRAMES_MIN = int(os.environ.get("STABLE_PRESENT_FRAMES_MIN", "1"))
    STABLE_SAMPLE_INTERVAL = int(os.environ.get("STABLE_SAMPLE_INTERVAL", "3"))
    BLUR_KERNEL_SIZE = int(os.environ.get("BLUR_KERNEL_SIZE", "5"))
    MORPH_OPEN_KERNEL = int(os.environ.get("MORPH_OPEN_KERNEL", "3"))
    MORPH_CLOSE_KERNEL = int(os.environ.get("MORPH_CLOSE_KERNEL", "7"))
    SCORE_CLARITY_WEIGHT = float(os.environ.get("SCORE_CLARITY_WEIGHT", "0.6"))
    SCORE_COMPLETENESS_WEIGHT = float(os.environ.get("SCORE_COMPLETENESS_WEIGHT", "0.4"))
    EVENT_RECORD_FILENAME = os.environ.get("EVENT_RECORD_FILENAME", "event_records.jsonl")
    TRAY_ORANGE_RATIO_THRESHOLD = float(os.environ.get("TRAY_ORANGE_RATIO_THRESHOLD", "0.05"))
    TRAY_CENTER_MARGIN = float(os.environ.get("TRAY_CENTER_MARGIN", "0.15"))
    TRAY_MOTION_THRESHOLD = int(os.environ.get("TRAY_MOTION_THRESHOLD", "500"))
    TRAY_WINDOW_SIZE = int(os.environ.get("TRAY_WINDOW_SIZE", "20"))
    TRAY_MIN_LAPLACIAN = float(os.environ.get("TRAY_MIN_LAPLACIAN", "50"))
    TRAY_ROI_EXPAND = int(os.environ.get("TRAY_ROI_EXPAND", "0"))
    TRAY_LEAVE_MOTION_THRESHOLD = int(os.environ.get("TRAY_LEAVE_MOTION_THRESHOLD", "1500"))
    TRAY_LEAVE_MOTION_FRAMES = int(os.environ.get("TRAY_LEAVE_MOTION_FRAMES", "6"))
    TRAY_DEDUP_THRESHOLD = float(os.environ.get("TRAY_DEDUP_THRESHOLD", "0.75"))
    # Post-processing plate filter (filters out images without plates)
    ENABLE_PLATE_FILTER = os.environ.get("ENABLE_PLATE_FILTER", "true").lower() in {"1", "true", "yes"}
    # Compatibility fallbacks for older deployments.
    DIFF_THRESHOLD = MOTION_PIXEL_DELTA_THRESHOLD
    OBJECT_ENTER_RATIO = FG_RATIO_THRESHOLD

    # Matching
    TIME_OFFSET_TOLERANCE = int(os.environ.get("TIME_OFFSET_TOLERANCE", "1"))
    PRICE_TOLERANCE = float(os.environ.get("PRICE_TOLERANCE", "0.5"))

    # Report schedule
    WEEKLY_REPORT_SCHEDULE = os.environ.get("WEEKLY_REPORT_SCHEDULE", "30 7 * * 1")  # Mon 7:30
    MONTHLY_REPORT_SCHEDULE = os.environ.get("MONTHLY_REPORT_SCHEDULE", "30 7 1 * *")  # 1st 7:30

    # CORS
    CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*").split(",")

    # Logging
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

    # Alert threshold
    ALERT_NO_EVENT_MINUTES = int(os.environ.get("ALERT_NO_EVENT_MINUTES", "30"))


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
