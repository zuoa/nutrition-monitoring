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
    EXTRACT_FPS = float(os.environ.get("EXTRACT_FPS", "2"))
    DIFF_THRESHOLD = int(os.environ.get("DIFF_THRESHOLD", "30"))
    MIN_EVENT_DURATION_S = float(os.environ.get("MIN_EVENT_DURATION_S", "0.5"))
    STABLE_FRAME_OFFSET_S = float(os.environ.get("STABLE_FRAME_OFFSET_S", "1.0"))
    MIN_INTERVAL_S = float(os.environ.get("MIN_INTERVAL_S", "3.0"))
    # Plate detection: pixel threshold to determine if plate exists in ROI (vs empty background)
    PLATE_PIXEL_THRESHOLD = int(os.environ.get("PLATE_PIXEL_THRESHOLD", "5000"))

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
