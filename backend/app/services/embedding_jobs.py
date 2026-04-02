import logging
from typing import Any

from app.services.inference_client import InferenceServiceError, make_retrieval_control_client
from app.services.local_model_manager import is_local_model_ready
from app.services.model_management import is_retrieval_api_model_management
from app.services.recognition_modes import is_local_recognition_mode
from app.services.runtime_config import get_effective_config

logger = logging.getLogger(__name__)


def can_trigger_local_embedding_rebuild(
    config: dict[str, Any],
    *,
    check_remote_ready: bool = True,
) -> tuple[bool, str | None]:
    config = get_effective_config(config)

    if not config.get("LOCAL_REBUILD_SAMPLE_EMBEDDINGS_ON_UPLOAD", True):
        return False, "LOCAL_REBUILD_SAMPLE_EMBEDDINGS_ON_UPLOAD disabled"

    if not is_local_recognition_mode(config.get("DISH_RECOGNITION_MODE", "vl")):
        return False, "DISH_RECOGNITION_MODE is not local embedding mode"

    if is_retrieval_api_model_management(config):
        if not check_remote_ready:
            return True, None
        try:
            remote_status = make_retrieval_control_client(config).get_json("/health/models")
        except InferenceServiceError as e:
            return False, f"retrieval-api unavailable: {str(e)}"
        if not remote_status.get("embedding_model_downloaded"):
            return False, "retrieval-api embedding model is not downloaded yet"
        return True, None

    embedding_model_path = (config.get("LOCAL_QWEN3_VL_EMBEDDING_MODEL_PATH") or "").strip()
    if not embedding_model_path:
        return False, "LOCAL_QWEN3_VL_EMBEDDING_MODEL_PATH is empty"
    if not is_local_model_ready(embedding_model_path):
        return False, "LOCAL_QWEN3_VL_EMBEDDING_MODEL_PATH is not downloaded yet"

    return True, None


def trigger_local_embedding_rebuild(config: dict[str, Any], *, reason: str) -> bool:
    allowed, skip_reason = can_trigger_local_embedding_rebuild(
        config,
        check_remote_ready=False,
    )
    if not allowed:
        logger.info("Skip local embedding rebuild after %s: %s", reason, skip_reason)
        return False

    from app.tasks.embeddings import rebuild_sample_embeddings

    rebuild_sample_embeddings.delay()
    return True
