import logging
import os
import time
from threading import Lock
from typing import Any

import numpy as np
from PIL import Image

from app.services.local_embedding import LocalEmbeddingIndexService

logger = logging.getLogger(__name__)


def _elapsed_ms(started: float) -> int:
    return int(round((time.perf_counter() - started) * 1000))


_MODEL_CACHE: dict[tuple[str, str], Any] = {}
_MODEL_LOCK = Lock()


class VisualFeatureExtractor:
    """SigLIP2 + DINOv3 feature extractor used by the pure-vision pipeline."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.siglip_path = str(config.get("LOCAL_SIGLIP2_MODEL_PATH") or "")
        self.dino_path = str(config.get("LOCAL_DINOV3_MODEL_PATH") or "")
        self.max_patch_tokens = max(16, int(config.get("VISUAL_PATCH_MAX_TOKENS", 256)))
        self.device = str(config.get("VISUAL_DEVICE") or "").strip()

    @staticmethod
    def _normalize(vector: np.ndarray, axis: int = -1) -> np.ndarray:
        vector = np.asarray(vector, dtype=np.float32)
        norm = np.linalg.norm(vector, axis=axis, keepdims=True)
        return vector / np.maximum(norm, 1e-12)

    def _load_model(self, kind: str, path: str):
        if not path:
            raise ValueError(f"未配置 {kind} 模型路径")
        key = (kind, os.path.abspath(path))
        cached = _MODEL_CACHE.get(key)
        if cached is not None:
            return cached
        with _MODEL_LOCK:
            cached = _MODEL_CACHE.get(key)
            if cached is not None:
                return cached
            import torch
            from transformers import AutoImageProcessor, AutoModel, AutoProcessor

            processor = AutoProcessor.from_pretrained(path) if kind == "siglip2" else AutoImageProcessor.from_pretrained(path)
            dtype = torch.float16 if torch.cuda.is_available() else torch.float32
            model = AutoModel.from_pretrained(path, torch_dtype=dtype)
            device = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
            model = model.to(device).eval()
            cached = (processor, model, device)
            _MODEL_CACHE[key] = cached
            return cached

    def _compress_patches(self, patches: np.ndarray) -> np.ndarray:
        if patches.shape[0] <= self.max_patch_tokens:
            return patches
        side = int(round(patches.shape[0] ** 0.5))
        target_side = max(1, int(self.max_patch_tokens ** 0.5))
        if side * side != patches.shape[0]:
            indices = np.linspace(0, patches.shape[0] - 1, self.max_patch_tokens).astype(int)
            return patches[indices]
        import torch
        import torch.nn.functional as functional

        tensor = torch.from_numpy(patches).T.reshape(1, patches.shape[1], side, side)
        pooled = functional.adaptive_avg_pool2d(tensor, (target_side, target_side))
        return pooled.reshape(patches.shape[1], -1).T.numpy()

    @staticmethod
    def _select_dino_patch_tokens(model, hidden, pixel_values):
        """Remove class/register tokens while supporting native and timm DINOv3."""
        if len(hidden.shape) != 3:
            raise ValueError(f"DINOv3 输出维度异常: {tuple(hidden.shape)}")

        timm_model = getattr(model, "timm_model", None)
        patch_embed = getattr(timm_model, "patch_embed", None)
        patch_size = getattr(patch_embed, "patch_size", None)
        if patch_size and pixel_values is not None and len(pixel_values.shape) == 4:
            if isinstance(patch_size, (tuple, list)):
                patch_height, patch_width = int(patch_size[0]), int(patch_size[1])
            else:
                patch_height = patch_width = int(patch_size)
            image_height, image_width = int(pixel_values.shape[-2]), int(pixel_values.shape[-1])
            patch_count = (image_height // patch_height) * (image_width // patch_width)
            if 0 < patch_count <= hidden.shape[1]:
                return hidden[:, hidden.shape[1] - patch_count:, :]

        register_count = int(getattr(model.config, "num_register_tokens", 0) or 0)
        patches = hidden[:, 1 + register_count:, :]
        if patches.shape[1] <= 0:
            raise ValueError("DINOv3 输出中未找到 patch token")
        return patches

    @classmethod
    def _extract_dino_patch_tokens(cls, model, inputs):
        forward_inputs = dict(inputs)
        if hasattr(model, "timm_model"):
            # Patch MaxSim only needs the token map, so skip TimmWrapper's extra pooled output.
            forward_inputs["do_pooling"] = False
        output = model(**forward_inputs)
        return cls._select_dino_patch_tokens(model, output.last_hidden_state, inputs.get("pixel_values"))

    def extract(self, image_path: str) -> tuple[np.ndarray, np.ndarray]:
        import torch

        image = Image.open(image_path).convert("RGB")
        siglip_processor, siglip_model, siglip_device = self._load_model("siglip2", self.siglip_path)
        dino_processor, dino_model, dino_device = self._load_model("dinov3", self.dino_path)
        with torch.inference_mode():
            siglip_inputs = siglip_processor(images=image, return_tensors="pt")
            siglip_inputs = {key: value.to(siglip_device) for key, value in siglip_inputs.items()}
            if hasattr(siglip_model, "get_image_features"):
                siglip_output = siglip_model.get_image_features(**siglip_inputs)
                if hasattr(siglip_output, "pooler_output"):
                    siglip_output = siglip_output.pooler_output
            else:
                siglip_output = siglip_model(**siglip_inputs).pooler_output

            dino_inputs = dino_processor(images=image, return_tensors="pt")
            dino_inputs = {key: value.to(dino_device) for key, value in dino_inputs.items()}
            patches = self._extract_dino_patch_tokens(dino_model, dino_inputs)
            dino_global = patches.mean(dim=1)

        siglip_vector = self._normalize(siglip_output[0].float().cpu().numpy())
        dino_vector = self._normalize(dino_global[0].float().cpu().numpy())
        global_vector = self._normalize(np.concatenate([siglip_vector, dino_vector]))
        patch_vectors = patches[0].float().cpu().numpy()
        patch_vectors = self._compress_patches(patch_vectors)
        return global_vector.astype(np.float32), self._normalize(patch_vectors).astype(np.float32)


class VisualEmbeddingIndexService(LocalEmbeddingIndexService):
    MATRIX_FILENAME = "visual_global_embeddings.npy"
    METADATA_FILENAME = "visual_metadata.json"
    PATCH_FILENAME = "visual_patch_embeddings.npy"

    @staticmethod
    def _confidence_from_maxsim(raw_score: float) -> float:
        return max(0.0, min(1.0, (float(raw_score) + 1.0) / 2.0))

    def __init__(self, config: dict):
        super().__init__(config)
        self.index_dir = os.path.join(self.index_dir, "visual")
        self.embedding_topk = int(self.config.get("VISUAL_RECALL_TOPK", 50))
        self.visual_patch_topn = int(self.config.get("VISUAL_PATCH_TOPN", 10))
        self.visual_maxsim_threshold = float(self.config.get("VISUAL_MAXSIM_THRESHOLD", 0.5))
        if not self.rerank_enabled:
            self.rerank_score_threshold = self._confidence_from_maxsim(self.visual_maxsim_threshold)
        self._visual_extractor = None
        self._query_patches: dict[str, np.ndarray] = {}
        self._patch_matrix = None
        self._patch_cache_mtime = None

    def extra_index_filenames(self) -> list[str]:
        return [self.PATCH_FILENAME]

    def _get_visual_extractor(self) -> VisualFeatureExtractor:
        if self._visual_extractor is None:
            self._visual_extractor = VisualFeatureExtractor(self.config)
        return self._visual_extractor

    def embed_image_features(self, image_path: str) -> tuple[np.ndarray, np.ndarray]:
        return self._get_visual_extractor().extract(image_path)

    def embed_image_file(self, image_path: str, instruction: str | None = None) -> np.ndarray:
        vector, _ = self.embed_image_features(image_path)
        return vector

    def embed_regions(self, image_path: str, *, bboxes=None, instruction=None, region_sources=None):
        import time
        bbox_list = bboxes if bboxes else [None]
        embedded = []
        try:
            for idx, bbox in enumerate(bbox_list, start=1):
                started = time.perf_counter()
                region_path, cleanup = self._materialize_region_image(image_path, bbox)
                source = region_sources[idx - 1] if region_sources and idx - 1 < len(region_sources) else {}
                vector, patches = self.embed_image_features(region_path)
                self._query_patches[region_path] = patches
                embedded.append({
                    "index": int(source.get("index") or idx),
                    "bbox": bbox,
                    "vector": vector,
                    "patch_vectors": patches,
                    "region_path": region_path,
                    "should_cleanup": cleanup,
                    "source": str(source.get("source") or ("full_image" if bbox is None else "provided_bbox")),
                    "timings_ms": {"embed": _elapsed_ms(started), "total": _elapsed_ms(started)},
                })
            return embedded
        except Exception:
            for item in embedded:
                self._query_patches.pop(item["region_path"], None)
                if item.get("should_cleanup"):
                    self._safe_unlink(item["region_path"])
            raise

    def _load_patch_matrix(self) -> np.ndarray:
        path = os.path.join(self.index_dir, self.PATCH_FILENAME)
        if not os.path.exists(path):
            return np.empty((0, 0), dtype=np.float16)
        mtime = os.path.getmtime(path)
        if self._patch_matrix is None or self._patch_cache_mtime != mtime:
            self._patch_matrix = np.load(path, mmap_mode="r")
            self._patch_cache_mtime = mtime
        return self._patch_matrix

    @staticmethod
    def _maxsim(query: np.ndarray, candidate: np.ndarray) -> float:
        similarities = np.asarray(query, dtype=np.float32) @ np.asarray(candidate, dtype=np.float32).T
        return float((similarities.max(axis=1).mean() + similarities.max(axis=0).mean()) / 2.0)

    def _rerank_hits(self, query_image_path: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        query = self._query_patches.pop(query_image_path, None)
        patch_matrix = self._load_patch_matrix()
        if query is None or patch_matrix.size == 0:
            return hits
        reranked = []
        for hit in hits:
            offset = int(hit.get("patch_offset", 0) or 0)
            count = int(hit.get("patch_count", 0) or 0)
            if count <= 0:
                continue
            raw_score = self._maxsim(query, patch_matrix[offset:offset + count])
            reranked.append({**hit, "maxsim_score": raw_score, "score": self._confidence_from_maxsim(raw_score)})
        reranked.sort(key=lambda item: item["maxsim_score"], reverse=True)
        reranked = reranked[:self.visual_patch_topn]
        if self.rerank_enabled and self.reranker_model_path:
            return super()._rerank_hits(query_image_path, reranked)
        return reranked

    def _search_vector(self, vector, matrix, metadata, candidate_ids):
        hits = super()._search_vector(vector, matrix, metadata, candidate_ids)
        metadata_by_image = {int(item["image_id"]): item for item in metadata}
        return [{**hit, **{
            "patch_offset": metadata_by_image[int(hit["image_id"])].get("patch_offset", 0),
            "patch_count": metadata_by_image[int(hit["image_id"])].get("patch_count", 0),
        }} for hit in hits]

    def _build_model_version(self) -> str:
        suffix = "+qwen_reranker" if self.rerank_enabled and self.reranker_model_path else ""
        return f"siglip2+dinov3_timm_vitb16_lvd1689m+maxsim{suffix}"

    def analyze_regions(self, image_path: str, candidate_dishes: list[dict], regions: list[dict[str, Any]]) -> dict[str, Any]:
        result = super().analyze_regions(image_path, candidate_dishes, regions)
        raw_response = dict(result.get("raw_response") or {})
        raw_response["pipeline"] = "visual"
        result["raw_response"] = raw_response
        return result
