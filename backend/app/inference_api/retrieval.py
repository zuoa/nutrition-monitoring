import os
import time

from flask import Blueprint, current_app

from app.inference_api.common import (
    api_error,
    api_ok,
    internal_token_required,
    load_request_payload,
    parse_bboxes,
    parse_candidate_dishes,
    timed_call,
)
from app.services.inference_pipeline import EmbeddingRetrievalService
from app.services.local_embedding import LocalEmbeddingIndexService

bp = Blueprint("inference_retrieval", __name__)


@bp.route("/health", methods=["GET"])
def health():
    return {"status": "ok", "service": "retrieval-api"}


@bp.route("/health/models", methods=["GET"])
@internal_token_required
def health_models():
    cfg = current_app.config
    service = LocalEmbeddingIndexService(cfg)
    return api_ok({
        "embedding_model_path": service.embedding_model_path,
        "reranker_model_path": service.reranker_model_path,
        "index_dir": service.index_dir,
        "index_ready": bool(service._load_index()[1]),
    })


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
        service = EmbeddingRetrievalService(current_app.config)
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
        if not regions:
            raise ValueError("full 模式需要 regions")
        normalized_regions = [
            {
                "index": index,
                "bbox": bbox,
                "source": "yolo",
            }
            for index, bbox in enumerate(regions, start=1)
        ]
        service = EmbeddingRetrievalService(current_app.config)
        result, full_ms = timed_call(
            service.full,
            image_path,
            candidate_dishes=candidate_dishes,
            regions=normalized_regions,
        )
        total_ms = int(round((time.perf_counter() - started) * 1000))
        result["timings_ms"] = {"retrieve": full_ms, "total": total_ms}
        return api_ok(result)
    except ValueError as e:
        return api_error(str(e))
    except FileNotFoundError as e:
        return api_error(str(e))
    except Exception as e:
        return api_error(f"检索失败: {str(e)}", 500)
    finally:
        if cleanup and image_path and os.path.exists(image_path):
            try:
                os.unlink(image_path)
            except OSError:
                pass
