import os
from typing import Any

EMBEDDING_MODEL_TYPE = "embedding"
RERANKER_MODEL_TYPE = "reranker"
SIGLIP2_MODEL_TYPE = "siglip2"
DINOV3_MODEL_TYPE = "dinov3"

VARIANT_MODEL_TYPES = {EMBEDDING_MODEL_TYPE, RERANKER_MODEL_TYPE}
MODEL_VARIANTS = ("2B", "8B")
SUPPORTED_MODEL_TYPES = {
    EMBEDDING_MODEL_TYPE,
    RERANKER_MODEL_TYPE,
    SIGLIP2_MODEL_TYPE,
    DINOV3_MODEL_TYPE,
}

_MODEL_SPECS = {
    EMBEDDING_MODEL_TYPE: {
        "label": "embedding",
        "repo_env": "LOCAL_QWEN3_VL_EMBEDDING_REPO_ID",
        "path_env": "LOCAL_QWEN3_VL_EMBEDDING_MODEL_PATH",
        "default_repo_id": "Qwen/Qwen3-VL-Embedding-2B",
        "path_template": "qwen3-vl-embedding-{variant_lower}",
    },
    RERANKER_MODEL_TYPE: {
        "label": "reranker",
        "repo_env": "LOCAL_QWEN3_VL_RERANKER_REPO_ID",
        "path_env": "LOCAL_QWEN3_VL_RERANKER_MODEL_PATH",
        "default_repo_id": "Qwen/Qwen3-VL-Reranker-2B",
        "path_template": "qwen3-vl-reranker-{variant_lower}",
    },
    SIGLIP2_MODEL_TYPE: {
        "label": "SigLIP2",
        "repo_env": "LOCAL_SIGLIP2_REPO_ID",
        "path_env": "LOCAL_SIGLIP2_MODEL_PATH",
        "default_repo_id": "google/siglip2-so400m-patch16-512",
        "path_template": "siglip2-so400m-patch16-512",
    },
    DINOV3_MODEL_TYPE: {
        "label": "DINOv3",
        "repo_env": "LOCAL_DINOV3_REPO_ID",
        "path_env": "LOCAL_DINOV3_MODEL_PATH",
        "default_repo_id": "timm/vit_base_patch16_dinov3.lvd1689m",
        "path_template": "vit-base-patch16-dinov3-lvd1689m",
    },
}


def _normalize_variant(variant: str | None) -> str:
    normalized = (variant or "2B").strip().upper()
    if normalized not in MODEL_VARIANTS:
        raise ValueError(f"Unsupported model variant: {variant}")
    return normalized


def has_model_variants(model_type: str) -> bool:
    return model_type in VARIANT_MODEL_TYPES


def _build_repo_id(model_type: str, variant: str | None) -> str:
    if model_type == EMBEDDING_MODEL_TYPE:
        return f"Qwen/Qwen3-VL-Embedding-{_normalize_variant(variant)}"
    if model_type == RERANKER_MODEL_TYPE:
        return f"Qwen/Qwen3-VL-Reranker-{_normalize_variant(variant)}"
    raise ValueError(f"Unsupported model_type: {model_type}")


def _build_target_path(storage_root: str, model_type: str, variant: str | None) -> str:
    spec = _MODEL_SPECS.get(model_type)
    if spec is None:
        raise ValueError(f"Unsupported model_type: {model_type}")
    template = spec["path_template"]
    return os.path.join(
        storage_root,
        template.format(variant_lower=(variant or "").lower(), variant=(variant or "")),
    )


def get_local_model_spec(config: dict[str, Any], model_type: str, variant: str | None = None) -> dict[str, str]:
    if model_type not in SUPPORTED_MODEL_TYPES:
        raise ValueError(f"Unsupported model_type: {model_type}")

    storage_root = config.get("LOCAL_MODEL_STORAGE_PATH", "/data/models")
    spec = _MODEL_SPECS[model_type]
    active_repo_id = str(config.get(spec["repo_env"], spec["default_repo_id"]) or spec["default_repo_id"]).strip()
    active_path = str(config.get(spec["path_env"], "") or "").strip()

    if has_model_variants(model_type):
        normalized_variant = _normalize_variant(variant)
        active_variant = _normalize_variant(active_repo_id.rsplit("-", 1)[-1])
        return {
            "model_type": model_type,
            "variant": normalized_variant,
            "active_variant": active_variant,
            "repo_id": _build_repo_id(model_type, normalized_variant),
            "path": active_path if normalized_variant == active_variant else _build_target_path(storage_root, model_type, normalized_variant),
            "label": spec["label"],
            "repo_env": spec["repo_env"],
            "path_env": spec["path_env"],
        }

    resolved_path = active_path or _build_target_path(storage_root, model_type, None)
    return {
        "model_type": model_type,
        "variant": "",
        "active_variant": "",
        "repo_id": active_repo_id or spec["default_repo_id"],
        "path": resolved_path,
        "label": spec["label"],
        "repo_env": spec["repo_env"],
        "path_env": spec["path_env"],
    }


def is_local_model_ready(model_path: str) -> bool:
    if not model_path:
        return False
    if not os.path.isdir(model_path):
        return False
    return os.path.exists(os.path.join(model_path, "config.json"))


def is_managed_model_ready(model_path: str) -> bool:
    """Require the completion marker written after snapshot validation."""
    return is_local_model_ready(model_path) and os.path.isfile(os.path.join(model_path, ".download_complete"))
