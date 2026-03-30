import json
import logging
import os
import tempfile
from typing import Any

import numpy as np
from PIL import Image

from app.models import Dish, DishSampleImage, EmbeddingStatusEnum
from app.services.local_model_manager import is_local_model_ready
from app.services.qwen3_vl_local_wrappers import Qwen3VLEmbedder, Qwen3VLReranker
from app.services.region_proposal import RegionProposalService
from app.services.runtime_config import get_effective_config

logger = logging.getLogger(__name__)


class LocalEmbeddingIndexService:
    MATRIX_FILENAME = "dish_sample_embeddings.npy"
    METADATA_FILENAME = "dish_sample_metadata.json"

    def __init__(self, config: dict):
        self.config = get_effective_config(config)
        self.embedding_model_path = self.config.get("LOCAL_QWEN3_VL_EMBEDDING_MODEL_PATH", "")
        self.reranker_model_path = self.config.get("LOCAL_QWEN3_VL_RERANKER_MODEL_PATH", "")
        self.embedding_instruction = self.config.get("LOCAL_QWEN3_VL_EMBEDDING_INSTRUCTION", "")
        self.reranker_instruction = self.config.get(
            "LOCAL_QWEN3_VL_RERANKER_INSTRUCTION",
            "检索与当前餐盘菜区最相关的食堂菜品图片。",
        )
        self.index_dir = self.config.get("LOCAL_EMBEDDING_INDEX_DIR", "/data/images/embedding_index")
        self.similarity_threshold = float(self.config.get("LOCAL_EMBEDDING_SIMILARITY_THRESHOLD", 0.35))
        self.embedding_topk = int(self.config.get("LOCAL_EMBEDDING_TOPK", 5))
        self.rerank_topn = int(self.config.get("LOCAL_RERANK_TOPN", 5))
        self.rerank_score_threshold = float(self.config.get("LOCAL_RERANK_SCORE_THRESHOLD", 0.5))
        self.region_proposal_model_path = self.config.get("LOCAL_REGION_PROPOSAL_MODEL_PATH", "")
        self.sam_model_path = self.config.get("LOCAL_SAM_MODEL_PATH", "")
        self.max_regions = int(self.config.get("LOCAL_REGION_PROPOSAL_MAX_REGIONS", 8))
        self._embedder = None
        self._reranker = None
        self._index_matrix = None
        self._index_metadata = None
        self._index_cache_key = None

    def rebuild_index(self) -> dict[str, Any]:
        os.makedirs(self.index_dir, exist_ok=True)

        images = DishSampleImage.query.join(Dish).filter(
            Dish.is_active.is_(True),
            DishSampleImage.is_active.is_(True),
        ).order_by(DishSampleImage.dish_id.asc(), DishSampleImage.sort_order.asc()).all()

        if not images:
            self._write_index([], np.empty((0, 0), dtype=np.float32))
            return {"total": 0, "ready": 0, "failed": 0}

        records: list[dict[str, Any]] = []
        vectors = []
        failed = 0

        for image in images:
            image.embedding_status = EmbeddingStatusEnum.processing
            image.error_message = None

        from app import db
        db.session.commit()

        for image in images:
            try:
                if not image.image_path or not os.path.exists(image.image_path):
                    raise FileNotFoundError("样图文件不存在")

                vector = self.embed_image_file(image.image_path, instruction=None)
                vectors.append(vector.astype(np.float32))
                records.append({
                    "image_id": image.id,
                    "dish_id": image.dish_id,
                    "dish_name": image.dish.name if image.dish else "",
                    "image_path": image.image_path,
                    "original_filename": image.original_filename,
                })
                image.embedding_status = EmbeddingStatusEnum.ready
                image.embedding_model = os.path.basename(self.embedding_model_path) or "local_qwen3_vl_embedding"
                image.embedding_version = self.config.get("LOCAL_RECOGNITION_MODEL_VERSION", "local_embedding")
                image.error_message = None
            except Exception as e:
                failed += 1
                image.embedding_status = EmbeddingStatusEnum.failed
                image.error_message = str(e)[:255]
                logger.error("Failed to build embedding for sample image %s: %s", image.id, e)

        db.session.commit()

        matrix = np.vstack(vectors).astype(np.float32) if vectors else np.empty((0, 0), dtype=np.float32)
        self._write_index(records, matrix)
        return {
            "total": len(images),
            "ready": len(records),
            "failed": failed,
        }

    def recognize_dishes(self, image_path: str, candidate_dishes: list[dict]) -> dict[str, Any]:
        matrix, metadata = self._load_index()
        if matrix.size == 0 or not metadata:
            raise ValueError("本地 embedding 索引为空，请先上传样图并生成 embedding")

        candidate_ids = {
            int(item["id"])
            for item in candidate_dishes
            if item.get("id") is not None
        }
        regions = self.detect_regions(image_path) or [{"index": 1, "bbox": None, "source": "full_image"}]

        recognized = []
        raw_regions = []
        for region in regions:
            region_path, should_cleanup = self._materialize_region_image(image_path, region.get("bbox"))
            try:
                vector = self.embed_image_file(region_path, instruction=self.embedding_instruction or None)
                recall_hits = self._search_vector(vector, matrix, metadata, candidate_ids)
                reranked_hits = self._rerank_hits(region_path, recall_hits)
                final_hits = reranked_hits or recall_hits
            finally:
                if should_cleanup:
                    self._safe_unlink(region_path)

            raw_regions.append({
                "index": region.get("index"),
                "bbox": region.get("bbox"),
                "recall_hits": recall_hits[: self.embedding_topk],
                "reranked_hits": final_hits[: self.rerank_topn],
            })
            if not final_hits:
                continue

            best = final_hits[0]
            confidence = float(best.get("score", best.get("similarity", 0.0)) or 0.0)
            threshold = self.rerank_score_threshold if "score" in best else self.similarity_threshold
            if confidence < threshold:
                continue

            recognized.append({
                "name": best["dish_name"],
                "confidence": max(0.0, min(1.0, confidence)),
            })

        deduped = self._dedupe_results(recognized)
        return {
            "dishes": deduped,
            "notes": f"{self._build_region_backend_label()} local embedding 模式，区域数 {len(regions)}",
            "raw_response": {
                "mode": "local_embedding",
                "regions": raw_regions,
            },
            "model_version": self._build_model_version(),
        }

    def detect_regions(self, image_path: str) -> list[dict[str, Any]]:
        if not self.region_proposal_model_path:
            return []
        if not is_local_model_ready(self.region_proposal_model_path):
            logger.info(
                "Skip region proposal because model is not downloaded yet: %s",
                self.region_proposal_model_path,
            )
            return []

        try:
            result = RegionProposalService(self.config).propose_regions(image_path)
        except Exception as e:
            logger.warning("Region proposal unavailable, fallback to full-image recognition: %s", e)
            return []
        proposals = result.get("proposals", [])
        regions = []
        for idx, item in enumerate(proposals[: self.max_regions], start=1):
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
                "source": str(item.get("source") or result.get("backend") or "grounding_dino"),
            })

        regions.sort(key=lambda item: item.get("confidence", 0.0), reverse=True)
        return regions[: self.max_regions]

    def embed_image_file(self, image_path: str, instruction: str | None = None) -> np.ndarray:
        embedder = self._get_embedder()
        payload = {"image": image_path}
        if instruction:
            payload["instruction"] = instruction
        result = embedder.process([payload])
        return self._to_numpy_vector(result)

    def _search_vector(
        self,
        vector: np.ndarray,
        matrix: np.ndarray,
        metadata: list[dict[str, Any]],
        candidate_ids: set[int],
    ) -> list[dict[str, Any]]:
        if matrix.size == 0:
            return []

        similarities = matrix @ self._normalize(vector)
        order = np.argsort(similarities)[::-1]
        hits = []
        for idx in order:
            item = metadata[int(idx)]
            if candidate_ids and int(item["dish_id"]) not in candidate_ids:
                continue
            hits.append({
                "image_id": item["image_id"],
                "dish_id": item["dish_id"],
                "dish_name": item["dish_name"],
                "similarity": float(similarities[int(idx)]),
                "original_filename": item.get("original_filename"),
                "image_path": item.get("image_path"),
            })
            if len(hits) >= self.embedding_topk:
                break
        return hits

    def _rerank_hits(self, query_image_path: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not hits or not self.reranker_model_path:
            return hits

        reranker = self._get_reranker()
        top_hits = hits[: self.rerank_topn]
        inputs = {
            "instruction": self.reranker_instruction,
            "query": {"image": query_image_path},
            "documents": [
                {"text": hit["dish_name"], "image": hit["image_path"]}
                for hit in top_hits
            ],
        }
        scores = reranker.process(inputs)
        normalized_scores = self._coerce_scores(scores, len(top_hits))
        reranked = []
        for hit, score in zip(top_hits, normalized_scores):
            reranked.append({
                **hit,
                "score": max(0.0, min(1.0, float(score))),
            })
        reranked.sort(key=lambda item: item["score"], reverse=True)
        return reranked

    def _dedupe_results(self, dishes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        best_by_name: dict[str, dict[str, Any]] = {}
        for dish in dishes:
            name = (dish.get("name") or "").strip()
            confidence = float(dish.get("confidence", 0.0) or 0.0)
            if not name:
                continue
            current = best_by_name.get(name)
            if current is None or confidence > float(current.get("confidence", 0.0) or 0.0):
                best_by_name[name] = {"name": name, "confidence": confidence}
        return sorted(best_by_name.values(), key=lambda item: item["confidence"], reverse=True)

    def _get_embedder(self):
        if self._embedder is not None:
            return self._embedder

        if not self.embedding_model_path:
            raise ValueError("未配置 LOCAL_QWEN3_VL_EMBEDDING_MODEL_PATH")
        self._embedder = Qwen3VLEmbedder(model_name_or_path=self.embedding_model_path)
        return self._embedder

    def _get_reranker(self):
        if self._reranker is not None:
            return self._reranker

        if not self.reranker_model_path:
            raise ValueError("未配置 LOCAL_QWEN3_VL_RERANKER_MODEL_PATH")
        self._reranker = Qwen3VLReranker(model_name_or_path=self.reranker_model_path)
        return self._reranker

    def _load_index(self) -> tuple[np.ndarray, list[dict[str, Any]]]:
        matrix_path = os.path.join(self.index_dir, self.MATRIX_FILENAME)
        metadata_path = os.path.join(self.index_dir, self.METADATA_FILENAME)
        if not os.path.exists(matrix_path) or not os.path.exists(metadata_path):
            return np.empty((0, 0), dtype=np.float32), []

        cache_key = (os.path.getmtime(matrix_path), os.path.getmtime(metadata_path))
        if self._index_cache_key == cache_key and self._index_matrix is not None and self._index_metadata is not None:
            return self._index_matrix, self._index_metadata

        matrix = np.load(matrix_path)
        with open(metadata_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)

        self._index_cache_key = cache_key
        self._index_matrix = matrix
        self._index_metadata = metadata
        return matrix, metadata

    def _write_index(self, metadata: list[dict[str, Any]], matrix: np.ndarray):
        os.makedirs(self.index_dir, exist_ok=True)
        matrix_path = os.path.join(self.index_dir, self.MATRIX_FILENAME)
        metadata_path = os.path.join(self.index_dir, self.METADATA_FILENAME)
        normalized_matrix = matrix if matrix.size == 0 else np.vstack([self._normalize(v) for v in matrix])
        np.save(matrix_path, normalized_matrix.astype(np.float32))
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
        self._index_cache_key = None
        self._index_matrix = None
        self._index_metadata = None

    def _normalize(self, vector: np.ndarray) -> np.ndarray:
        vector = np.asarray(vector, dtype=np.float32).reshape(-1)
        norm = np.linalg.norm(vector)
        if norm <= 1e-12:
            return vector
        return vector / norm

    def _materialize_region_image(self, image_path: str, bbox: dict[str, int] | None) -> tuple[str, bool]:
        if not bbox:
            return image_path, False

        with Image.open(image_path) as image:
            crop = image.convert("RGB").crop((
                int(bbox["x1"]),
                int(bbox["y1"]),
                int(bbox["x2"]),
                int(bbox["y2"]),
            ))
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                crop.save(tmp.name, format="JPEG", quality=95)
                return tmp.name, True

    def _safe_unlink(self, path: str):
        try:
            os.unlink(path)
        except OSError:
            pass

    def _to_numpy_vector(self, result: Any) -> np.ndarray:
        if hasattr(result, "detach"):
            result = result.detach().cpu().numpy()
        elif hasattr(result, "cpu") and hasattr(result, "numpy"):
            result = result.cpu().numpy()

        array = np.asarray(result, dtype=np.float32)
        if array.ndim == 2:
            return array[0]
        if array.ndim == 1:
            return array
        raise ValueError(f"Unexpected embedding output shape: {array.shape}")

    def _coerce_scores(self, scores: Any, expected: int) -> list[float]:
        if hasattr(scores, "detach"):
            scores = scores.detach().cpu().numpy()
        elif hasattr(scores, "cpu") and hasattr(scores, "numpy"):
            scores = scores.cpu().numpy()

        if isinstance(scores, np.ndarray):
            flat = scores.astype(np.float32).reshape(-1).tolist()
        elif isinstance(scores, list):
            flat = [float(item) for item in scores]
        else:
            flat = [float(scores)]

        if len(flat) < expected:
            flat.extend([0.0] * (expected - len(flat)))
        return flat[:expected]

    def _build_model_version(self) -> str:
        parts = []
        if self.region_proposal_model_path:
            parts.append("grounding_dino")
        if self.sam_model_path:
            parts.append("sam")
        parts.append("qwen3_vl_embedding")
        if self.reranker_model_path:
            parts.append("reranker")
        return self.config.get("LOCAL_RECOGNITION_MODEL_VERSION", "+".join(parts))

    def _build_region_backend_label(self) -> str:
        if self.region_proposal_model_path and self.sam_model_path:
            return "grounding_dino+sam"
        if self.region_proposal_model_path:
            return "grounding_dino"
        return "full_image"
