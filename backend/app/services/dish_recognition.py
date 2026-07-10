import logging
import time

from app.services.inference_client import (
    InferenceServiceError,
    make_detector_client,
    make_retrieval_client,
)
from app.services.qwen_vl import QwenVLService
from app.services.recognition_modes import LOCAL_RECOGNITION_MODE, normalize_recognition_mode
from app.services.runtime_config import get_effective_config


logger = logging.getLogger(__name__)


def _elapsed_ms(started: float) -> int:
    return int(round((time.perf_counter() - started) * 1000))


class DishRecognitionService:
    def __init__(self, config: dict):
        self.config = get_effective_config(config)
        self.mode = normalize_recognition_mode(self.config.get("DISH_RECOGNITION_MODE", "vl"))

    def recognize_dishes(self, image_path: str, candidate_dishes: list[dict]) -> dict:
        if self.mode == LOCAL_RECOGNITION_MODE:
            return self._recognize_dishes_via_retrieval_api(image_path, candidate_dishes)

        result = QwenVLService(self.config).recognize_dishes(image_path, candidate_dishes)
        result["model_version"] = self.config.get("QWEN_MODEL", "qwen-vl-max")
        return result

    def _recognize_dishes_via_retrieval_api(self, image_path: str, candidate_dishes: list[dict]) -> dict:
        total_started = time.perf_counter()
        if not candidate_dishes:
            return {
                "dishes": [],
                "notes": "候选菜品为空",
                "raw_response": {"mode": "local_embedding", "regions": []},
                "region_results": [],
                "model_version": "retrieval-api",
                "regions": [],
                "detector_backend": "full_image",
                "timings_ms": {"total": _elapsed_ms(total_started)},
            }

        regions, detector_backend, detector_timings = self._detect_regions_via_inference(image_path)
        if not regions and not detector_timings.get("failed"):
            return {
                "dishes": [],
                "notes": "图片中未检测到餐盘或有效菜区，已标记为无效图片",
                "raw_response": {
                    "mode": "local_embedding",
                    "regions": [],
                    "invalid_reason": "no_plate_detected",
                },
                "region_results": [],
                "model_version": "detector-api",
                "regions": [],
                "detector_backend": detector_backend,
                "valid_image": False,
                "invalid_reason": "no_plate_detected",
                "timings_ms": {
                    "detect": detector_timings.get("request", detector_timings.get("total", 0)),
                    "retrieve": 0,
                    "total": _elapsed_ms(total_started),
                    "detector": detector_timings,
                    "retrieval": {},
                },
            }

        payload = {
            "candidate_dishes": candidate_dishes,
        }
        if regions:
            payload["regions"] = [region.get("bbox") for region in regions]

        retrieval_started = time.perf_counter()
        result = make_retrieval_client(self.config).post_file(
            "/v1/full",
            image_path=image_path,
            data=payload,
        )
        retrieval_request_ms = _elapsed_ms(retrieval_started)
        retrieval_timings = result.get("timings_ms") if isinstance(result.get("timings_ms"), dict) else {}
        return {
            "dishes": result.get("recognized_dishes", []),
            "notes": str(result.get("notes") or ""),
            "raw_response": result.get("raw_response"),
            "region_results": result.get("region_results", []),
            "model_version": result.get("model_version") or "retrieval-api",
            "regions": regions,
            "detector_backend": detector_backend,
            "valid_image": True,
            "timings_ms": {
                "detect": detector_timings.get("request", detector_timings.get("total", 0)),
                "retrieve": retrieval_request_ms,
                "total": _elapsed_ms(total_started),
                "detector": detector_timings,
                "retrieval": retrieval_timings,
            },
        }

    def _detect_regions_via_inference(self, image_path: str) -> tuple[list[dict], str, dict]:
        max_regions = int(self.config.get("YOLO_MAX_REGIONS", 6) or 6)
        try:
            started = time.perf_counter()
            result = make_detector_client(self.config).post_file(
                "/v1/detect",
                image_path=image_path,
                data={"max_regions": max_regions},
            )
            request_ms = _elapsed_ms(started)
        except (InferenceServiceError, ValueError, FileNotFoundError) as e:
            logger.warning("Detector unavailable for remote local recognition, fallback to full-image retrieval: %s", e)
            return [], "full_image", {"request": _elapsed_ms(started) if "started" in locals() else 0, "failed": True}

        proposals = result.get("regions") or result.get("proposals") or []
        backend = str(result.get("backend") or "detector")
        timings = result.get("timings_ms") if isinstance(result.get("timings_ms"), dict) else {}
        timings = {**timings, "request": request_ms}
        regions = []
        for idx, item in enumerate(proposals[:max_regions], start=1):
            bbox = item.get("bbox") or {}
            x1 = int(bbox.get("x1", 0))
            y1 = int(bbox.get("y1", 0))
            x2 = int(bbox.get("x2", 0))
            y2 = int(bbox.get("y2", 0))
            if x2 - x1 < 24 or y2 - y1 < 24:
                continue
            regions.append({
                "index": int(item.get("index") or idx),
                "bbox": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                "confidence": float(item.get("score", 0.0) or 0.0),
                "source": str(item.get("source") or backend),
            })

        regions.sort(key=lambda item: item.get("confidence", 0.0), reverse=True)
        return regions[:max_regions], backend if regions else f"{backend}_no_regions", timings
