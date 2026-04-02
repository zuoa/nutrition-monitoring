from __future__ import annotations

from typing import Any

from PIL import Image


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_bbox(raw: Any) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None

    x1 = _coerce_float(raw.get("x1"))
    y1 = _coerce_float(raw.get("y1"))
    x2 = _coerce_float(raw.get("x2"))
    y2 = _coerce_float(raw.get("y2"))
    if None in {x1, y1, x2, y2}:
        return None

    left = min(x1, x2)
    top = min(y1, y2)
    right = max(x1, x2)
    bottom = max(y1, y2)
    if right <= left or bottom <= top:
        return None

    return {
        "x1": left,
        "y1": top,
        "x2": right,
        "y2": bottom,
    }


def bbox_looks_like_percentage(bbox: dict[str, float] | None) -> bool:
    if not bbox:
        return False
    return (
        0.0 <= bbox["x1"] <= 100.0
        and 0.0 <= bbox["y1"] <= 100.0
        and 0.0 <= bbox["x2"] <= 100.0
        and 0.0 <= bbox["y2"] <= 100.0
    )


def bbox_to_pixels(
    raw: Any,
    *,
    image_width: int,
    image_height: int,
    bbox_source: str = "auto",
) -> dict[str, int] | None:
    bbox = normalize_bbox(raw)
    if not bbox:
        return None

    normalized_source = str(bbox_source or "auto").strip().lower()
    if normalized_source not in {"auto", "percent", "pixels"}:
        normalized_source = "auto"

    use_percent = normalized_source == "percent"
    if normalized_source == "auto" and max(image_width, image_height) > 100 and bbox_looks_like_percentage(bbox):
        use_percent = True

    if use_percent:
        left = round(image_width * bbox["x1"] / 100.0)
        top = round(image_height * bbox["y1"] / 100.0)
        right = round(image_width * bbox["x2"] / 100.0)
        bottom = round(image_height * bbox["y2"] / 100.0)
    else:
        left = round(bbox["x1"])
        top = round(bbox["y1"])
        right = round(bbox["x2"])
        bottom = round(bbox["y2"])

    left = max(0, min(left, max(image_width - 1, 0)))
    top = max(0, min(top, max(image_height - 1, 0)))
    right = max(left + 1, min(right, image_width))
    bottom = max(top + 1, min(bottom, image_height))
    if right <= left or bottom <= top:
        return None

    return {
        "x1": left,
        "y1": top,
        "x2": right,
        "y2": bottom,
    }


def derive_position_from_bbox(
    raw: Any,
    *,
    image_width: int,
    image_height: int,
    bbox_source: str = "auto",
) -> str:
    bbox = bbox_to_pixels(
        raw,
        image_width=image_width,
        image_height=image_height,
        bbox_source=bbox_source,
    )
    if not bbox or image_width <= 0 or image_height <= 0:
        return ""

    center_x = ((bbox["x1"] + bbox["x2"]) / 2.0) / float(image_width)
    center_y = ((bbox["y1"] + bbox["y2"]) / 2.0) / float(image_height)

    if center_x < (1.0 / 3.0):
        horizontal = "左"
    elif center_x > (2.0 / 3.0):
        horizontal = "右"
    else:
        horizontal = "中"

    if center_y < (1.0 / 3.0):
        vertical = "上"
    elif center_y > (2.0 / 3.0):
        vertical = "下"
    else:
        vertical = "中"

    if horizontal == "中" and vertical == "中":
        return "中间"
    if horizontal == "中":
        return "上方" if vertical == "上" else "下方"
    if vertical == "中":
        return f"{horizontal}侧"
    return f"{horizontal}{vertical}"


def load_image_size(image_path: str) -> tuple[int, int] | None:
    if not image_path:
        return None
    with Image.open(image_path) as image:
        return image.size
