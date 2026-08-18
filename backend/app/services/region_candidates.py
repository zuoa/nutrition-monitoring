import logging
import os
import shutil
import uuid
from decimal import Decimal
from typing import Any

from PIL import Image
from flask import current_app

from app import db
from app.models import (
    CapturedImage,
    CapturedImageRegion,
    Dish,
    DishSampleImage,
    EmbeddingStatusEnum,
    RegionRecognitionStatusEnum,
    RegionReviewStatusEnum,
)
from app.services.sample_image_quality import expand_bbox, validate_sample_image_path

logger = logging.getLogger(__name__)

MAX_DISH_SAMPLE_IMAGES = 12
MIN_REGION_EDGE = 24


def _coerce_bbox(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    try:
        x1 = int(round(float(value["x1"])))
        y1 = int(round(float(value["y1"])))
        x2 = int(round(float(value["x2"])))
        y2 = int(round(float(value["y2"])))
    except (KeyError, TypeError, ValueError):
        return None
    left = min(x1, x2)
    top = min(y1, y2)
    right = max(x1, x2)
    bottom = max(y1, y2)
    if right - left < MIN_REGION_EDGE or bottom - top < MIN_REGION_EDGE:
        return None
    return {"x1": left, "y1": top, "x2": right, "y2": bottom}


def _region_key(bbox: dict[str, int] | None) -> tuple[int, int, int, int] | None:
    if not bbox:
        return None
    return (int(bbox["x1"]), int(bbox["y1"]), int(bbox["x2"]), int(bbox["y2"]))


def _pick_hit(region_result: dict[str, Any]) -> dict[str, Any] | None:
    accepted = region_result.get("accepted_hit")
    if isinstance(accepted, dict) and accepted:
        return accepted
    for key in ("reranked_hits", "recall_hits"):
        hits = region_result.get(key)
        if isinstance(hits, list) and hits:
            hit = hits[0]
            if isinstance(hit, dict):
                return hit
    return None


def _hit_confidence(hit: dict[str, Any] | None) -> float | None:
    if not hit:
        return None
    raw = hit.get("score", hit.get("similarity"))
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return None


def _resolve_region_status(region_result: dict[str, Any], confidence: float | None) -> RegionRecognitionStatusEnum:
    if not _pick_hit(region_result):
        return RegionRecognitionStatusEnum.unrecognized
    if bool(region_result.get("accepted")) and confidence is not None and confidence >= 0.6:
        return RegionRecognitionStatusEnum.recognized
    return RegionRecognitionStatusEnum.low_confidence


def _safe_unlink(path: str | None):
    if not path or not os.path.exists(path):
        return
    try:
        os.unlink(path)
    except OSError as e:
        logger.warning("Failed to delete region candidate image %s: %s", path, e)


def clear_region_candidates_for_image(image_id: int):
    existing = CapturedImageRegion.query.filter_by(image_id=image_id).all()
    for region in existing:
        _safe_unlink(region.image_path)
        db.session.delete(region)


def _crop_region_image(source_path: str, dest_path: str, bbox: dict[str, int]) -> dict[str, int]:
    with Image.open(source_path) as source:
        rgb = source.convert("RGB")
        width, height = rgb.size
        left, top, right, bottom = expand_bbox(
            (int(bbox["x1"]), int(bbox["y1"]), int(bbox["x2"]), int(bbox["y2"])),
            image_width=width,
            image_height=height,
            padding_ratio=0,
        )
        if right - left < MIN_REGION_EDGE or bottom - top < MIN_REGION_EDGE:
            raise ValueError(f"region 区域太小，宽高至少需要 {MIN_REGION_EDGE}px")
        crop_bbox = expand_bbox(
            (left, top, right, bottom),
            image_width=width,
            image_height=height,
            padding_ratio=float(current_app.config.get("LOCAL_EMBEDDING_CROP_PADDING_RATIO", 0.06)),
        )
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        rgb.crop(crop_bbox).save(dest_path, format="JPEG", quality=95)
    return {"x1": left, "y1": top, "x2": right, "y2": bottom}


def create_region_candidates_from_recognition(
    *,
    image: CapturedImage,
    recognition_result: dict[str, Any],
) -> list[CapturedImageRegion]:
    clear_region_candidates_for_image(image.id)
    if not image.image_path or not os.path.exists(image.image_path):
        return []

    region_sources = {
        int(item.get("index") or idx): item
        for idx, item in enumerate(recognition_result.get("regions") or [], start=1)
        if isinstance(item, dict)
    }
    raw_region_results = [
        item for item in (recognition_result.get("region_results") or [])
        if isinstance(item, dict)
    ]
    region_results_by_index = {
        int(item.get("index") or idx): item
        for idx, item in enumerate(raw_region_results, start=1)
    }
    merged_region_results: list[tuple[dict[str, Any], dict[str, Any]]] = []
    matched_result_indexes: set[int] = set()

    # Detector proposals and retrieval results normally have a one-to-one
    # relationship. Keep detector-only proposals too so the analysis detail can
    # explain when YOLO found a plate but dish retrieval produced no result.
    for region_index, source in region_sources.items():
        region_result = dict(region_results_by_index.get(region_index) or {})
        if region_index in region_results_by_index:
            matched_result_indexes.add(region_index)
        region_result.setdefault("index", region_index)
        region_result.setdefault("bbox", source.get("bbox"))
        merged_region_results.append((region_result, source))

    for fallback_index, region_result in enumerate(raw_region_results, start=1):
        region_index = int(region_result.get("index") or fallback_index)
        if region_index in matched_result_indexes:
            continue
        merged_region_results.append((region_result, region_sources.get(region_index) or {}))

    image_root = current_app.config.get("IMAGE_STORAGE_PATH", "/data/images")
    date_part = image.capture_date.isoformat() if image.capture_date else "unknown"
    dest_dir = os.path.join(image_root, "region_candidates", date_part, str(image.id))
    created: list[CapturedImageRegion] = []
    seen_keys: set[tuple[int, int, int, int]] = set()

    for fallback_index, (region_result, source) in enumerate(merged_region_results, start=1):
        bbox = _coerce_bbox(region_result.get("bbox"))
        if not bbox:
            continue
        key = _region_key(bbox)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        region_index = int(region_result.get("index") or fallback_index)
        hit = _pick_hit(region_result)
        confidence = _hit_confidence(hit)
        status = _resolve_region_status(region_result, confidence)
        stored_name = f"region_{region_index}_{uuid.uuid4().hex}.jpg"
        dest_path = os.path.join(dest_dir, stored_name)
        try:
            normalized_bbox = _crop_region_image(image.image_path, dest_path, bbox)
        except Exception as e:
            logger.warning("Failed to crop region candidate image=%s region=%s: %s", image.id, region_index, e)
            continue

        suggested_dish_id = None
        if hit and hit.get("dish_id") is not None:
            try:
                suggested_dish_id = int(hit.get("dish_id"))
            except (TypeError, ValueError):
                suggested_dish_id = None

        region = CapturedImageRegion(
            image_id=image.id,
            captured_at=image.captured_at,
            region_index=region_index,
            bbox=normalized_bbox,
            bbox_source="pixels",
            detector_source=str(source.get("source") or region_result.get("source") or ""),
            image_path=dest_path,
            recognition_status=status,
            suggested_dish_id=suggested_dish_id,
            suggested_dish_name=str(hit.get("dish_name") or "") if hit else None,
            suggested_confidence=Decimal(str(round(confidence, 3))) if confidence is not None else None,
            review_status=RegionReviewStatusEnum.pending,
            model_version=recognition_result.get("model_version"),
            raw_result={
                **region_result,
                "detector_confidence": source.get("confidence", source.get("score")),
            },
        )
        db.session.add(region)
        created.append(region)

    return created


def _next_sample_sort_order(dish_id: int) -> int:
    current_max_sort = db.session.query(db.func.max(DishSampleImage.sort_order)).filter(
        DishSampleImage.dish_id == dish_id,
        DishSampleImage.is_active.is_(True),
    ).scalar() or 0
    return int(current_max_sort) + 1


def _has_cover_image(dish_id: int) -> bool:
    return bool(db.session.query(DishSampleImage.id).filter(
        DishSampleImage.dish_id == dish_id,
        DishSampleImage.is_active.is_(True),
        DishSampleImage.is_cover.is_(True),
    ).first())


def bind_region_candidate(region: CapturedImageRegion, dish: Dish) -> DishSampleImage:
    if region.review_status == RegionReviewStatusEnum.bound:
        raise ValueError("该候选图已绑定")
    if not region.image_path or not os.path.exists(region.image_path):
        raise ValueError("候选图文件不存在")

    active_count = DishSampleImage.query.filter_by(dish_id=dish.id, is_active=True).count()
    if active_count >= MAX_DISH_SAMPLE_IMAGES:
        raise ValueError(f"每个菜品最多上传 {MAX_DISH_SAMPLE_IMAGES} 张样图")

    quality_error = validate_sample_image_path(
        region.image_path,
        min_edge=max(24, int(current_app.config.get("SAMPLE_IMAGE_MIN_EDGE", 128))),
        max_aspect_ratio=max(1.0, float(current_app.config.get("SAMPLE_IMAGE_MAX_ASPECT_RATIO", 3.0))),
        max_pixels=max(1, int(current_app.config.get("SAMPLE_IMAGE_MAX_PIXELS", 16_777_216))),
    )
    if quality_error:
        raise ValueError(quality_error)

    image_root = current_app.config.get("IMAGE_STORAGE_PATH", "/data/images")
    dest_dir = os.path.join(image_root, "dish_samples", str(dish.id))
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, f"{uuid.uuid4().hex}.jpg")
    shutil.copyfile(region.image_path, dest_path)

    sample_image = DishSampleImage(
        dish_id=dish.id,
        image_path=dest_path,
        original_filename=f"region_candidate_{region.id}.jpg",
        sort_order=_next_sample_sort_order(dish.id),
        is_cover=not _has_cover_image(dish.id),
        embedding_status=EmbeddingStatusEnum.pending,
    )
    db.session.add(sample_image)
    db.session.flush()

    region.review_status = RegionReviewStatusEnum.bound
    region.dish_sample_image_id = sample_image.id
    region.suggested_dish_id = dish.id
    region.suggested_dish_name = dish.name
    if region.raw_result:
        region.raw_result = {
            **region.raw_result,
            "bound_dish_id": dish.id,
            "bound_dish_name": dish.name,
        }
    return sample_image
