import logging
import os
from threading import Lock
from typing import Any

from app.services.runtime_config import get_effective_config

logger = logging.getLogger(__name__)

_YOLO_CACHE: dict[tuple[str, str], Any] = {}
_YOLO_CACHE_LOCK = Lock()


class YoloRegionDetectorService:
    def __init__(self, config: dict):
        self.config = get_effective_config(config)
        self.model_path = str(self.config.get("YOLO_MODEL_PATH", "") or "").strip()
        self.device = str(self.config.get("YOLO_DEVICE", "") or "").strip()
        self.conf_threshold = float(self.config.get("YOLO_CONF_THRESHOLD", 0.75))
        self.iou_threshold = float(self.config.get("YOLO_IOU_THRESHOLD", 0.45))
        self.max_regions = int(self.config.get("YOLO_MAX_REGIONS", 6))
        self.class_id = int(self.config.get("YOLO_CLASS_ID", 0))

    def detect_regions(
        self,
        image_path: str,
        *,
        conf_threshold: float | None = None,
        iou_threshold: float | None = None,
        max_regions: int | None = None,
    ) -> dict[str, Any]:
        if not image_path:
            raise ValueError("图片路径不存在")
        if not os.path.exists(image_path):
            raise FileNotFoundError("图片文件不存在")
        if not self.model_path:
            raise ValueError("未配置 YOLO_MODEL_PATH")

        model = self._get_model()
        device = self._resolve_device()
        conf = float(conf_threshold if conf_threshold is not None else self.conf_threshold)
        iou = float(iou_threshold if iou_threshold is not None else self.iou_threshold)
        limit = int(max_regions if max_regions is not None else self.max_regions)

        predictions = model.predict(
            source=image_path,
            conf=conf,
            iou=iou,
            max_det=limit,
            classes=[self.class_id],
            device=device,
            verbose=False,
        )
        result = predictions[0] if predictions else None
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            return {"backend": "yolo", "proposals": []}

        names = getattr(model, "names", {}) or {}
        proposals = []
        xyxy = getattr(boxes, "xyxy", None)
        confs = getattr(boxes, "conf", None)
        class_ids = getattr(boxes, "cls", None)
        count = len(xyxy) if xyxy is not None else 0
        for idx in range(count):
            box = xyxy[idx].tolist() if hasattr(xyxy[idx], "tolist") else xyxy[idx]
            score = confs[idx].item() if confs is not None and hasattr(confs[idx], "item") else (confs[idx] if confs is not None else 0.0)
            class_id = int(class_ids[idx].item()) if class_ids is not None and hasattr(class_ids[idx], "item") else int(class_ids[idx]) if class_ids is not None else 0
            x1, y1, x2, y2 = [int(round(float(value))) for value in box]
            if x2 - x1 < 24 or y2 - y1 < 24:
                continue
            proposals.append({
                "index": len(proposals) + 1,
                "bbox": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                "score": max(0.0, min(1.0, float(score))),
                "class_id": class_id,
                "class_name": str(names.get(class_id, class_id)),
                "source": "yolo",
            })

        proposals.sort(key=lambda item: item["score"], reverse=True)
        limited = proposals[:limit]
        for index, proposal in enumerate(limited, start=1):
            proposal["index"] = index
        return {"backend": "yolo", "proposals": limited}

    def _get_model(self):
        device = self._resolve_device()
        cache_key = (os.path.abspath(self.model_path), device)
        cached = _YOLO_CACHE.get(cache_key)
        if cached is not None:
            return cached

        with _YOLO_CACHE_LOCK:
            cached = _YOLO_CACHE.get(cache_key)
            if cached is not None:
                return cached

            try:
                from ultralytics import YOLO
            except Exception as e:
                raise RuntimeError("未安装 ultralytics 或其依赖") from e

            model = YOLO(self.model_path)
            _YOLO_CACHE[cache_key] = model
            return model

    def _resolve_device(self) -> str:
        if self.device:
            return self.device
        try:
            import torch
        except Exception:
            return "cpu"
        return "cuda:0" if torch.cuda.is_available() else "cpu"
