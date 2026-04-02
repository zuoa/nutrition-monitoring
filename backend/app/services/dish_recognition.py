import logging

from app.services.inference_client import (
    InferenceServiceError,
    make_detector_client,
    make_retrieval_client,
)
from app.services.qwen_vl import QwenVLService
from app.services.recognition_modes import LOCAL_RECOGNITION_MODE, normalize_recognition_mode
from app.services.runtime_config import get_effective_config


logger = logging.getLogger(__name__)


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
        if not candidate_dishes:
            return {
                "dishes": [],
                "notes": "候选菜品为空",
                "raw_response": {"mode": "local_embedding", "regions": []},
                "region_results": [],
                "model_version": "retrieval-api",
                "regions": [],
                "detector_backend": "full_image",
            }

        regions, detector_backend = self._detect_regions_via_inference(image_path)
        payload = {
            "candidate_dishes": candidate_dishes,
        }
        if regions:
            payload["regions"] = [region.get("bbox") for region in regions]

        result = make_retrieval_client(self.config).post_file(
            "/v1/full",
            image_path=image_path,
            data=payload,
        )
        return {
            "dishes": result.get("recognized_dishes", []),
            "notes": str(result.get("notes") or ""),
            "raw_response": result.get("raw_response"),
            "region_results": result.get("region_results", []),
            "model_version": result.get("model_version") or "retrieval-api",
            "regions": regions,
            "detector_backend": detector_backend,
        }

    def _detect_regions_via_inference(self, image_path: str) -> tuple[list[dict], str]:
        max_regions = int(self.config.get("YOLO_MAX_REGIONS", 6) or 6)
        try:
            result = make_detector_client(self.config).post_file(
                "/v1/detect",
                image_path=image_path,
                data={"max_regions": max_regions},
            )
        except (InferenceServiceError, ValueError, FileNotFoundError) as e:
            logger.warning("Detector unavailable for remote local recognition, fallback to full-image retrieval: %s", e)
            return [], "full_image"

        proposals = result.get("regions") or result.get("proposals") or []
        backend = str(result.get("backend") or "detector")
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
        return regions[:max_regions], backend if regions else "full_image"
