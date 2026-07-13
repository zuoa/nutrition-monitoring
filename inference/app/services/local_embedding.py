import json
import logging
import os
import tempfile
import time
from threading import Lock
from typing import Any

import numpy as np
from PIL import Image

from app.services.inference_client import InferenceServiceError, make_detector_client
from app.services.qwen3_vl_local_wrappers import Qwen3VLEmbedder, Qwen3VLReranker
from app.services.runtime_config import get_effective_config

try:
    from app.models import Dish, DishSampleImage, EmbeddingStatusEnum
except ModuleNotFoundError:
    Dish = None
    DishSampleImage = None
    EmbeddingStatusEnum = None

logger = logging.getLogger(__name__)

_EMBEDDER_CACHE: dict[str, Any] = {}
_RERANKER_CACHE: dict[str, Any] = {}
_MODEL_CACHE_LOCK = Lock()


def _elapsed_ms(started: float) -> int:
    return int(round((time.perf_counter() - started) * 1000))


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class LocalEmbeddingIndexService:
    MATRIX_FILENAME = "dish_sample_embeddings.npy"
    METADATA_FILENAME = "dish_sample_metadata.json"

    def __init__(self, config: dict):
        self.config = get_effective_config(config)
        self.embedding_model_path = self.config.get("LOCAL_QWEN3_VL_EMBEDDING_MODEL_PATH", "")
        self.reranker_model_path = self.config.get("LOCAL_QWEN3_VL_RERANKER_MODEL_PATH", "")
        self.embedding_precision = str(self.config.get("LOCAL_QWEN3_VL_EMBEDDING_PRECISION", "auto") or "auto")
        self.reranker_precision = str(self.config.get("LOCAL_QWEN3_VL_RERANKER_PRECISION", "auto") or "auto")
        self.embedding_instruction = self.config.get("LOCAL_QWEN3_VL_EMBEDDING_INSTRUCTION", "")
        self.reranker_instruction = self.config.get(
            "LOCAL_QWEN3_VL_RERANKER_INSTRUCTION",
            "检索与当前餐盘菜区最相关的食堂菜品图片。",
        )
        self.index_dir = self.config.get("LOCAL_EMBEDDING_INDEX_DIR", "/data/images/embedding_index")
        self.similarity_threshold = float(self.config.get("LOCAL_EMBEDDING_SIMILARITY_THRESHOLD", 0.35))
        self.embedding_topk = int(self.config.get("LOCAL_EMBEDDING_TOPK", 5))
        self.embedding_batch_size = max(1, int(self.config.get("LOCAL_EMBEDDING_BATCH_SIZE", 8)))
        self.embedding_max_pixels = max(4096, int(self.config.get("LOCAL_EMBEDDING_MAX_PIXELS", 786432)))
        self.rerank_enabled = _as_bool(self.config.get("LOCAL_RERANK_ENABLED"), default=True)
        self.rerank_topn = int(self.config.get("LOCAL_RERANK_TOPN", 3))
        self.rerank_score_threshold = float(self.config.get("LOCAL_RERANK_SCORE_THRESHOLD", 0.5))
        self.max_regions = int(self.config.get("YOLO_MAX_REGIONS", 6))
        self._embedder = None
        self._reranker = None
        self._index_matrix = None
        self._index_metadata = None
        self._index_cache_key = None
        self._last_region_backend = "full_image"

    def rebuild_index(self) -> dict[str, Any]:
        if Dish is None or DishSampleImage is None or EmbeddingStatusEnum is None:
            raise RuntimeError("样图索引重建依赖 backend 数据模型，不在 inference 服务内执行")
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

                vector = self.embed_image_file(
                    image.image_path,
                    instruction=self.embedding_instruction or None,
                )
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
                image.embedding_version = self._build_model_version()
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
        regions = self.detect_regions(image_path) or [{"index": 1, "bbox": None, "source": "full_image"}]
        return self.analyze_regions(image_path, candidate_dishes, regions)

    def analyze_regions(
        self,
        image_path: str,
        candidate_dishes: list[dict],
        regions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        total_started = time.perf_counter()
        self._last_region_backend = self._resolve_region_backend(regions)
        load_index_started = time.perf_counter()
        matrix, metadata = self._load_index()
        load_index_ms = _elapsed_ms(load_index_started)
        if matrix.size == 0 or not metadata:
            raise ValueError("本地 embedding 索引为空，请先上传样图并生成 embedding")

        candidate_ids = {
            int(item["id"])
            for item in candidate_dishes
            if item.get("id") is not None
        }
        logger.debug(
            "Analyze local embedding regions: image=%s candidate_count=%s region_count=%s index_size=%s backend=%s",
            os.path.basename(image_path or ""),
            len(candidate_dishes),
            len(regions),
            len(metadata),
            self._last_region_backend,
        )

        recognized = []
        region_results = []
        missing_hit_regions = 0
        below_threshold_regions = 0
        embed_started = time.perf_counter()
        embedded_regions = self.embed_regions(
            image_path,
            bboxes=[region.get("bbox") for region in regions],
            instruction=self.embedding_instruction or None,
            region_sources=regions,
        )
        embed_ms = _elapsed_ms(embed_started)
        search_ms = 0
        rerank_ms = 0
        for embedded in embedded_regions:
            region_path = embedded["region_path"]
            should_cleanup = embedded["should_cleanup"]
            vector = embedded["vector"]
            embedded_timings = dict(embedded.get("timings_ms") or {})
            try:
                search_started = time.perf_counter()
                recall_hits = self._search_vector(vector, matrix, metadata, candidate_ids)
                region_search_ms = _elapsed_ms(search_started)
                search_ms += region_search_ms
                rerank_started = time.perf_counter()
                reranked_hits = self._rerank_hits(region_path, recall_hits)
                region_rerank_ms = _elapsed_ms(rerank_started)
                rerank_ms += region_rerank_ms
                final_hits = reranked_hits or recall_hits
            finally:
                if should_cleanup:
                    self._safe_unlink(region_path)

            region_timing_total = int(embedded_timings.get("total", 0)) + region_search_ms + region_rerank_ms
            accepted = False
            accepted_hit = None
            if final_hits:
                candidate_best = final_hits[0]
                candidate_confidence = float(candidate_best.get("score", candidate_best.get("similarity", 0.0)) or 0.0)
                candidate_threshold = self.rerank_score_threshold if "score" in candidate_best else self.similarity_threshold
                if candidate_confidence >= candidate_threshold:
                    accepted = True
                    accepted_hit = candidate_best

            region_results.append({
                "index": embedded["index"],
                "bbox": embedded["bbox"],
                "embedding_dim": int(vector.shape[0]),
                "recall_hits": recall_hits[: self.embedding_topk],
                "reranked_hits": final_hits[: self.rerank_topn],
                "accepted": accepted,
                "accepted_hit": accepted_hit,
                "timings_ms": {
                    **embedded_timings,
                    "search": region_search_ms,
                    "rerank": region_rerank_ms,
                    "total": region_timing_total,
                },
            })
            if not final_hits:
                missing_hit_regions += 1
                continue

            best = final_hits[0]
            confidence = float(best.get("score", best.get("similarity", 0.0)) or 0.0)
            threshold = self.rerank_score_threshold if "score" in best else self.similarity_threshold
            if confidence < threshold:
                below_threshold_regions += 1
                continue

            recognized.append({
                "name": best["dish_name"],
                "confidence": max(0.0, min(1.0, confidence)),
                "bbox": embedded.get("bbox"),
                "bbox_source": "pixels" if embedded.get("bbox") is not None else "",
                "position": "",
                "notes": self._build_region_note(
                    embedded=embedded,
                    best_hit=best,
                    final_hits=final_hits,
                ),
            })

        deduped = self._dedupe_results(recognized)
        return {
            "dishes": deduped,
            "notes": self._build_analysis_note(
                region_count=len(regions),
                missing_hit_regions=missing_hit_regions,
                below_threshold_regions=below_threshold_regions,
                recognized_before_dedupe=len(recognized),
                unique_dish_count=len(deduped),
            ),
            "raw_response": {
                "mode": "local_embedding",
                "regions": region_results,
            },
            "region_results": region_results,
            "model_version": self._build_model_version(),
            "timings_ms": {
                "load_index": load_index_ms,
                "embed": embed_ms,
                "search": search_ms,
                "rerank": rerank_ms,
                "total": _elapsed_ms(total_started),
            },
        }

    def detect_regions(self, image_path: str) -> list[dict[str, Any]]:
        try:
            result = make_detector_client(self.config).post_file(
                "/v1/detect",
                image_path=image_path,
                data={"max_regions": self.max_regions},
            )
        except (InferenceServiceError, ValueError, FileNotFoundError) as e:
            logger.warning("Detector unavailable, fallback to full-image recognition: %s", e)
            self._last_region_backend = "full_image"
            return []
        proposals = result.get("proposals", [])
        if not proposals:
            proposals = result.get("regions", [])
        backend = str(result.get("backend") or "detector")
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
                "source": str(item.get("source") or backend),
            })

        regions.sort(key=lambda item: item.get("confidence", 0.0), reverse=True)
        limited = regions[: self.max_regions]
        self._last_region_backend = backend if limited else "full_image"
        return limited

    def embed_image_file(self, image_path: str, instruction: str | None = None) -> np.ndarray:
        embedder = self._get_embedder()
        payload = {"image": image_path}
        if instruction:
            payload["instruction"] = instruction
        result = embedder.process([payload])
        return self._to_numpy_vector(result)

    def embed_regions(
        self,
        image_path: str,
        *,
        bboxes: list[dict[str, int] | None] | None = None,
        instruction: str | None = None,
        region_sources: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        bbox_list = bboxes if bboxes is not None else [None]
        if not bbox_list:
            bbox_list = [None]

        materialized = []
        try:
            for idx, bbox in enumerate(bbox_list, start=1):
                region_started = time.perf_counter()
                materialize_started = time.perf_counter()
                region_path, should_cleanup = self._materialize_region_image(image_path, bbox)
                source = region_sources[idx - 1] if region_sources and idx - 1 < len(region_sources) else {}
                materialized.append({
                    "index": int(source.get("index") or idx),
                    "bbox": bbox,
                    "region_path": region_path,
                    "should_cleanup": should_cleanup,
                    "source": str(source.get("source") or ("full_image" if bbox is None else "provided_bbox")),
                    "materialize_ms": _elapsed_ms(materialize_started),
                    "region_started": region_started,
                })

            # Keep the single-region path compatible and avoid unnecessary matrix
            # handling. Multi-region images are encoded in GPU batches instead of
            # invoking the 2B embedding model once per crop.
            if len(materialized) == 1:
                item = materialized[0]
                embed_started = time.perf_counter()
                vectors = [self.embed_image_file(item["region_path"], instruction=instruction)]
                embed_times = [_elapsed_ms(embed_started)]
            else:
                embedder = self._get_embedder()
                vectors = []
                embed_times = []
                for offset in range(0, len(materialized), self.embedding_batch_size):
                    batch = materialized[offset:offset + self.embedding_batch_size]
                    payloads = []
                    for item in batch:
                        payload = {"image": item["region_path"]}
                        if instruction:
                            payload["instruction"] = instruction
                        payloads.append(payload)
                    embed_started = time.perf_counter()
                    try:
                        batch_result = self._to_numpy_array(embedder.process(payloads))
                        batch_matrix = np.asarray(batch_result, dtype=np.float32)
                        if batch_matrix.ndim == 1:
                            batch_matrix = batch_matrix.reshape(1, -1)
                        if batch_matrix.ndim != 2 or batch_matrix.shape[0] != len(batch):
                            raise ValueError(f"Unexpected batched embedding output shape: {batch_matrix.shape}")
                        batch_ms = _elapsed_ms(embed_started)
                        vectors.extend(batch_matrix)
                        embed_times.extend([batch_ms] * len(batch))
                    except Exception as batch_error:
                        logger.warning(
                            "Batched embedding failed for %s regions; retrying one by one: %s",
                            len(batch),
                            batch_error,
                        )
                        for item in batch:
                            single_started = time.perf_counter()
                            vectors.append(self.embed_image_file(item["region_path"], instruction=instruction))
                            embed_times.append(_elapsed_ms(single_started))

            embedded_regions = []
            for item, vector, embed_ms in zip(materialized, vectors, embed_times):
                embedded_regions.append({
                    "index": item["index"],
                    "bbox": item["bbox"],
                    "vector": np.asarray(vector, dtype=np.float32),
                    "region_path": item["region_path"],
                    "should_cleanup": item["should_cleanup"],
                    "source": item["source"],
                    "timings_ms": {
                        "materialize": item["materialize_ms"],
                        "embed": embed_ms,
                        "total": _elapsed_ms(item["region_started"]),
                    },
                })
            return embedded_regions
        except Exception:
            for item in materialized:
                if item.get("should_cleanup"):
                    self._safe_unlink(item["region_path"])
            raise

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
            similarity = float(similarities[int(idx)])
            if similarity < self.similarity_threshold:
                break
            item = metadata[int(idx)]
            if candidate_ids and int(item["dish_id"]) not in candidate_ids:
                continue
            hits.append({
                "image_id": item["image_id"],
                "dish_id": item["dish_id"],
                "dish_name": item["dish_name"],
                "similarity": similarity,
                "original_filename": item.get("original_filename"),
                "image_path": item.get("image_path"),
            })
            if len(hits) >= self.embedding_topk:
                break
        return hits

    def _rerank_hits(self, query_image_path: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not hits or not self.rerank_enabled or not self.reranker_model_path:
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
        logger.debug(
            "Run reranker: query=%s top_hit_count=%s hits=%s model=%s",
            os.path.basename(query_image_path or ""),
            len(top_hits),
            self._summarize_hits(top_hits),
            os.path.basename(self.reranker_model_path or ""),
        )
        try:
            scores = reranker.process(inputs)
        except Exception:
            logger.exception(
                "Reranker process failed: query=%s top_hit_count=%s hits=%s model=%s",
                os.path.basename(query_image_path or ""),
                len(top_hits),
                self._summarize_hits(top_hits),
                os.path.basename(self.reranker_model_path or ""),
            )
            raise
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
                best_by_name[name] = {
                    **dish,
                    "name": name,
                    "confidence": confidence,
                }
        return sorted(best_by_name.values(), key=lambda item: item["confidence"], reverse=True)

    def _build_region_note(
        self,
        *,
        embedded: dict[str, Any],
        best_hit: dict[str, Any],
        final_hits: list[dict[str, Any]],
    ) -> str:
        region_index = int(embedded.get("index") or 0)
        region_source = str(embedded.get("source") or "").strip() or "full_image"
        if embedded.get("bbox") is None:
            scope = "整图检索"
        else:
            scope = f"区域 {region_index}"

        score_key = "score" if "score" in best_hit else "similarity"
        score_label = "重排得分" if score_key == "score" else "相似度"
        score_value = float(best_hit.get(score_key, 0.0) or 0.0)

        alternatives = [
            str(hit.get("dish_name") or "").strip()
            for hit in final_hits[1:3]
            if str(hit.get("dish_name") or "").strip()
        ]
        note_parts = [
            f"{scope}，来源 {region_source}",
            f"{score_label} {score_value:.3f}",
        ]
        if alternatives:
            note_parts.append(f"备选 {', '.join(alternatives)}")
        return "；".join(note_parts)

    def _get_embedder(self):
        if self._embedder is not None:
            return self._embedder

        if not self.embedding_model_path:
            raise ValueError("未配置 LOCAL_QWEN3_VL_EMBEDDING_MODEL_PATH")
        cache_key = os.path.abspath(self.embedding_model_path)
        cached = _EMBEDDER_CACHE.get(cache_key)
        if cached is not None:
            self._embedder = cached
            return self._embedder

        with _MODEL_CACHE_LOCK:
            cached = _EMBEDDER_CACHE.get(cache_key)
            if cached is None:
                logger.info(
                    "Loading embedding model: path=%s precision=%s max_pixels=%s",
                    self.embedding_model_path,
                    self.embedding_precision,
                    self.embedding_max_pixels,
                )
                cached = Qwen3VLEmbedder(
                    model_name_or_path=self.embedding_model_path,
                    max_pixels=self.embedding_max_pixels,
                    precision=self.embedding_precision,
                )
                _EMBEDDER_CACHE[cache_key] = cached
            self._embedder = cached
        return self._embedder

    def _get_reranker(self):
        if self._reranker is not None:
            return self._reranker

        if not self.reranker_model_path:
            raise ValueError("未配置 LOCAL_QWEN3_VL_RERANKER_MODEL_PATH")
        cache_key = os.path.abspath(self.reranker_model_path)
        cached = _RERANKER_CACHE.get(cache_key)
        if cached is not None:
            self._reranker = cached
            return self._reranker

        with _MODEL_CACHE_LOCK:
            cached = _RERANKER_CACHE.get(cache_key)
            if cached is None:
                logger.info(
                    "Loading reranker model: path=%s precision=%s",
                    self.reranker_model_path,
                    self.reranker_precision,
                )
                cached = Qwen3VLReranker(
                    model_name_or_path=self.reranker_model_path,
                    precision=self.reranker_precision,
                )
                _RERANKER_CACHE[cache_key] = cached
            self._reranker = cached
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

    def _to_numpy_array(self, value: Any) -> Any:
        if hasattr(value, "detach"):
            value = value.detach()
        if hasattr(value, "cpu"):
            value = value.cpu()
        if hasattr(value, "float") and hasattr(value, "numpy"):
            value = value.float()
        if hasattr(value, "numpy"):
            value = value.numpy()
        return value

    def _to_numpy_vector(self, result: Any) -> np.ndarray:
        result = self._to_numpy_array(result)
        array = np.asarray(result, dtype=np.float32)
        if array.ndim == 2:
            return array[0]
        if array.ndim == 1:
            return array
        raise ValueError(f"Unexpected embedding output shape: {array.shape}")

    def _coerce_scores(self, scores: Any, expected: int) -> list[float]:
        scores = self._to_numpy_array(scores)
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
        embedding_label = "qwen3_vl_embedding"
        if self.embedding_precision.lower() != "auto":
            embedding_label += f"_{self.embedding_precision.lower()}"
        parts.append(embedding_label)
        if self.rerank_enabled and self.reranker_model_path:
            reranker_label = "reranker"
            if self.reranker_precision.lower() != "auto":
                reranker_label += f"_{self.reranker_precision.lower()}"
            parts.append(reranker_label)
        return "+".join(parts)

    def _build_region_backend_label(self) -> str:
        return self._last_region_backend

    def _build_analysis_note(
        self,
        *,
        region_count: int,
        missing_hit_regions: int,
        below_threshold_regions: int,
        recognized_before_dedupe: int,
        unique_dish_count: int,
    ) -> str:
        note_parts = [
            f"{self._build_region_backend_label()} local embedding 模式",
            f"检测到 {region_count} 个菜区",
        ]
        if missing_hit_regions > 0:
            note_parts.append(f"{missing_hit_regions} 个菜区没有召回到候选结果")
        if below_threshold_regions > 0:
            note_parts.append(f"{below_threshold_regions} 个菜区分数低于阈值")

        merged_duplicate_regions = max(0, recognized_before_dedupe - unique_dish_count)
        if merged_duplicate_regions > 0:
            note_parts.append(f"{merged_duplicate_regions} 个菜区命中重复菜名并被合并")

        note_parts.append(f"最终保留 {unique_dish_count} 个唯一菜品")
        return "；".join(note_parts)

    def _resolve_region_backend(self, regions: list[dict[str, Any]]) -> str:
        if not regions:
            return "full_image"
        for region in regions:
            source = str(region.get("source") or "").strip()
            if source:
                return source
            if region.get("bbox") is not None:
                return "provided_bbox"
        return "full_image"

    def _summarize_hits(self, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        summary = []
        for hit in hits:
            summary.append({
                "dish_id": hit.get("dish_id"),
                "dish_name": hit.get("dish_name"),
                "image_path": os.path.basename(str(hit.get("image_path") or "")),
            })
        return summary
