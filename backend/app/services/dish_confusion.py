from typing import Any

from app.models import Dish, DishSampleImage


def enrich_dish_confusion_report(report: dict[str, Any], *, pipeline: str) -> dict[str, Any]:
    """Add current dish coverage, preview URLs, and actionable recommendations."""
    active_dishes = Dish.query.filter(Dish.is_active.is_(True)).order_by(Dish.name.asc()).all()
    active_dish_by_id = {dish.id: dish for dish in active_dishes}
    indexed_dish_ids = {
        int(item["dish_id"])
        for item in report.get("indexed_dishes", [])
        if str(item.get("dish_id", "")).isdigit()
    }
    indexed_dish_by_id = {
        dish.id: dish
        for dish in Dish.query.filter(Dish.id.in_(indexed_dish_ids)).all()
    } if indexed_dish_ids else {}

    sample_image_ids = {
        image_id
        for pair in report.get("pairs", [])
        for side_key in ("left", "right")
        if isinstance(image_id := (pair.get(side_key) or {}).get("sample_image_id"), int)
    }
    sample_by_id = {
        image.id: image
        for image in DishSampleImage.query.filter(DishSampleImage.id.in_(sample_image_ids)).all()
    } if sample_image_ids else {}

    enriched_pairs = []
    for pair in report.get("pairs", []):
        enriched_pair = dict(pair)
        for side_key in ("left", "right"):
            side = dict(pair.get(side_key) or {})
            dish = indexed_dish_by_id.get(side.get("dish_id"))
            sample = sample_by_id.get(side.get("sample_image_id"))
            if dish:
                side.update({
                    "dish_name": dish.name,
                    "category": dish.category.value if dish.category else None,
                    "exists": True,
                    "is_active": bool(dish.is_active),
                })
            else:
                side["exists"] = False
                side["is_active"] = False
            if sample:
                side["sample_image_url"] = sample._build_image_url()
            enriched_pair[side_key] = side
        enriched_pairs.append(enriched_pair)

    not_analyzed_dishes = [
        {
            "dish_id": dish.id,
            "dish_name": dish.name,
            "category": dish.category.value if dish.category else None,
            "sample_image_count": len([image for image in dish.sample_images if image.is_active]),
            "sample_embedding_status": dish.to_dict(embedding_pipeline=pipeline)["sample_embedding_status"],
        }
        for dish in active_dishes
        if dish.id not in indexed_dish_ids
    ]

    summary = dict(report.get("summary") or {})
    stale_indexed_dish_count = len(indexed_dish_ids - set(active_dish_by_id))
    summary.update({
        "total_active_dish_count": len(active_dishes),
        "indexed_dish_count": len(indexed_dish_ids & set(active_dish_by_id)),
        "not_analyzed_dish_count": len(not_analyzed_dishes),
        "stale_indexed_dish_count": stale_indexed_dish_count,
    })

    recommendations = []
    if not report.get("index_ready", False):
        recommendations.append("当前样图索引尚未构建，请先重建当前识别模式的样图索引，再重新体检。")
    elif summary.get("analyzed_pair_count", 0) == 0:
        recommendations.append("当前可用索引菜品不足两个，尚未执行跨菜品对比；请补齐样图并完成向量化后重新体检。")
    elif summary.get("high_risk_pair_count", 0):
        recommendations.append("优先复核高风险菜品对的最高相似样图，排除错标、重复图和裁剪范围过近。")
        recommendations.append("为高风险菜品补充能体现主料、配菜、形状和摆盘差异的样图，再重建索引后复测。")
    elif summary.get("medium_risk_pair_count", 0):
        recommendations.append("关注中风险菜品对，建议补充不同批次、光线和角度的差异化样图。")
    else:
        recommendations.append("当前索引未发现达到预警阈值的跨菜品样图，仍建议在新增菜品或样图后重新体检。")
    if not_analyzed_dishes:
        recommendations.append("先为未纳入分析的菜品补齐样图并完成向量化，否则报告无法覆盖这些菜品。")
    if summary.get("invalid_sample_count", 0):
        recommendations.append("索引中存在无效向量样图，建议重建当前样图索引。")
    if stale_indexed_dish_count:
        recommendations.append("当前索引仍包含已停用或已删除菜品，建议立即重建索引，避免旧菜品继续参与识别。")

    return {
        **report,
        "summary": summary,
        "pairs": enriched_pairs,
        "not_analyzed_dishes": not_analyzed_dishes,
        "recommendations": recommendations,
    }
