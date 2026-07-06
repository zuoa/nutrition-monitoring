import os

from flask import Blueprint, current_app, request

from app.inference_api.common import (
    api_error,
    api_ok,
    internal_token_required,
    load_request_payload,
    timed_call,
)
from app.services.runtime_config import get_effective_config, persist_runtime_overrides
from app.services.yolo_detector import YoloRegionDetectorService, clear_yolo_cache, is_yolo_model_ready

bp = Blueprint("inference_detector", __name__)

ALLOWED_YOLO_MODEL_EXTENSIONS = {".pt"}
DEFAULT_YOLO_MODEL_PATH = "/models/yolo/best.pt"
DEFAULT_MAX_YOLO_MODEL_SIZE = 500 * 1024 * 1024


def _max_yolo_model_size(config: dict) -> int:
    raw = config.get("MAX_YOLO_MODEL_SIZE")
    if raw is None or raw == "":
        return DEFAULT_MAX_YOLO_MODEL_SIZE
    try:
        return max(int(raw), 1)
    except (TypeError, ValueError):
        return DEFAULT_MAX_YOLO_MODEL_SIZE


def _resolve_yolo_model_path(config: dict) -> str:
    cfg = get_effective_config(config)
    path = str(cfg.get("YOLO_MODEL_PATH", "") or "").strip()
    return path or DEFAULT_YOLO_MODEL_PATH


@bp.route("/health", methods=["GET"])
def health():
    return {"status": "ok", "service": "detector-api"}


@bp.route("/v1/models/yolo/status", methods=["GET"])
@internal_token_required
def yolo_model_status():
    try:
        cfg = get_effective_config(current_app.config)
        model_path = _resolve_yolo_model_path(current_app.config)
        ready = is_yolo_model_ready(current_app.config)
        return api_ok({
            "yolo_model_path": model_path,
            "yolo_model_ready": ready,
            "yolo_model_filename": os.path.basename(model_path) or "",
            "yolo_conf_threshold": float(cfg.get("YOLO_CONF_THRESHOLD", 0.75)),
            "yolo_iou_threshold": float(cfg.get("YOLO_IOU_THRESHOLD", 0.45)),
            "yolo_max_regions": int(cfg.get("YOLO_MAX_REGIONS", 6)),
        })
    except Exception as e:
        return api_error(f"获取 YOLO 模型状态失败: {str(e)}", 500)


@bp.route("/v1/models/yolo/upload", methods=["POST"])
@internal_token_required
def upload_yolo_model():
    if "model_file" not in request.files:
        return api_error("请上传模型文件")

    model_file = request.files["model_file"]
    if not model_file or not model_file.filename:
        return api_error("文件名无效")

    ext = os.path.splitext(model_file.filename)[1].lower()
    if ext not in ALLOWED_YOLO_MODEL_EXTENSIONS:
        return api_error(f"不支持的模型格式，请上传 {', '.join(sorted(ALLOWED_YOLO_MODEL_EXTENSIONS))} 格式")

    content_length = request.content_length
    max_size = _max_yolo_model_size(current_app.config)
    if content_length is not None and content_length > max_size:
        return api_error(f"模型文件大小超过限制 {max_size / (1024 * 1024):.0f} MB")

    tmp_path = ""
    try:
        model_path = _resolve_yolo_model_path(current_app.config)
        os.makedirs(os.path.dirname(model_path) or ".", exist_ok=True)

        dest_tmp = f"{model_path}.tmp"
        with open(dest_tmp, "wb") as tmp:
            model_file.save(tmp)
            tmp_path = dest_tmp

        actual_size = os.path.getsize(dest_tmp)
        if actual_size > max_size:
            return api_error(f"模型文件大小超过限制 {max_size / (1024 * 1024):.0f} MB")

        os.replace(dest_tmp, model_path)

        persist_runtime_overrides(current_app.config, {"YOLO_MODEL_PATH": model_path})
        clear_yolo_cache()

        return api_ok({
            "yolo_model_path": model_path,
            "yolo_model_ready": os.path.isfile(model_path),
            "yolo_model_filename": os.path.basename(model_path),
            "size": actual_size,
        })
    except ValueError as e:
        return api_error(str(e))
    except OSError as e:
        return api_error(f"模型文件保存失败: {str(e)}", 500)
    except Exception as e:
        return api_error(f"上传 YOLO 模型失败: {str(e)}", 500)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


@bp.route("/v1/detect", methods=["POST"])
@internal_token_required
def detect():
    cleanup = False
    image_path = None
    try:
        payload, image_path, cleanup = load_request_payload()
        conf_threshold = float(payload.get("conf_threshold")) if payload.get("conf_threshold") not in (None, "") else None
        iou_threshold = float(payload.get("iou_threshold")) if payload.get("iou_threshold") not in (None, "") else None
        max_regions = int(payload.get("max_regions")) if payload.get("max_regions") not in (None, "") else None
        service = YoloRegionDetectorService(current_app.config)
        result, elapsed_ms = timed_call(
            service.detect_regions,
            image_path,
            conf_threshold=conf_threshold,
            iou_threshold=iou_threshold,
            max_regions=max_regions,
        )
        return api_ok({
            "backend": result.get("backend", "yolo"),
            "regions": result.get("proposals", []),
            "model_version": os.path.basename(service.model_path) or "yolo",
            "timings_ms": {"detect": elapsed_ms, "total": elapsed_ms},
        })
    except ValueError as e:
        return api_error(str(e))
    except FileNotFoundError as e:
        return api_error(str(e))
    except Exception as e:
        return api_error(f"检测失败: {str(e)}", 500)
    finally:
        if cleanup and image_path and os.path.exists(image_path):
            try:
                os.unlink(image_path)
            except OSError:
                pass
