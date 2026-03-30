import logging
import os
import re
from threading import Lock
from typing import Any

from PIL import Image

from app.services.runtime_config import get_effective_config

logger = logging.getLogger(__name__)

_GROUNDING_CACHE: dict[tuple[str, str], tuple[Any, Any, str]] = {}
_SAM_CACHE: dict[tuple[str, str], tuple[Any, Any, str]] = {}
_CACHE_LOCK = Lock()
_DEFAULT_PROMPTS = [
    "food portion",
    "prepared dish",
    "meal",
    "food on tray",
]


class RegionProposalService:
    def __init__(self, config: dict):
        self.config = get_effective_config(config)
        self.model_path = str(self.config.get("LOCAL_REGION_PROPOSAL_MODEL_PATH", "") or "").strip()
        self.device = str(self.config.get("LOCAL_REGION_PROPOSAL_DEVICE", "") or "").strip()
        self.sam_model_path = str(self.config.get("LOCAL_SAM_MODEL_PATH", "") or "").strip()
        self.sam_device = str(self.config.get("LOCAL_SAM_MODEL_DEVICE", "") or "").strip()
        self.default_prompt = str(self.config.get("LOCAL_REGION_PROPOSAL_TEXT_PROMPT", "") or "").strip()
        self.box_threshold = float(self.config.get("LOCAL_REGION_PROPOSAL_BOX_THRESHOLD", 0.28))
        self.text_threshold = float(self.config.get("LOCAL_REGION_PROPOSAL_TEXT_THRESHOLD", 0.2))
        self.nms_threshold = float(self.config.get("LOCAL_REGION_PROPOSAL_NMS_THRESHOLD", 0.55))
        self.max_regions = int(self.config.get("LOCAL_REGION_PROPOSAL_MAX_REGIONS", 8))

    def propose_regions(self, image_path: str, prompt: str | None = None) -> dict[str, Any]:
        if not image_path:
            raise ValueError("图片路径不存在")
        if not os.path.exists(image_path):
            raise FileNotFoundError("图片文件不存在")

        errors: list[str] = []
        regions: list[dict[str, Any]] = []

        if self.model_path:
            try:
                regions = self._propose_with_grounding_dino(image_path, prompt=prompt)
            except Exception as e:
                logger.warning("Grounding DINO region proposal failed: %s", e, exc_info=True)
                errors.append(f"Grounding DINO 不可用: {e}")

        if regions:
            backend = "grounding_dino"
            if self.sam_model_path:
                try:
                    regions = self._refine_with_sam(image_path, regions)
                    backend = "grounding_dino+sam"
                except Exception as e:
                    logger.warning("SAM refinement failed: %s", e, exc_info=True)
                    errors.append(f"SAM 精修不可用: {e}")
            return {
                "backend": backend,
                "prompt_labels": self._parse_prompt_labels(prompt),
                "proposals": regions,
            }

        if not self.model_path:
            raise ValueError("未配置 Grounding DINO 菜区提议模型")

        if errors:
            raise RuntimeError("；".join(errors))
        return {
            "backend": "grounding_dino",
            "prompt_labels": self._parse_prompt_labels(prompt),
            "proposals": [],
        }

    def _propose_with_grounding_dino(self, image_path: str, prompt: str | None = None) -> list[dict[str, Any]]:
        try:
            import torch
            from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
        except Exception as e:
            raise RuntimeError("未安装 Grounding DINO 所需依赖 transformers/torch") from e

        labels = self._parse_prompt_labels(prompt)
        if not labels:
            labels = list(_DEFAULT_PROMPTS)

        processor, model, device = self._load_grounding_dino(AutoProcessor, AutoModelForZeroShotObjectDetection, torch)

        with Image.open(image_path) as image:
            rgb = image.convert("RGB")
            width, height = rgb.size
            inputs = processor(images=rgb, text=[labels], return_tensors="pt")
            if hasattr(inputs, "to"):
                inputs = inputs.to(device)
            else:
                for key, value in list(inputs.items()):
                    if hasattr(value, "to"):
                        inputs[key] = value.to(device)

            with torch.no_grad():
                outputs = model(**inputs)

            results = processor.post_process_grounded_object_detection(
                outputs,
                inputs.input_ids,
                threshold=self.box_threshold,
                text_threshold=self.text_threshold,
                target_sizes=[(height, width)],
            )

        if not results:
            return []

        result = results[0]
        boxes = result.get("boxes")
        scores = result.get("scores")
        labels_out = result.get("labels")
        if boxes is None or scores is None or labels_out is None:
            return []

        proposals: list[dict[str, Any]] = []
        for box, score, label in zip(boxes, scores, labels_out):
            if hasattr(box, "tolist"):
                box = box.tolist()
            if hasattr(score, "item"):
                score = score.item()

            x1, y1, x2, y2 = [int(round(float(v))) for v in box]
            left = max(0, min(x1, width - 1))
            top = max(0, min(y1, height - 1))
            right = max(left + 1, min(x2, width))
            bottom = max(top + 1, min(y2, height))
            if right - left < 24 or bottom - top < 24:
                continue

            proposals.append({
                "bbox": {"x1": left, "y1": top, "x2": right, "y2": bottom},
                "score": max(0.0, min(1.0, float(score))),
                "label": str(label),
                "source": "grounding_dino",
            })

        proposals = self._apply_nms(proposals)
        proposals.sort(key=lambda item: item.get("score", 0.0), reverse=True)
        limited = proposals[: self.max_regions]
        for index, item in enumerate(limited, start=1):
            item["index"] = index
        return limited

    def _load_grounding_dino(self, processor_cls, model_cls, torch_module) -> tuple[Any, Any, str]:
        device = self._resolve_device(torch_module)
        cache_key = (self.model_path, device)
        cached = _GROUNDING_CACHE.get(cache_key)
        if cached is not None:
            return cached

        with _CACHE_LOCK:
            cached = _GROUNDING_CACHE.get(cache_key)
            if cached is not None:
                return cached

            processor = processor_cls.from_pretrained(self.model_path)
            model = model_cls.from_pretrained(self.model_path)
            model.to(device)
            model.eval()
            loaded = (processor, model, device)
            _GROUNDING_CACHE[cache_key] = loaded
            return loaded

    def _resolve_device(self, torch_module) -> str:
        if self.device:
            return self.device
        return "cuda" if torch_module.cuda.is_available() else "cpu"

    def _resolve_sam_device(self, torch_module) -> str:
        if self.sam_device:
            return self.sam_device
        if self.device:
            return self.device
        return "cuda" if torch_module.cuda.is_available() else "cpu"

    def _refine_with_sam(self, image_path: str, proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not proposals or not self.sam_model_path:
            return proposals

        try:
            import numpy as np
            import torch
            from transformers import SamModel, SamProcessor
        except Exception as e:
            raise RuntimeError("未安装 SAM 所需依赖 transformers/torch") from e

        processor, model, device = self._load_sam(SamProcessor, SamModel, torch)

        with Image.open(image_path) as image:
            rgb = image.convert("RGB")
            width, height = rgb.size
            refined: list[dict[str, Any]] = []
            for proposal in proposals:
                bbox = proposal["bbox"]
                refined_bbox = self._refine_single_box_with_sam(
                    rgb,
                    bbox,
                    width,
                    height,
                    processor,
                    model,
                    device,
                    np,
                    torch,
                )
                refined.append({
                    **proposal,
                    "bbox": refined_bbox,
                    "source": "grounding_dino+sam",
                })
            return refined

    def _load_sam(self, processor_cls, model_cls, torch_module) -> tuple[Any, Any, str]:
        device = self._resolve_sam_device(torch_module)
        cache_key = (self.sam_model_path, device)
        cached = _SAM_CACHE.get(cache_key)
        if cached is not None:
            return cached

        with _CACHE_LOCK:
            cached = _SAM_CACHE.get(cache_key)
            if cached is not None:
                return cached

            processor = processor_cls.from_pretrained(self.sam_model_path)
            model = model_cls.from_pretrained(self.sam_model_path)
            model.to(device)
            model.eval()
            loaded = (processor, model, device)
            _SAM_CACHE[cache_key] = loaded
            return loaded

    def _refine_single_box_with_sam(
        self,
        rgb_image: Image.Image,
        bbox: dict[str, int],
        width: int,
        height: int,
        processor,
        model,
        device: str,
        np_module,
        torch_module,
    ) -> dict[str, int]:
        box_values = [[
            int(bbox["x1"]),
            int(bbox["y1"]),
            int(bbox["x2"]),
            int(bbox["y2"]),
        ]]
        inputs = processor(
            images=rgb_image,
            input_boxes=[box_values],
            return_tensors="pt",
        )
        for key, value in list(inputs.items()):
            if hasattr(value, "to"):
                inputs[key] = value.to(device)

        with torch_module.no_grad():
            outputs = model(**inputs, multimask_output=False)

        masks = processor.image_processor.post_process_masks(
            outputs.pred_masks.cpu(),
            inputs["original_sizes"].cpu(),
            inputs["reshaped_input_sizes"].cpu(),
        )
        if not masks:
            return bbox

        mask_tensor = masks[0]
        if hasattr(mask_tensor, "numpy"):
            mask_array = mask_tensor.numpy()
        else:
            mask_array = np_module.asarray(mask_tensor)
        mask_array = np_module.asarray(mask_array)
        if mask_array.ndim >= 4:
            mask_array = mask_array[0, 0]
        elif mask_array.ndim == 3:
            mask_array = mask_array[0]
        elif mask_array.ndim != 2:
            return bbox

        positive = np_module.argwhere(mask_array > 0)
        if positive.size == 0:
            return bbox

        y_min, x_min = positive.min(axis=0)
        y_max, x_max = positive.max(axis=0)
        left = max(0, min(int(x_min), width - 1))
        top = max(0, min(int(y_min), height - 1))
        right = max(left + 1, min(int(x_max) + 1, width))
        bottom = max(top + 1, min(int(y_max) + 1, height))
        if right - left < 24 or bottom - top < 24:
            return bbox
        return {"x1": left, "y1": top, "x2": right, "y2": bottom}

    def _parse_prompt_labels(self, prompt: str | None = None) -> list[str]:
        raw = (prompt or self.default_prompt or "").strip()
        if not raw:
            return []
        parts = [part.strip() for part in re.split(r"[\n,;，；]+", raw) if part.strip()]
        deduped: list[str] = []
        seen: set[str] = set()
        for item in parts:
            normalized = item.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(item)
        return deduped

    def _apply_nms(self, proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ordered = sorted(proposals, key=lambda item: item.get("score", 0.0), reverse=True)
        kept: list[dict[str, Any]] = []
        for proposal in ordered:
            if all(self._iou(proposal["bbox"], existing["bbox"]) < self.nms_threshold for existing in kept):
                kept.append(proposal)
        return kept

    def _iou(self, a: dict[str, int], b: dict[str, int]) -> float:
        left = max(int(a["x1"]), int(b["x1"]))
        top = max(int(a["y1"]), int(b["y1"]))
        right = min(int(a["x2"]), int(b["x2"]))
        bottom = min(int(a["y2"]), int(b["y2"]))
        if right <= left or bottom <= top:
            return 0.0

        intersection = float((right - left) * (bottom - top))
        a_area = float(max(1, int(a["x2"]) - int(a["x1"])) * max(1, int(a["y2"]) - int(a["y1"])))
        b_area = float(max(1, int(b["x2"]) - int(b["x1"])) * max(1, int(b["y2"]) - int(b["y1"])))
        union = a_area + b_area - intersection
        if union <= 0:
            return 0.0
        return intersection / union
