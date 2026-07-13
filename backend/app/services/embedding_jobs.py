import logging
from typing import Any

from flask import has_app_context

from app.services.inference_client import InferenceServiceError, make_retrieval_control_client
from app.services.recognition_modes import is_local_recognition_mode
from app.services.runtime_config import get_effective_config

logger = logging.getLogger(__name__)
SUPPORTED_RETRIEVAL_PIPELINES = {"qwen", "visual"}


def _mark_sample_embedding_indexes_stale() -> None:
    if not has_app_context():
        return

    from app import db
    from app.models import Dish, DishSampleImage, EmbeddingStatusEnum

    active_images = DishSampleImage.query.join(Dish).filter(
        Dish.is_active.is_(True),
        DishSampleImage.is_active.is_(True),
    ).all()
    for image in active_images:
        image.embedding_status = EmbeddingStatusEnum.pending
        image.error_message = None
        image.visual_embedding_status = EmbeddingStatusEnum.pending
        image.visual_error_message = None
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def _normalize_retrieval_pipeline(value: Any, fallback: str = "qwen") -> str:
    pipeline = str(value or "").strip().lower()
    return pipeline if pipeline in SUPPORTED_RETRIEVAL_PIPELINES else fallback


def _resolve_active_retrieval_pipeline(config: dict[str, Any], client) -> str:
    configured_pipeline = _normalize_retrieval_pipeline(
        config.get("LOCAL_RETRIEVAL_PIPELINE", "qwen")
    )
    try:
        remote_status = client.get_json("/health/models")
    except InferenceServiceError as e:
        logger.warning(
            "Failed to resolve active retrieval pipeline; falling back to %s: %s",
            configured_pipeline,
            e,
        )
        return configured_pipeline

    remote_pipeline = _normalize_retrieval_pipeline(
        remote_status.get("retrieval_pipeline"),
        fallback="",
    )
    if remote_pipeline:
        return remote_pipeline

    logger.warning(
        "retrieval-api returned an invalid active pipeline %r; falling back to %s",
        remote_status.get("retrieval_pipeline"),
        configured_pipeline,
    )
    return configured_pipeline


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

    if not check_remote_ready:
        return True, None
    try:
        remote_status = make_retrieval_control_client(config).get_json("/health/models")
    except InferenceServiceError as e:
        return False, f"retrieval-api unavailable: {str(e)}"
    if not remote_status.get("embedding_model_downloaded"):
        return False, "retrieval-api embedding model is not downloaded yet"
    return True, None


def trigger_local_embedding_rebuild(config: dict[str, Any], *, reason: str) -> bool:
    _mark_sample_embedding_indexes_stale()
    allowed, skip_reason = can_trigger_local_embedding_rebuild(
        config,
        check_remote_ready=False,
    )
    if not allowed:
        logger.info("Skip local embedding rebuild after %s: %s", reason, skip_reason)
        return False

    from app.tasks.embeddings import rebuild_sample_embeddings

    effective_config = get_effective_config(config)
    client = make_retrieval_control_client(effective_config)
    pipeline = _resolve_active_retrieval_pipeline(effective_config, client)
    inactive_pipeline = "visual" if pipeline == "qwen" else "qwen"
    try:
        client.post_json(
            "/v1/index/invalidate",
            {"pipeline": inactive_pipeline},
        )
    except InferenceServiceError as e:
        logger.error("Failed to invalidate inactive %s index after %s: %s", inactive_pipeline, reason, e)
    rebuild_sample_embeddings.delay(pipeline)
    return True
