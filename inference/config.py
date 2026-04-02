import os


class Config:
    INFERENCE_API_TOKEN = os.environ.get("INFERENCE_API_TOKEN", "")
    INFERENCE_API_TIMEOUT = int(os.environ.get("INFERENCE_API_TIMEOUT", "180"))
    INFERENCE_SERVICE_ROLE = os.environ.get("INFERENCE_SERVICE_ROLE", "all")

    HF_ENDPOINT = os.environ.get("HF_ENDPOINT", "")
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
    LOCAL_EMBEDDING_SIMILARITY_THRESHOLD = float(
        os.environ.get("LOCAL_EMBEDDING_SIMILARITY_THRESHOLD", "0.35")
    )
    LOCAL_EMBEDDING_TOPK = int(os.environ.get("LOCAL_EMBEDDING_TOPK", "5"))
    LOCAL_RERANK_TOPN = int(os.environ.get("LOCAL_RERANK_TOPN", "5"))
    LOCAL_RERANK_SCORE_THRESHOLD = float(os.environ.get("LOCAL_RERANK_SCORE_THRESHOLD", "0.5"))

    YOLO_MODEL_PATH = os.environ.get("YOLO_MODEL_PATH", "")
    YOLO_DEVICE = os.environ.get("YOLO_DEVICE", "")
    YOLO_CONF_THRESHOLD = float(os.environ.get("YOLO_CONF_THRESHOLD", "0.25"))
    YOLO_IOU_THRESHOLD = float(os.environ.get("YOLO_IOU_THRESHOLD", "0.45"))
    YOLO_MAX_REGIONS = int(os.environ.get("YOLO_MAX_REGIONS", "6"))


def get_config():
    return Config
