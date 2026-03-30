from app.services.local_embedding import LocalEmbeddingIndexService
from app.services.qwen_vl import QwenVLService
from app.services.runtime_config import get_effective_config


class DishRecognitionService:
    def __init__(self, config: dict):
        self.config = get_effective_config(config)
        self.mode = self.config.get("DISH_RECOGNITION_MODE", "vl")

    def recognize_dishes(self, image_path: str, candidate_dishes: list[dict]) -> dict:
        if self.mode == "yolo_embedding_local":
            return LocalEmbeddingIndexService(self.config).recognize_dishes(image_path, candidate_dishes)

        result = QwenVLService(self.config).recognize_dishes(image_path, candidate_dishes)
        result["model_version"] = self.config.get("QWEN_MODEL", "qwen-vl-max")
        return result
