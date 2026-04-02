from typing import Any


LOCAL_MODEL_MANAGEMENT_MODE_LOCAL = "local"
LOCAL_MODEL_MANAGEMENT_MODE_RETRIEVAL_API = "retrieval_api"
SUPPORTED_LOCAL_MODEL_MANAGEMENT_MODES = {
    LOCAL_MODEL_MANAGEMENT_MODE_LOCAL,
    LOCAL_MODEL_MANAGEMENT_MODE_RETRIEVAL_API,
}


def normalize_local_model_management_mode(mode: str | None) -> str:
    normalized = str(mode or "").strip().lower()
    if normalized in SUPPORTED_LOCAL_MODEL_MANAGEMENT_MODES:
        return normalized
    return LOCAL_MODEL_MANAGEMENT_MODE_LOCAL


def is_retrieval_api_model_management(config: dict[str, Any]) -> bool:
    return normalize_local_model_management_mode(
        config.get("LOCAL_MODEL_MANAGEMENT_MODE"),
    ) == LOCAL_MODEL_MANAGEMENT_MODE_RETRIEVAL_API
