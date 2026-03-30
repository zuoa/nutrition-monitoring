import os

from flask import Blueprint, current_app

from app.inference_api.common import (
    api_error,
    api_ok,
    internal_token_required,
    load_request_payload,
    timed_call,
)
from app.services.yolo_detector import YoloRegionDetectorService

bp = Blueprint("inference_detector", __name__)


@bp.route("/health", methods=["GET"])
def health():
    return {"status": "ok", "service": "detector-api"}


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
