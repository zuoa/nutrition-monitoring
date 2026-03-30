import os
from typing import Any

EMBEDDING_MODEL_TYPE = "embedding"
RERANKER_MODEL_TYPE = "reranker"
MODEL_VARIANTS = ("2B", "8B")


def _normalize_variant(variant: str | None) -> str:
    normalized = (variant or "2B").strip().upper()
    if normalized not in MODEL_VARIANTS:
        raise ValueError(f"Unsupported model variant: {variant}")
    return normalized


def _build_repo_id(model_type: str, variant: str) -> str:
    if model_type == EMBEDDING_MODEL_TYPE:
        return f"Qwen/Qwen3-VL-Embedding-{variant}"
    if model_type == RERANKER_MODEL_TYPE:
        return f"Qwen/Qwen3-VL-Reranker-{variant}"
    raise ValueError(f"Unsupported model_type: {model_type}")


def _build_target_path(storage_root: str, model_type: str, variant: str) -> str:
    suffix = "embedding" if model_type == EMBEDDING_MODEL_TYPE else "reranker"
    return os.path.join(storage_root, f"qwen3-vl-{suffix}-{variant.lower()}")


def get_local_model_spec(config: dict[str, Any], model_type: str, variant: str | None = None) -> dict[str, str]:
    normalized_variant = _normalize_variant(variant)
    storage_root = config.get("LOCAL_MODEL_STORAGE_PATH", "/data/models")

    if model_type == EMBEDDING_MODEL_TYPE:
        default_variant = _normalize_variant(
            str(config.get("LOCAL_QWEN3_VL_EMBEDDING_REPO_ID", "Qwen/Qwen3-VL-Embedding-2B")).rsplit("-", 1)[-1]
        )
        return {
            "model_type": model_type,
            "variant": normalized_variant,
            "active_variant": default_variant,
            "repo_id": _build_repo_id(model_type, normalized_variant),
            "path": (
                config.get("LOCAL_QWEN3_VL_EMBEDDING_MODEL_PATH", "")
                if normalized_variant == default_variant
                else _build_target_path(storage_root, model_type, normalized_variant)
            ),
            "label": "embedding",
        }

    if model_type == RERANKER_MODEL_TYPE:
        default_variant = _normalize_variant(
            str(config.get("LOCAL_QWEN3_VL_RERANKER_REPO_ID", "Qwen/Qwen3-VL-Reranker-2B")).rsplit("-", 1)[-1]
        )
        return {
            "model_type": model_type,
            "variant": normalized_variant,
            "active_variant": default_variant,
            "repo_id": _build_repo_id(model_type, normalized_variant),
            "path": (
                config.get("LOCAL_QWEN3_VL_RERANKER_MODEL_PATH", "")
                if normalized_variant == default_variant
                else _build_target_path(storage_root, model_type, normalized_variant)
            ),
            "label": "reranker",
        }

    raise ValueError(f"Unsupported model_type: {model_type}")


def is_local_model_ready(model_path: str) -> bool:
    if not model_path:
        return False
    if not os.path.isdir(model_path):
        return False
    return os.path.exists(os.path.join(model_path, "config.json"))
