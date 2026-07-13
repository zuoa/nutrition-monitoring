import json
import logging
import os
import shutil
import tempfile
import time
import uuid
import zipfile

import numpy as np
from flask import Blueprint, current_app, request

from app.inference_api.common import (
    api_error,
    api_ok,
    internal_token_required,
    load_request_payload,
    parse_bboxes,
    parse_candidate_dishes,
    timed_call,
)
from app.inference_api.model_download_tasks import (
    ensure_remote_download_worker,
    find_remote_download_state,
    read_remote_download_state,
    utcnow_iso,
    write_remote_download_state,
)
from app.services.inference_pipeline import EmbeddingRetrievalService
from app.services.local_embedding import LocalEmbeddingIndexService
from app.services.visual_embedding import VisualEmbeddingIndexService
from app.services.local_model_manager import (
    DINOV3_MODEL_TYPE,
    EMBEDDING_MODEL_TYPE,
    RERANKER_MODEL_TYPE,
    SIGLIP2_MODEL_TYPE,
    get_local_model_spec,
    has_model_variants,
    is_managed_model_ready,
    is_local_model_ready,
)
from app.services.model_downloads import DEFAULT_HF_ENDPOINT
from app.services.runtime_config import get_effective_config, persist_runtime_overrides

bp = Blueprint("inference_retrieval", __name__)
logger = logging.getLogger(__name__)


def _invalidate_pipeline_index(config: dict, pipeline: str) -> None:
    service = VisualEmbeddingIndexService(config) if pipeline == "visual" else LocalEmbeddingIndexService(config)
    os.makedirs(service.index_dir, exist_ok=True)
    stale_path = os.path.join(service.index_dir, getattr(service, "STALE_FILENAME", ".stale"))
    with open(stale_path, "w", encoding="utf-8") as marker:
        marker.write(f"invalidated_at={utcnow_iso()}\n")
    service._index_cache_key = None
    service._index_matrix = None
    service._index_metadata = None


def _build_model_health_payload(config: dict) -> dict:
    cfg = get_effective_config(config)
    embedding_spec = get_local_model_spec(cfg, EMBEDDING_MODEL_TYPE)
    reranker_spec = get_local_model_spec(cfg, RERANKER_MODEL_TYPE)
    siglip_spec = get_local_model_spec(cfg, SIGLIP2_MODEL_TYPE)
    dino_spec = get_local_model_spec(cfg, DINOV3_MODEL_TYPE)
    service = LocalEmbeddingIndexService(cfg)
    visual_service = VisualEmbeddingIndexService(cfg)
    return {
        "hf_endpoint": cfg.get("HF_ENDPOINT", "") or DEFAULT_HF_ENDPOINT,
        "local_model_storage_path": cfg.get("LOCAL_MODEL_STORAGE_PATH", "/data/models"),
        "local_runtime_config_path": cfg.get("LOCAL_RUNTIME_CONFIG_PATH", ""),
        "embedding_active_variant": embedding_spec["active_variant"],
        "embedding_repo_id": embedding_spec["repo_id"],
        "embedding_model_path": cfg.get("LOCAL_QWEN3_VL_EMBEDDING_MODEL_PATH", ""),
        "embedding_model_downloaded": is_local_model_ready(embedding_spec["path"]),
        "reranker_active_variant": reranker_spec["active_variant"],
        "reranker_repo_id": reranker_spec["repo_id"],
        "reranker_model_path": cfg.get("LOCAL_QWEN3_VL_RERANKER_MODEL_PATH", ""),
        "reranker_model_downloaded": is_local_model_ready(reranker_spec["path"]),
        "reranker_enabled": bool(service.rerank_enabled),
        "siglip2_repo_id": siglip_spec["repo_id"],
        "siglip2_model_path": siglip_spec["path"],
        "siglip2_model_downloaded": is_managed_model_ready(siglip_spec["path"]),
        "dinov3_repo_id": dino_spec["repo_id"],
        "dinov3_model_path": dino_spec["path"],
        "dinov3_model_downloaded": is_managed_model_ready(dino_spec["path"]),
        "retrieval_pipeline": str(cfg.get("LOCAL_RETRIEVAL_PIPELINE", "qwen") or "qwen"),
        "index_dir": service.index_dir,
        "index_ready": bool(service._load_index()[1]),
        "visual_index_dir": visual_service.index_dir,
        "visual_index_ready": bool(visual_service._load_index()[1]) and visual_service._load_patch_matrix().size > 0,
    }


def _normalize_archive_member_path(value: str, *, label: str) -> str:
    normalized = os.path.normpath(str(value or "")).lstrip("/\\")
    if normalized.startswith(".."):
        raise ValueError(f"{label} 包含非法路径")
    return normalized


def _stage_uploaded_index(
    service: LocalEmbeddingIndexService,
    *,
    matrix: np.ndarray,
    metadata: list,
    samples_archive_path: str | None,
    reuse_existing_samples: bool = False,
    extra_files: dict[str, str] | None = None,
) -> tuple[str, str]:
    index_dir = service.index_dir
    os.makedirs(index_dir, exist_ok=True)

    # Keep staging inside index_dir so later os.replace calls stay on the same filesystem.
    stage_dir = tempfile.mkdtemp(prefix=".index-stage-", dir=index_dir)
    try:
        stage_sample_root = os.path.join(stage_dir, "sample_images")
        os.makedirs(stage_sample_root, exist_ok=True)
        final_sample_root = os.path.join(index_dir, "sample_images")

        if metadata and not samples_archive_path and not reuse_existing_samples:
            raise ValueError("metadata_file 非空时必须提供 samples_archive")

        if samples_archive_path:
            with zipfile.ZipFile(samples_archive_path) as archive:
                for member in archive.infolist():
                    if member.is_dir():
                        continue
                    normalized_name = _normalize_archive_member_path(member.filename, label="samples_archive")
                    dest_path = os.path.abspath(os.path.join(stage_sample_root, normalized_name))
                    if os.path.commonpath([os.path.abspath(stage_sample_root), dest_path]) != os.path.abspath(stage_sample_root):
                        raise ValueError("samples_archive 包含越界路径")
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    with archive.open(member, "r") as src, open(dest_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)

        rewritten_metadata = []
        for item in metadata:
            if not isinstance(item, dict):
                raise ValueError("metadata_file 数组项必须是对象")
            next_item = dict(item)
            relative_image_path = str(next_item.pop("relative_image_path", "") or "").strip()
            if relative_image_path:
                normalized_rel = _normalize_archive_member_path(relative_image_path, label="metadata_file")
                staged_image_path = os.path.join(stage_sample_root, normalized_rel)
                if not os.path.exists(staged_image_path):
                    existing_image_path = os.path.join(final_sample_root, normalized_rel)
                    if not reuse_existing_samples or not os.path.exists(existing_image_path):
                        raise ValueError(f"samples_archive 缺少文件: {normalized_rel}")
                    os.makedirs(os.path.dirname(staged_image_path), exist_ok=True)
                    try:
                        os.link(existing_image_path, staged_image_path)
                    except OSError:
                        shutil.copy2(existing_image_path, staged_image_path)
                next_item["image_path"] = os.path.join(final_sample_root, normalized_rel)
            rewritten_metadata.append(next_item)

        stage_matrix_path = os.path.join(stage_dir, service.MATRIX_FILENAME)
        normalized_matrix = matrix if matrix.size == 0 else np.vstack([service._normalize(vector) for vector in matrix])
        np.save(stage_matrix_path, normalized_matrix.astype(np.float32))
        stage_metadata_path = os.path.join(stage_dir, service.METADATA_FILENAME)
        with open(stage_metadata_path, "w", encoding="utf-8") as f:
            json.dump(rewritten_metadata, f, ensure_ascii=False, indent=2)

        for filename, source_path in (extra_files or {}).items():
            shutil.copy2(source_path, os.path.join(stage_dir, filename))

        return stage_dir, final_sample_root
    except Exception:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise


def _install_staged_index(service: LocalEmbeddingIndexService, *, stage_dir: str) -> None:
    index_dir = service.index_dir
    os.makedirs(index_dir, exist_ok=True)
    # Backups must live alongside the target index files to avoid cross-device renames.
    backup_dir = tempfile.mkdtemp(prefix=".index-backup-", dir=index_dir)

    targets = {
        service.MATRIX_FILENAME: os.path.join(index_dir, service.MATRIX_FILENAME),
        service.METADATA_FILENAME: os.path.join(index_dir, service.METADATA_FILENAME),
        "sample_images": os.path.join(index_dir, "sample_images"),
    }
    staged_paths = {
        service.MATRIX_FILENAME: os.path.join(stage_dir, service.MATRIX_FILENAME),
        service.METADATA_FILENAME: os.path.join(stage_dir, service.METADATA_FILENAME),
        "sample_images": os.path.join(stage_dir, "sample_images"),
    }
    for filename in service.extra_index_filenames() if hasattr(service, "extra_index_filenames") else []:
        staged_path = os.path.join(stage_dir, filename)
        if os.path.exists(staged_path):
            targets[filename] = os.path.join(index_dir, filename)
            staged_paths[filename] = staged_path
    backups: dict[str, str] = {}
    installed: list[str] = []

    try:
        for name, target_path in targets.items():
            if not os.path.exists(target_path):
                continue
            backup_path = os.path.join(backup_dir, name)
            os.replace(target_path, backup_path)
            backups[name] = backup_path

        for name, staged_path in staged_paths.items():
            os.replace(staged_path, targets[name])
            installed.append(name)
    except Exception:
        for name in installed:
            target_path = targets[name]
            try:
                if os.path.isdir(target_path):
                    shutil.rmtree(target_path, ignore_errors=True)
                elif os.path.exists(target_path):
                    os.unlink(target_path)
            except OSError:
                pass

        for name, backup_path in backups.items():
            if os.path.exists(backup_path):
                os.replace(backup_path, targets[name])
        raise
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)
        shutil.rmtree(backup_dir, ignore_errors=True)

    service._index_cache_key = None
    service._index_matrix = None
    service._index_metadata = None
    stale_path = os.path.join(index_dir, getattr(service, "STALE_FILENAME", ".stale"))
    if os.path.exists(stale_path):
        os.unlink(stale_path)


@bp.route("/health", methods=["GET"])
def health():
    return {"status": "ok", "service": "retrieval-api"}


@bp.route("/health/models", methods=["GET"])
@internal_token_required
def health_models():
    return api_ok(_build_model_health_payload(current_app.config))


@bp.route("/v1/models/download", methods=["POST"])
@internal_token_required
def download_model():
    config = get_effective_config(current_app.config)
    data = request.get_json(silent=True) or {}
    model_type = str(data.get("model_type") or "").strip()
    if model_type not in {EMBEDDING_MODEL_TYPE, RERANKER_MODEL_TYPE, SIGLIP2_MODEL_TYPE, DINOV3_MODEL_TYPE}:
        return api_error("不支持的模型类型")

    variant = data.get("variant") if has_model_variants(model_type) else None
    try:
        spec = get_local_model_spec(config, model_type, variant=variant)
    except ValueError as e:
        return api_error(str(e))

    parent_dir = os.path.dirname(spec["path"]) or "."
    try:
        os.makedirs(parent_dir, exist_ok=True)
    except OSError:
        return api_error(f"无法创建模型目录: {parent_dir}")

    existing = find_remote_download_state(
        config,
        model_type=model_type,
        variant=spec["variant"],
    )
    if existing:
        existing_task_id = str(existing.get("task_id") or "").strip()
        if existing_task_id:
            ensure_remote_download_worker(config, existing_task_id)
        return api_ok(existing)

    hf_endpoint = (config.get("HF_ENDPOINT") or "").strip()
    task_id = uuid.uuid4().hex
    state = {
        "task_id": task_id,
        "model_type": model_type,
        "variant": spec["variant"],
        "repo_id": spec["repo_id"],
        "target_path": spec["path"],
        "hf_endpoint": hf_endpoint or DEFAULT_HF_ENDPOINT,
        "status": "pending",
        "progress_percent": 0.0,
        "downloaded_bytes": 0,
        "total_bytes": 0,
        "downloaded_files": 0,
        "total_files": 0,
        "status_text": "等待下载开始",
        "error_message": "",
        "started_at": None,
        "finished_at": None,
        "created_at": utcnow_iso(),
    }
    write_remote_download_state(config, task_id, state)
    ensure_remote_download_worker(config, task_id)
    return api_ok(state), 202


@bp.route("/v1/models/download/<task_id>", methods=["GET"])
@internal_token_required
def get_download_model_status(task_id: str):
    config = get_effective_config(current_app.config)
    state = read_remote_download_state(config, task_id)
    if not state:
        return api_error("模型下载任务不存在", 404)
    ensure_remote_download_worker(config, task_id)
    state = read_remote_download_state(config, task_id) or state
    return api_ok(state)


@bp.route("/v1/models/activate", methods=["POST"])
@internal_token_required
def activate_model():
    config = get_effective_config(current_app.config)
    data = request.get_json(silent=True) or {}
    model_type = str(data.get("model_type") or "").strip()
    if model_type not in {EMBEDDING_MODEL_TYPE, RERANKER_MODEL_TYPE, SIGLIP2_MODEL_TYPE, DINOV3_MODEL_TYPE}:
        return api_error("不支持的模型类型")

    variant = data.get("variant") if has_model_variants(model_type) else None
    try:
        spec = get_local_model_spec(config, model_type, variant=variant)
    except ValueError as e:
        return api_error(str(e))

    ready_check = is_managed_model_ready if model_type in {SIGLIP2_MODEL_TYPE, DINOV3_MODEL_TYPE} else is_local_model_ready
    if not ready_check(spec["path"]):
        return api_error(
            f"{spec['label']}" + (f" {spec['variant']}" if spec["variant"] else "") + " 模型尚未下载完成",
        )

    current_spec = get_local_model_spec(config, model_type)
    model_changed = (
        current_spec["repo_id"] != spec["repo_id"]
        or current_spec["path"] != spec["path"]
    )

    if model_type == EMBEDDING_MODEL_TYPE:
        updates = {
            "LOCAL_QWEN3_VL_EMBEDDING_REPO_ID": spec["repo_id"],
            "LOCAL_QWEN3_VL_EMBEDDING_MODEL_PATH": spec["path"],
        }
    elif model_type == RERANKER_MODEL_TYPE:
        updates = {
            "LOCAL_QWEN3_VL_RERANKER_REPO_ID": spec["repo_id"],
            "LOCAL_QWEN3_VL_RERANKER_MODEL_PATH": spec["path"],
        }
    else:
        updates = {
            spec["repo_env"]: spec["repo_id"],
            spec["path_env"]: spec["path"],
        }

    runtime_config_path = persist_runtime_overrides(current_app.config, updates)
    current_app.config.update(updates)
    current_app.config["LOCAL_RUNTIME_CONFIG_PATH"] = runtime_config_path

    invalidated_pipeline = None
    if model_changed and model_type == EMBEDDING_MODEL_TYPE:
        invalidated_pipeline = "qwen"
    elif model_changed and model_type in {SIGLIP2_MODEL_TYPE, DINOV3_MODEL_TYPE}:
        invalidated_pipeline = "visual"
    if invalidated_pipeline:
        _invalidate_pipeline_index(current_app.config, invalidated_pipeline)

    return api_ok({
        "message": (
            f"已切换当前 {spec['label']}"
            + (f" 模型到 {spec['variant']}" if spec["variant"] else " 模型")
            + (f"，{invalidated_pipeline} 索引需重建" if invalidated_pipeline else "")
        ),
        "model_type": model_type,
        "variant": spec["variant"],
        "repo_id": spec["repo_id"],
        "target_path": spec["path"],
        "runtime_config_path": runtime_config_path,
        "requires_index_rebuild": bool(invalidated_pipeline),
        "invalidated_pipeline": invalidated_pipeline,
    })


@bp.route("/v1/pipeline/activate", methods=["POST"])
@internal_token_required
def activate_pipeline():
    config = get_effective_config(current_app.config)
    data = request.get_json(silent=True) or {}
    pipeline = str(data.get("pipeline") or "").strip().lower()
    if pipeline not in {"qwen", "visual"}:
        return api_error("不支持的检索 pipeline")
    service = VisualEmbeddingIndexService(config) if pipeline == "visual" else LocalEmbeddingIndexService(config)
    _, metadata = service._load_index()
    if not metadata:
        return api_error(f"{pipeline} 索引尚未构建")
    if pipeline == "visual":
        siglip = get_local_model_spec(config, SIGLIP2_MODEL_TYPE)
        dino = get_local_model_spec(config, DINOV3_MODEL_TYPE)
        if not is_managed_model_ready(siglip["path"]) or not is_managed_model_ready(dino["path"]):
            return api_error("SigLIP2 或 DINOv3 模型尚未下载完成")
        if service._load_patch_matrix().size == 0:
            return api_error("visual patch 索引为空")
    if service.rerank_enabled:
        reranker = get_local_model_spec(config, RERANKER_MODEL_TYPE)
        if not is_local_model_ready(reranker["path"]):
            return api_error("当前已启用 Qwen reranker，但模型尚未下载完成")
    updates = {"LOCAL_RETRIEVAL_PIPELINE": pipeline}
    runtime_config_path = persist_runtime_overrides(current_app.config, updates)
    current_app.config.update(updates)
    current_app.config["LOCAL_RUNTIME_CONFIG_PATH"] = runtime_config_path
    return api_ok({
        "message": f"已切换到 {pipeline} 检索模式",
        "pipeline": pipeline,
        "runtime_config_path": runtime_config_path,
    })


@bp.route("/v1/index/invalidate", methods=["POST"])
@internal_token_required
def invalidate_index():
    data = request.get_json(silent=True) or {}
    pipeline = str(data.get("pipeline") or "").strip().lower()
    if pipeline not in {"qwen", "visual"}:
        return api_error("不支持的索引 pipeline")
    _invalidate_pipeline_index(current_app.config, pipeline)
    return api_ok({"pipeline": pipeline, "index_ready": False})


@bp.route("/v1/index/upload", methods=["POST"])
@internal_token_required
def upload_index():
    matrix_file = request.files.get("matrix_file")
    metadata_file = request.files.get("metadata_file")
    samples_archive = request.files.get("samples_archive")
    patch_file = request.files.get("patch_file")
    pipeline = str(request.form.get("pipeline") or "qwen").strip().lower()
    if pipeline not in {"qwen", "visual"}:
        return api_error("不支持的索引 pipeline")
    if pipeline == "visual" and (not patch_file or not patch_file.filename):
        return api_error("visual 索引需要 patch_file")
    if not matrix_file or not matrix_file.filename:
        return api_error("请提供 matrix_file")
    if not metadata_file or not metadata_file.filename:
        return api_error("请提供 metadata_file")

    matrix_tmp = ""
    metadata_tmp = ""
    samples_tmp = ""
    patch_tmp = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".npy", delete=False) as tmp:
            matrix_file.save(tmp.name)
            matrix_tmp = tmp.name
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            metadata_file.save(tmp.name)
            metadata_tmp = tmp.name
        if samples_archive and samples_archive.filename:
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                samples_archive.save(tmp.name)
                samples_tmp = tmp.name
        if patch_file and patch_file.filename:
            with tempfile.NamedTemporaryFile(suffix=".npy", delete=False) as tmp:
                patch_file.save(tmp.name)
                patch_tmp = tmp.name

        matrix = np.load(matrix_tmp)
        if matrix.ndim != 2:
            return api_error("matrix_file 必须是二维数组")
        with open(metadata_tmp, "r", encoding="utf-8") as f:
            metadata = json.load(f)
        if not isinstance(metadata, list):
            return api_error("metadata_file 必须是 JSON 数组")
        if matrix.shape[0] != len(metadata):
            return api_error("matrix_file 行数必须与 metadata_file 条目数一致")
        if pipeline == "visual":
            patch_matrix = np.load(patch_tmp)
            if patch_matrix.ndim != 2:
                return api_error("patch_file 必须是二维数组")
            for item in metadata:
                offset = int(item.get("patch_offset", 0) or 0)
                count = int(item.get("patch_count", 0) or 0)
                if offset < 0 or count <= 0 or offset + count > patch_matrix.shape[0]:
                    return api_error("visual metadata 中的 patch offset/count 无效")

        service = VisualEmbeddingIndexService(current_app.config) if pipeline == "visual" else LocalEmbeddingIndexService(current_app.config)
        stage_dir, sample_root = _stage_uploaded_index(
            service,
            matrix=matrix,
            metadata=metadata,
            samples_archive_path=samples_tmp or None,
            reuse_existing_samples=str(request.form.get("reuse_existing_samples", "")).strip().lower() in {"1", "true", "yes"},
            extra_files={service.PATCH_FILENAME: patch_tmp} if pipeline == "visual" else None,
        )
        _install_staged_index(service, stage_dir=stage_dir)
        reloaded_matrix, reloaded_metadata = service._load_index()
        return api_ok({
            "index_ready": bool(reloaded_metadata),
            "embedding_count": int(reloaded_matrix.shape[0]) if getattr(reloaded_matrix, "ndim", 0) >= 1 else 0,
            "index_dir": service.index_dir,
            "sample_image_root": sample_root,
            "pipeline": pipeline,
        })
    except ValueError as e:
        return api_error(str(e))
    except Exception as e:
        return api_error(f"写入索引失败: {str(e)}", 500)
    finally:
        for path in (matrix_tmp, metadata_tmp, samples_tmp, patch_tmp):
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass


@bp.route("/v1/index/reload", methods=["POST"])
@internal_token_required
def reload_index():
    service = LocalEmbeddingIndexService(current_app.config)
    service._index_cache_key = None
    service._index_matrix = None
    service._index_metadata = None
    matrix, metadata = service._load_index()
    return api_ok({
        "index_ready": bool(metadata),
        "embedding_count": int(matrix.shape[0]) if getattr(matrix, "ndim", 0) >= 1 else 0,
    })


@bp.route("/v1/embed", methods=["POST"])
@internal_token_required
def embed():
    cleanup = False
    image_path = None
    try:
        payload, image_path, cleanup = load_request_payload()
        bboxes = parse_bboxes(payload.get("bboxes"))
        instruction = str(payload.get("instruction") or "").strip() or None
        pipeline = str(payload.get("pipeline") or "").strip() or None
        service = EmbeddingRetrievalService(current_app.config, pipeline=pipeline) if pipeline else EmbeddingRetrievalService(current_app.config)
        result, elapsed_ms = timed_call(
            service.embed,
            image_path,
            bboxes=bboxes or None,
            instruction=instruction,
        )
        result["timings_ms"] = {"embed": elapsed_ms, "total": elapsed_ms}
        return api_ok(result)
    except ValueError as e:
        return api_error(str(e))
    except FileNotFoundError as e:
        return api_error(str(e))
    except Exception as e:
        return api_error(f"embedding 失败: {str(e)}", 500)
    finally:
        if cleanup and image_path and os.path.exists(image_path):
            try:
                os.unlink(image_path)
            except OSError:
                pass


@bp.route("/v1/full", methods=["POST"])
@internal_token_required
def full():
    return _run_retrieval()


@bp.route("/v1/retrieve", methods=["POST"])
@internal_token_required
def retrieve():
    return _run_retrieval()


def _run_retrieval():
    cleanup = False
    image_path = None
    started = time.perf_counter()
    try:
        payload, image_path, cleanup = load_request_payload()
        candidate_dishes = parse_candidate_dishes(payload.get("candidate_dishes"))
        regions = parse_bboxes(payload.get("regions"))
        if not candidate_dishes:
            raise ValueError("full 模式需要 candidate_dishes")
        if regions:
            normalized_regions = [
                {
                    "index": index,
                    "bbox": bbox,
                    "source": "yolo",
                }
                for index, bbox in enumerate(regions, start=1)
            ]
        else:
            normalized_regions = [{
                "index": 1,
                "bbox": None,
                "source": "full_image",
            }]
        pipeline = str(payload.get("pipeline") or "").strip() or None
        service = EmbeddingRetrievalService(current_app.config, pipeline=pipeline) if pipeline else EmbeddingRetrievalService(current_app.config)
        result, full_ms = timed_call(
            service.full,
            image_path,
            candidate_dishes=candidate_dishes,
            regions=normalized_regions,
        )
        total_ms = int(round((time.perf_counter() - started) * 1000))
        service_timings = result.get("timings_ms") if isinstance(result.get("timings_ms"), dict) else {}
        result["timings_ms"] = {
            "retrieve": full_ms,
            "total": total_ms,
            **{f"local_{key}": value for key, value in service_timings.items()},
        }
        return api_ok(result)
    except ValueError as e:
        return api_error(str(e))
    except FileNotFoundError as e:
        return api_error(str(e))
    except Exception as e:
        logger.exception(
            "Retrieval request failed: image=%s candidate_count=%s region_count=%s payload_keys=%s",
            os.path.basename(image_path or ""),
            len(candidate_dishes) if "candidate_dishes" in locals() else None,
            len(normalized_regions) if "normalized_regions" in locals() else None,
            sorted(payload.keys()) if "payload" in locals() and isinstance(payload, dict) else None,
        )
        return api_error(f"检索失败: {str(e)}", 500)
    finally:
        if cleanup and image_path and os.path.exists(image_path):
            try:
                os.unlink(image_path)
            except OSError:
                pass
