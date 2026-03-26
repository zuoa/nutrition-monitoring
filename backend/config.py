import json
import os
from datetime import timedelta
from urllib.parse import quote


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

    # OpenAI-compatible API (for dish nutrition analysis, default to DeepSeek)
    # Supports: DeepSeek, OpenAI, or any OpenAI-compatible API
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", os.environ.get("DEEPSEEK_API_KEY", ""))
    OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "deepseek-chat")
    OPENAI_TIMEOUT = int(os.environ.get("OPENAI_TIMEOUT", "30"))

    # Video source: "nvr" (default) or "hikvision_camera"
    VIDEO_SOURCE_MODE = os.environ.get("VIDEO_SOURCE_MODE", "nvr")
    # Hikvision direct-camera mode: JSON mapping channel_id -> {host, port, username, password}
    # Example: {"1": {"host": "192.168.1.101", "port": 80, "username": "admin", "password": "xxx"}}
    HIKVISION_CAMERAS = os.environ.get("HIKVISION_CAMERAS", "{}")

    # NVR
    NVR_HOST = os.environ.get("NVR_HOST", "")
    NVR_PORT = int(os.environ.get("NVR_PORT", "8080"))
    NVR_USERNAME = os.environ.get("NVR_USERNAME", "")
    NVR_PASSWORD = os.environ.get("NVR_PASSWORD", "")
    NVR_CHANNEL_IDS = os.environ.get("NVR_CHANNEL_IDS", "1").split(",")
    NVR_MEAL_WINDOWS = os.environ.get(
        "NVR_MEAL_WINDOWS",
        '[{"start":"11:30","end":"13:00"},{"start":"17:30","end":"19:00"}]',
    )
    NVR_DOWNLOAD_TRIGGER_TIME = os.environ.get("NVR_DOWNLOAD_TRIGGER_TIME", "21:30")
    NVR_LOCAL_STORAGE_PATH = os.environ.get("NVR_LOCAL_STORAGE_PATH", "/data/nvr_cache")
    NVR_RETENTION_DAYS = int(os.environ.get("NVR_RETENTION_DAYS", "3"))

    # Image storage
    IMAGE_STORAGE_PATH = os.environ.get("IMAGE_STORAGE_PATH", "/data/images")
    MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB
    ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png"}

    # Video analysis defaults
    # ROI for settlement area, e.g. {"x": 220, "y": 170, "w": 840, "h": 430}
    ROI_REGION = _load_json_env("ROI_REGION", None)
    APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Asia/Shanghai")
    VIDEO_TIMEZONE = os.environ.get("VIDEO_TIMEZONE", APP_TIMEZONE)
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
