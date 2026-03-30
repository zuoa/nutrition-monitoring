import os
from typing import Any

EMBEDDING_MODEL_TYPE = "embedding"
RERANKER_MODEL_TYPE = "reranker"
REGION_PROPOSAL_MODEL_TYPE = "region_proposal"
SAM_MODEL_TYPE = "sam"

VARIANT_MODEL_TYPES = {EMBEDDING_MODEL_TYPE, RERANKER_MODEL_TYPE}
MODEL_VARIANTS = ("2B", "8B")
SUPPORTED_MODEL_TYPES = {
    EMBEDDING_MODEL_TYPE,
    RERANKER_MODEL_TYPE,
    REGION_PROPOSAL_MODEL_TYPE,
    SAM_MODEL_TYPE,
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
    REGION_PROPOSAL_MODEL_TYPE: {
        "label": "region proposal",
        "repo_env": "LOCAL_REGION_PROPOSAL_REPO_ID",
        "path_env": "LOCAL_REGION_PROPOSAL_MODEL_PATH",
        "default_repo_id": "IDEA-Research/grounding-dino-tiny",
        "path_template": "grounding-dino-tiny",
    },
    SAM_MODEL_TYPE: {
        "label": "sam",
        "repo_env": "LOCAL_SAM_MODEL_REPO_ID",
        "path_env": "LOCAL_SAM_MODEL_PATH",
        "default_repo_id": "facebook/sam-vit-base",
        "path_template": "sam-vit-base",
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
    if model_type == REGION_PROPOSAL_MODEL_TYPE:
        return _MODEL_SPECS[REGION_PROPOSAL_MODEL_TYPE]["default_repo_id"]
    if model_type == SAM_MODEL_TYPE:
        return _MODEL_SPECS[SAM_MODEL_TYPE]["default_repo_id"]
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
