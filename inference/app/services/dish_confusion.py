from collections import defaultdict
from typing import Any

import numpy as np


def analyze_dish_confusion(
    matrix: np.ndarray,
    metadata: list[dict[str, Any]],
    *,
    high_threshold: float = 0.85,
    medium_threshold: float = 0.75,
    max_pairs: int = 100,
) -> dict[str, Any]:
    """Compare indexed samples across dishes and return potential confusion pairs."""
    vectors = np.asarray(matrix, dtype=np.float32)
    if vectors.ndim != 2:
        raise ValueError("样图向量矩阵格式无效")
    if len(metadata) != vectors.shape[0]:
        raise ValueError("样图向量与元数据数量不一致")
    if not 0 <= medium_threshold < high_threshold <= 1:
        raise ValueError("菜品混淆分析阈值配置无效")

    limit = max(1, int(max_pairs))
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    valid_vector_mask = np.isfinite(vectors).all(axis=1) & (norms[:, 0] > 1e-12)
    normalized = np.divide(
        vectors,
        norms,
        out=np.zeros_like(vectors),
        where=norms > 1e-12,
    )

    samples_by_dish: dict[int, list[int]] = defaultdict(list)
    invalid_sample_count = 0
    for index, item in enumerate(metadata):
        try:
            dish_id = int(item.get("dish_id"))
        except (TypeError, ValueError):
            invalid_sample_count += 1
            continue
        if dish_id <= 0 or not valid_vector_mask[index]:
            invalid_sample_count += 1
            continue
        samples_by_dish[dish_id].append(index)

    dish_ids = sorted(samples_by_dish)
    indexed_dishes = []
    for dish_id in dish_ids:
        sample_indices = samples_by_dish[dish_id]
        first_meta = metadata[sample_indices[0]]
        indexed_dishes.append({
            "dish_id": dish_id,
            "dish_name": str(first_meta.get("dish_name") or ""),
            "sample_count": len(sample_indices),
        })
    risk_pairs: list[dict[str, Any]] = []
    analyzed_pair_count = 0
    high_risk_pair_count = 0
    medium_risk_pair_count = 0

    for left_offset, left_dish_id in enumerate(dish_ids):
        left_indices = samples_by_dish[left_dish_id]
        left_vectors = normalized[left_indices]
        for right_dish_id in dish_ids[left_offset + 1:]:
            analyzed_pair_count += 1
            right_indices = samples_by_dish[right_dish_id]
            similarities = left_vectors @ normalized[right_indices].T
            flat_index = int(np.argmax(similarities))
            left_local, right_local = np.unravel_index(flat_index, similarities.shape)
            max_similarity = float(np.clip(similarities[left_local, right_local], -1.0, 1.0))

            if max_similarity < medium_threshold:
                continue

            risk_level = "high" if max_similarity >= high_threshold else "medium"
            if risk_level == "high":
                high_risk_pair_count += 1
            else:
                medium_risk_pair_count += 1

            left_index = left_indices[int(left_local)]
            right_index = right_indices[int(right_local)]
            left_meta = metadata[left_index]
            right_meta = metadata[right_index]
            risk_pairs.append({
                "risk_level": risk_level,
                "max_similarity": round(max_similarity, 6),
                "similar_sample_pair_count": int(np.count_nonzero(similarities >= medium_threshold)),
                "left": {
                    "dish_id": left_dish_id,
                    "dish_name": str(left_meta.get("dish_name") or ""),
                    "sample_count": len(left_indices),
                    "sample_image_id": left_meta.get("image_id"),
                    "sample_filename": left_meta.get("original_filename"),
                },
                "right": {
                    "dish_id": right_dish_id,
                    "dish_name": str(right_meta.get("dish_name") or ""),
                    "sample_count": len(right_indices),
                    "sample_image_id": right_meta.get("image_id"),
                    "sample_filename": right_meta.get("original_filename"),
                },
            })

    risk_pairs.sort(
        key=lambda item: (item["max_similarity"], item["similar_sample_pair_count"]),
        reverse=True,
    )
    return {
        "thresholds": {
            "high": float(high_threshold),
            "medium": float(medium_threshold),
        },
        "summary": {
            "indexed_dish_count": len(dish_ids),
            "indexed_sample_count": sum(len(indices) for indices in samples_by_dish.values()),
            "invalid_sample_count": invalid_sample_count,
            "analyzed_pair_count": analyzed_pair_count,
            "high_risk_pair_count": high_risk_pair_count,
            "medium_risk_pair_count": medium_risk_pair_count,
            "safe_pair_count": analyzed_pair_count - high_risk_pair_count - medium_risk_pair_count,
            "returned_pair_count": min(len(risk_pairs), limit),
            "truncated_pair_count": max(0, len(risk_pairs) - limit),
        },
        "indexed_dishes": indexed_dishes,
        "pairs": risk_pairs[:limit],
    }
