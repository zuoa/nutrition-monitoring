from typing import Any

from app.services.local_embedding import LocalEmbeddingIndexService


class EmbeddingRetrievalService:
    def __init__(self, config: dict):
        self.index_service = LocalEmbeddingIndexService(config)

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
                    }
                    for item in embedded
                ],
                "model_version": self.index_service._build_model_version(),
            }
        finally:
            for item in embedded:
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
        }
