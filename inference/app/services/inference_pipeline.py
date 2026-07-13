from typing import Any

from app.services.local_embedding import LocalEmbeddingIndexService
from app.services.visual_embedding import VisualEmbeddingIndexService


class EmbeddingRetrievalService:
    def __init__(self, config: dict, pipeline: str | None = None):
        selected = str(pipeline or config.get("LOCAL_RETRIEVAL_PIPELINE", "qwen") or "qwen").strip().lower()
        if selected not in {"qwen", "visual"}:
            raise ValueError(f"不支持的检索 pipeline: {selected}")
        self.pipeline = selected
        self.index_service = VisualEmbeddingIndexService(config) if selected == "visual" else LocalEmbeddingIndexService(config)

    def embed(
        self,
        image_path: str,
        *,
        bboxes: list[dict[str, int] | None] | None = None,
        instruction: str | None = None,
    ) -> dict[str, Any]:
        embedded = self.index_service.embed_regions(
            image_path,
            bboxes=bboxes,
            instruction=instruction,
        )
        try:
            return {
                "embeddings": [
                    {
                        "index": item["index"],
                        "bbox": item["bbox"],
                        "vector": item["vector"].astype(float).tolist(),
                        "dim": int(item["vector"].shape[0]),
                        "source": item["source"],
                        **({
                            "patch_vectors": item["patch_vectors"].astype(float).tolist(),
                            "patch_count": int(item["patch_vectors"].shape[0]),
                        } if item.get("patch_vectors") is not None else {}),
                    }
                    for item in embedded
                ],
                "model_version": self.index_service._build_model_version(),
                "pipeline": self.pipeline,
            }
        finally:
            for item in embedded:
                query_patches = getattr(self.index_service, "_query_patches", None)
                if isinstance(query_patches, dict):
                    query_patches.pop(item.get("region_path"), None)
                if item.get("should_cleanup"):
                    self.index_service._safe_unlink(item["region_path"])

    def full(
        self,
        image_path: str,
        *,
        candidate_dishes: list[dict[str, Any]],
        regions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        result = self.index_service.analyze_regions(
            image_path,
            candidate_dishes,
            regions,
        )
        return {
            "recognized_dishes": result.get("dishes", []),
            "region_results": result.get("region_results", []),
            "raw_response": result.get("raw_response"),
            "model_version": result.get("model_version"),
            "notes": result.get("notes"),
            "timings_ms": result.get("timings_ms", {}),
            "pipeline": self.pipeline,
        }
