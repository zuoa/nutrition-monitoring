import os

DEFAULT_HF_ENDPOINT = "https://hf-mirror.com"
DEFAULT_HF_HUB_DISABLE_XET = "1"

os.environ.setdefault("HF_ENDPOINT", DEFAULT_HF_ENDPOINT)
os.environ.setdefault("HF_HUB_DISABLE_XET", DEFAULT_HF_HUB_DISABLE_XET)


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


class Config:
    INFERENCE_API_TOKEN = os.environ.get("INFERENCE_API_TOKEN", "")
    INFERENCE_API_TIMEOUT = _load_int_env("INFERENCE_API_TIMEOUT", 60)
    INFERENCE_SERVICE_ROLE = os.environ.get("INFERENCE_SERVICE_ROLE", "all")

    HF_ENDPOINT = os.environ.get("HF_ENDPOINT", DEFAULT_HF_ENDPOINT).strip()
    HF_HUB_DISABLE_XET = os.environ.get("HF_HUB_DISABLE_XET", DEFAULT_HF_HUB_DISABLE_XET).strip()
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
        "/data/models/qwen3-vl-embedding-2b",
    )
    LOCAL_QWEN3_VL_RERANKER_MODEL_PATH = os.environ.get(
        "LOCAL_QWEN3_VL_RERANKER_MODEL_PATH",
        "/data/models/qwen3-vl-reranker-2b",
    )
    LOCAL_QWEN3_VL_EMBEDDING_INSTRUCTION = os.environ.get(
        "LOCAL_QWEN3_VL_EMBEDDING_INSTRUCTION",
        "",
    )
    LOCAL_QWEN3_VL_RERANKER_INSTRUCTION = os.environ.get(
        "LOCAL_QWEN3_VL_RERANKER_INSTRUCTION",
        "检索与当前餐盘菜区最相关的食堂菜品图片。",
    )
    LOCAL_EMBEDDING_INDEX_DIR = os.environ.get("LOCAL_EMBEDDING_INDEX_DIR", "/data/index")
    LOCAL_EMBEDDING_SIMILARITY_THRESHOLD = _load_float_env(
        "LOCAL_EMBEDDING_SIMILARITY_THRESHOLD", 0.35
    )
    LOCAL_EMBEDDING_TOPK = _load_int_env("LOCAL_EMBEDDING_TOPK", 5)
    LOCAL_EMBEDDING_BATCH_SIZE = _load_int_env("LOCAL_EMBEDDING_BATCH_SIZE", 8)
    LOCAL_RERANK_TOPN = _load_int_env("LOCAL_RERANK_TOPN", 3)
    LOCAL_RERANK_SCORE_THRESHOLD = _load_float_env("LOCAL_RERANK_SCORE_THRESHOLD", 0.5)

    YOLO_MODEL_PATH = os.environ.get("YOLO_MODEL_PATH", "")
    YOLO_DEVICE = os.environ.get("YOLO_DEVICE", "")
    YOLO_CONF_THRESHOLD = _load_float_env("YOLO_CONF_THRESHOLD", 0.75)
    YOLO_IOU_THRESHOLD = _load_float_env("YOLO_IOU_THRESHOLD", 0.45)
    YOLO_MAX_REGIONS = _load_int_env("YOLO_MAX_REGIONS", 6)
    YOLO_CLASS_ID = _load_int_env("YOLO_CLASS_ID", 0)
    MAX_YOLO_MODEL_SIZE = _load_int_env("MAX_YOLO_MODEL_SIZE", 500 * 1024 * 1024)


def get_config():
    return Config
