import math
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError


DEFAULT_SAMPLE_IMAGE_MAX_PIXELS = 16_777_216


def validate_sample_dimensions(
    width: int,
    height: int,
    *,
    min_edge: int,
    max_aspect_ratio: float,
    max_pixels: int = DEFAULT_SAMPLE_IMAGE_MAX_PIXELS,
) -> str | None:
    if width <= 0 or height <= 0:
        return "图片尺寸无效"

    pixel_count = width * height
    normalized_max_pixels = max(1, int(max_pixels))
    if pixel_count > normalized_max_pixels:
        return (
            f"样图像素总数不能超过 {normalized_max_pixels}，"
            f"当前为 {width} × {height}（{pixel_count} 像素）"
        )

    short_edge = min(width, height)
    if short_edge < min_edge:
        return f"样图短边至少需要 {min_edge}px，当前为 {short_edge}px"

    aspect_ratio = max(width, height) / float(short_edge)
    if aspect_ratio > max_aspect_ratio:
        return f"样图长宽比不能超过 {max_aspect_ratio:g}:1，当前约为 {aspect_ratio:.2f}:1"
    return None


def validate_sample_image_stream(
    stream: BinaryIO,
    *,
    min_edge: int,
    max_aspect_ratio: float,
    max_pixels: int = DEFAULT_SAMPLE_IMAGE_MAX_PIXELS,
) -> str | None:
    try:
        original_position = stream.tell()
    except (AttributeError, OSError):
        original_position = 0

    try:
        stream.seek(0)
        with Image.open(stream) as image:
            width, height = image.size
            dimension_error = validate_sample_dimensions(
                width,
                height,
                min_edge=min_edge,
                max_aspect_ratio=max_aspect_ratio,
                max_pixels=max_pixels,
            )
            if dimension_error:
                return dimension_error
            image.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        return "图片分辨率过高，无法安全处理"
    except (AttributeError, OSError, UnidentifiedImageError, ValueError):
        return "无法读取图片内容，请确认文件未损坏"
    finally:
        try:
            stream.seek(original_position)
        except (AttributeError, OSError):
            pass

    return None


def validate_sample_image_path(
    image_path: str,
    *,
    min_edge: int,
    max_aspect_ratio: float,
    max_pixels: int = DEFAULT_SAMPLE_IMAGE_MAX_PIXELS,
) -> str | None:
    try:
        with Image.open(image_path) as image:
            width, height = image.size
            dimension_error = validate_sample_dimensions(
                width,
                height,
                min_edge=min_edge,
                max_aspect_ratio=max_aspect_ratio,
                max_pixels=max_pixels,
            )
            if dimension_error:
                return dimension_error
            image.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        return "图片分辨率过高，无法安全处理"
    except (OSError, UnidentifiedImageError, ValueError):
        return "无法读取图片内容，请确认文件未损坏"

    return None


def expand_bbox(
    bbox: tuple[int, int, int, int],
    *,
    image_width: int,
    image_height: int,
    padding_ratio: float,
) -> tuple[int, int, int, int]:
    left = max(0, min(int(bbox[0]), max(0, image_width - 1)))
    top = max(0, min(int(bbox[1]), max(0, image_height - 1)))
    right = max(left + 1, min(int(bbox[2]), image_width))
    bottom = max(top + 1, min(int(bbox[3]), image_height))

    ratio = max(0.0, min(float(padding_ratio), 0.5))
    if ratio <= 0:
        return left, top, right, bottom

    pad_x = (right - left) * ratio
    pad_y = (bottom - top) * ratio
    return (
        max(0, int(math.floor(left - pad_x))),
        max(0, int(math.floor(top - pad_y))),
        min(image_width, int(math.ceil(right + pad_x))),
        min(image_height, int(math.ceil(bottom + pad_y))),
    )
