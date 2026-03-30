import logging
from typing import Any

from app.services.local_model_manager import is_local_model_ready
from app.services.recognition_modes import is_local_recognition_mode
from app.services.runtime_config import get_effective_config

logger = logging.getLogger(__name__)


def can_trigger_local_embedding_rebuild(config: dict[str, Any]) -> tuple[bool, str | None]:
    config = get_effective_config(config)

    if not config.get("LOCAL_REBUILD_SAMPLE_EMBEDDINGS_ON_UPLOAD", True):
        return False, "LOCAL_REBUILD_SAMPLE_EMBEDDINGS_ON_UPLOAD disabled"

    if not is_local_recognition_mode(config.get("DISH_RECOGNITION_MODE", "vl")):
        return False, "DISH_RECOGNITION_MODE is not local embedding mode"

    embedding_model_path = (config.get("LOCAL_QWEN3_VL_EMBEDDING_MODEL_PATH") or "").strip()
    if not embedding_model_path:
        return False, "LOCAL_QWEN3_VL_EMBEDDING_MODEL_PATH is empty"
    if not is_local_model_ready(embedding_model_path):
        return False, "LOCAL_QWEN3_VL_EMBEDDING_MODEL_PATH is not downloaded yet"

    return True, None


def trigger_local_embedding_rebuild(config: dict[str, Any], *, reason: str) -> bool:
    allowed, skip_reason = can_trigger_local_embedding_rebuild(config)
    if not allowed:
        logger.info("Skip local embedding rebuild after %s: %s", reason, skip_reason)
        return False

    from app.tasks.embeddings import rebuild_sample_embeddings

    rebuild_sample_embeddings.delay()
    return True
