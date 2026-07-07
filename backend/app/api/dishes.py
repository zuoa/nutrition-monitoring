import io
import logging
import os
import re
import tempfile
import unicodedata
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from flask import Blueprint, request, current_app, send_file
from app import db
from app.models import Dish, CategoryEnum, DishSampleImage, EmbeddingStatusEnum, TaskLog
from app.utils.jwt_utils import login_required, role_required, api_ok, api_error
from app.utils.pagination import paginate, paginated_response
from app.services.dish_analyzer import DishAnalyzerService
from app.services.embedding_jobs import can_trigger_local_embedding_rebuild, trigger_local_embedding_rebuild
from app.services.qwen_vl import QwenVLService
from app.services.structured_description import compose_structured_description, empty_structured_description
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

bp = Blueprint("dishes", __name__)
logger = logging.getLogger(__name__)

ALLOWED_ROLES_WRITE = ("admin", "canteen_manager")
MAX_DISH_SAMPLE_IMAGES = 12
ALLOWED_SAMPLE_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


@bp.route("/", methods=["GET"])
@login_required
def list_dishes():
    q = Dish.query
    # Filters
    if request.args.get("active_only") != "false":
        q = q.filter(Dish.is_active)
    if category := request.args.get("category"):
        q = q.filter(Dish.category == category)
    if search := request.args.get("search"):
        q = q.filter(Dish.name.ilike(f"%{search}%"))
    has_sample_images = request.args.get("has_sample_images")
    if has_sample_images in {"true", "false"}:
        has_active_sample_images = Dish.sample_images.any(DishSampleImage.is_active.is_(True))
        q = q.filter(has_active_sample_images if has_sample_images == "true" else ~has_active_sample_images)
    q = q.order_by(Dish.category, Dish.name)

    items, total, page, page_size = paginate(q)
    return api_ok(paginated_response([d.to_dict() for d in items], total, page, page_size))


@bp.route("/<int:dish_id>", methods=["GET"])
@login_required
def get_dish(dish_id):
    dish = Dish.query.get_or_404(dish_id)
    return api_ok(dish.to_dict())


@bp.route("/", methods=["POST"])
@role_required(*ALLOWED_ROLES_WRITE)
def create_dish():
    data = request.get_json() or {}
    errors = _validate_dish(data)
    if errors:
        return api_error("; ".join(errors))

    name = data["name"].strip()
    if Dish.query.filter(Dish.name.ilike(name)).first():
        return api_error(f"菜品「{name}」已存在")

    dish = Dish(
        name=name,
        description=data.get("description"),
        ingredients=data.get("ingredients"),
        image_url=data.get("image_url"),
        price=data["price"],
        category=data["category"],
        weight=data.get("weight", 100),
        calories=data.get("calories"),
        protein=data.get("protein"),
        fat=data.get("fat"),
        carbohydrate=data.get("carbohydrate"),
        sodium=data.get("sodium"),
        fiber=data.get("fiber"),
    )
    db.session.add(dish)
    db.session.commit()
    return api_ok(dish.to_dict()), 201


@bp.route("/<int:dish_id>", methods=["PUT"])
@role_required(*ALLOWED_ROLES_WRITE)
def update_dish(dish_id):
    dish = Dish.query.get_or_404(dish_id)
    data = request.get_json() or {}

    if "name" in data:
        name = data["name"].strip()
        existing = Dish.query.filter(Dish.name.ilike(name), Dish.id != dish_id).first()
        if existing:
            return api_error(f"菜品「{name}」已存在")
        dish.name = name

    for field in ["description", "ingredients", "image_url", "price", "category", "weight",
                  "calories", "protein", "fat", "carbohydrate", "sodium", "fiber", "is_active"]:
        if field in data:
            setattr(dish, field, data[field])

    db.session.commit()
    return api_ok(dish.to_dict())


@bp.route("/<int:dish_id>", methods=["DELETE"])
@role_required(*ALLOWED_ROLES_WRITE)
def delete_dish(dish_id):
    dish = Dish.query.get_or_404(dish_id)
    dish.is_active = False  # soft delete
    db.session.commit()
    return api_ok({"id": dish_id})


@bp.route("/categories", methods=["GET"])
@login_required
def list_categories():
    return api_ok([c.value for c in CategoryEnum])


def _validate_dish(data):
    errors = []
    if not data.get("name", "").strip():
        errors.append("菜品名称不能为空")
    if data.get("price") is None:
        errors.append("价格不能为空")
    elif float(data["price"]) < 0:
        errors.append("价格不能为负数")
    if not data.get("category"):
        errors.append("分类不能为空")
    elif data["category"] not in [c.value for c in CategoryEnum]:
        errors.append(f"分类无效，可选：{[c.value for c in CategoryEnum]}")
    return errors


def _validate_sample_image_file(file_storage):
    filename = (file_storage.filename or "").strip()
    if not filename:
        return "文件名无效"

    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_SAMPLE_IMAGE_EXTENSIONS:
        return f"不支持的图片格式，请上传 {', '.join(sorted(ALLOWED_SAMPLE_IMAGE_EXTENSIONS))} 格式"

    try:
        current_pos = file_storage.stream.tell()
        file_storage.stream.seek(0, os.SEEK_END)
        size = file_storage.stream.tell()
        file_storage.stream.seek(current_pos)
    except (AttributeError, OSError):
        size = 0

    max_size = current_app.config.get("MAX_IMAGE_SIZE", 5 * 1024 * 1024)
    if size and size > max_size:
        return f"图片大小不能超过 {max_size // (1024 * 1024)}MB"

    return None


def _save_sample_image_file(dish_id: int, file_storage, *, sort_order: int, is_cover: bool) -> DishSampleImage:
    ext = os.path.splitext(file_storage.filename or "")[1].lower()
    image_root = current_app.config.get("IMAGE_STORAGE_PATH", "/data/images")
    dest_dir = os.path.join(image_root, "dish_samples", str(dish_id))
    os.makedirs(dest_dir, exist_ok=True)

    stored_name = f"{uuid.uuid4().hex}{ext}"
    dest_path = os.path.join(dest_dir, stored_name)
    file_storage.save(dest_path)

    return DishSampleImage(
        dish_id=dish_id,
        image_path=dest_path,
        original_filename=file_storage.filename,
        sort_order=sort_order,
        is_cover=is_cover,
        embedding_status=EmbeddingStatusEnum.pending,
    )


def _reset_sample_image_embedding_state(image: DishSampleImage):
    image.embedding_status = EmbeddingStatusEnum.pending
    image.embedding_model = None
    image.embedding_version = None
    image.embedding_input_hash = None
    image.embedding_vector = None
    image.embedding_updated_at = None
    image.error_message = None


def _replace_sample_image_file(image: DishSampleImage, file_storage):
    ext = os.path.splitext(file_storage.filename or "")[1].lower()
    image_root = current_app.config.get("IMAGE_STORAGE_PATH", "/data/images")
    dest_dir = os.path.join(image_root, "dish_samples", str(image.dish_id))
    os.makedirs(dest_dir, exist_ok=True)

    stored_name = f"{uuid.uuid4().hex}{ext}"
    dest_path = os.path.join(dest_dir, stored_name)
    file_storage.save(dest_path)

    old_path = image.image_path
    image.image_path = dest_path
    image.original_filename = file_storage.filename
    _reset_sample_image_embedding_state(image)

    if old_path and old_path != dest_path and os.path.exists(old_path):
        try:
            os.unlink(old_path)
        except OSError as e:
            logger.warning("Failed to delete previous sample image file %s: %s", old_path, e)


def _delete_sample_image_file(image: DishSampleImage):
    image_path = image.image_path
    if image_path and os.path.exists(image_path):
        try:
            os.unlink(image_path)
        except OSError as e:
            logger.warning("Failed to delete sample image file %s: %s", image_path, e)


def _ensure_cover_image(dish_id: int):
    has_cover = db.session.query(DishSampleImage.id).filter(
        DishSampleImage.dish_id == dish_id,
        DishSampleImage.is_active.is_(True),
        DishSampleImage.is_cover.is_(True),
    ).first()
    if has_cover:
        return

    next_image = DishSampleImage.query.filter_by(
        dish_id=dish_id,
        is_active=True,
    ).order_by(DishSampleImage.sort_order.asc(), DishSampleImage.id.asc()).first()
    if next_image:
        next_image.is_cover = True


@bp.route("/<int:dish_id>/images", methods=["POST"])
@role_required(*ALLOWED_ROLES_WRITE)
def upload_dish_images(dish_id):
    dish = Dish.query.get_or_404(dish_id)
    files = request.files.getlist("images")
    if not files and "image" in request.files:
        files = [request.files["image"]]

    files = [f for f in files if (f.filename or "").strip()]
    if not files:
        return api_error("请至少上传一张图片")

    active_count = DishSampleImage.query.filter_by(dish_id=dish.id, is_active=True).count()
    if active_count + len(files) > MAX_DISH_SAMPLE_IMAGES:
        return api_error(f"每个菜品最多上传 {MAX_DISH_SAMPLE_IMAGES} 张样图")

    for file in files:
        error = _validate_sample_image_file(file)
        if error:
            return api_error(f"{file.filename}: {error}")

    created_images = []
    try:
        current_max_sort = db.session.query(db.func.max(DishSampleImage.sort_order)).filter(
            DishSampleImage.dish_id == dish.id,
            DishSampleImage.is_active.is_(True),
        ).scalar() or 0
        has_cover = db.session.query(DishSampleImage.id).filter(
            DishSampleImage.dish_id == dish.id,
            DishSampleImage.is_cover.is_(True),
            DishSampleImage.is_active.is_(True),
        ).first()
        for file in files:
            current_max_sort += 1
            image = _save_sample_image_file(
                dish.id,
                file,
                sort_order=current_max_sort,
                is_cover=not bool(has_cover),
            )
            db.session.add(image)
            created_images.append(image)
            has_cover = True
        db.session.commit()

        try:
            trigger_local_embedding_rebuild(current_app.config, reason="dish sample upload")
        except Exception as e:
            logger.warning("Failed to trigger local embedding rebuild after upload: %s", e)
    except Exception as e:
        db.session.rollback()
        for image in created_images:
            _delete_sample_image_file(image)
        logger.error("Failed to upload dish sample images for dish %s: %s", dish.id, e)
        return api_error(f"上传样图失败: {str(e)}"), 500

    return api_ok({
        "dish_id": dish.id,
        "images": [img.to_dict() for img in created_images],
        "sample_image_count": DishSampleImage.query.filter_by(dish_id=dish.id, is_active=True).count(),
    }), 201


@bp.route("/images/<int:image_id>", methods=["DELETE"])
@role_required(*ALLOWED_ROLES_WRITE)
def delete_dish_image(image_id):
    image = DishSampleImage.query.get_or_404(image_id)
    dish_id = image.dish_id
    _delete_sample_image_file(image)
    db.session.delete(image)
    db.session.flush()
    _ensure_cover_image(dish_id)
    db.session.commit()
    try:
        trigger_local_embedding_rebuild(current_app.config, reason="dish sample delete")
    except Exception as e:
        logger.warning("Failed to trigger local embedding rebuild after delete: %s", e)
    return api_ok({"id": image_id, "dish_id": dish_id})


@bp.route("/images/<int:image_id>", methods=["PUT"])
@role_required(*ALLOWED_ROLES_WRITE)
def update_dish_image(image_id):
    image = DishSampleImage.query.get_or_404(image_id)
    file = request.files.get("image")
    if file is None or not (file.filename or "").strip():
        return api_error("请上传图片")

    error = _validate_sample_image_file(file)
    if error:
        return api_error(error)

    try:
        _replace_sample_image_file(image, file)
        db.session.commit()

        try:
            trigger_local_embedding_rebuild(current_app.config, reason="dish sample replace")
        except Exception as e:
            logger.warning("Failed to trigger local embedding rebuild after replace: %s", e)
    except Exception as e:
        db.session.rollback()
        logger.error("Failed to replace dish sample image %s: %s", image_id, e, exc_info=True)
        return api_error(f"更新样图失败: {str(e)}"), 500

    return api_ok({
        "dish_id": image.dish_id,
        "image": image.to_dict(),
    })


@bp.route("/rebuild-sample-embeddings", methods=["POST"])
@role_required(*ALLOWED_ROLES_WRITE)
def rebuild_dish_sample_embeddings():
    allowed, skip_reason = can_trigger_local_embedding_rebuild(current_app.config)
    if not allowed:
        return api_error(f"当前配置不支持重建本地样图 embedding: {skip_reason}")

    try:
        from app.tasks.embeddings import rebuild_sample_embeddings
        rebuild_sample_embeddings.delay()
        return api_ok({"message": "样图 embedding 重建任务已提交"})
    except Exception as e:
        logger.error("Failed to submit embedding rebuild task: %s", e, exc_info=True)
        return api_error(f"提交重建任务失败: {str(e)}"), 500


@bp.route("/<int:dish_id>/analyze-nutrition", methods=["POST"])
@role_required(*ALLOWED_ROLES_WRITE)
def analyze_dish_nutrition(dish_id):
    """Analyze dish nutrition using AI and update dish record."""
    dish = Dish.query.get_or_404(dish_id)
    data = request.get_json() or {}
    weight = int(data.get("weight", 100))

    if weight <= 0 or weight > 10000:
        return api_error("重量必须在 1-10000g 之间")

    # Get config from app
    config = current_app.config
    api_key = config.get("OPENAI_API_KEY", "")

    if not api_key:
        return api_error("营养分析服务未配置 (OPENAI_API_KEY)"), 503

    try:
        analyzer = DishAnalyzerService(config)
        result = analyzer.analyze_nutrition(dish.name, weight)

        # Update dish with analyzed nutrition data and description
        dish.weight = weight
        dish.calories = result.get("calories")
        dish.protein = result.get("protein")
        dish.fat = result.get("fat")
        dish.carbohydrate = result.get("carbohydrate")
        dish.sodium = result.get("sodium")
        dish.fiber = result.get("fiber")
        composed_description = compose_structured_description(
            result.get("description", ""),
            result.get("structured_description"),
        )
        if composed_description:
            dish.description = composed_description

        db.session.commit()

        return api_ok({
            "dish": dish.to_dict(),
            "weight": weight,
            "structured_description": result.get("structured_description", {}),
            "analysis_notes": result.get("notes", ""),
        })
    except Exception as e:
        logger.error(f"Failed to analyze dish nutrition: {e}")
        return api_error(f"营养分析失败: {str(e)}"), 500


@bp.route("/import-template", methods=["GET"])
@login_required
def download_import_template():
    """Download Excel template for dish import."""
    wb = Workbook()
    ws = wb.active
    ws.title = "菜品导入模板"

    # Define columns with Chinese headers
    columns = [
        ("菜品名称 *", "name"),
        ("分类 *", "category"),
        ("单价(元) *", "price"),
        ("份量(g)", "weight"),
        ("视觉描述", "description"),
        ("配菜描述", "ingredients"),
        ("热量(kcal)", "calories"),
        ("蛋白质(g)", "protein"),
        ("脂肪(g)", "fat"),
        ("碳水化合物(g)", "carbohydrate"),
        ("钠(mg)", "sodium"),
        ("膳食纤维(g)", "fiber"),
    ]

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4F46E5", end_color="4F46E5", fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )

    # Write headers
    for col_idx, (header, _) in enumerate(columns, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border

    # Add example row with sample data
    example_data = [
        "红烧肉", "荤菜", 12.00, 150,
        "深红色酱汁包裹的五花肉块，肥瘦相间，表面油亮",
        "五花肉、土豆、冰糖、酱油",
        450, 25, 35, 8, 800, 1.5,
    ]
    for col_idx, value in enumerate(example_data, 1):
        cell = ws.cell(row=2, column=col_idx, value=value)
        cell.alignment = Alignment(vertical="center")
        cell.border = thin_border

    # Set column widths
    col_widths = [15, 8, 10, 8, 40, 30, 12, 12, 10, 14, 10, 12]
    for idx, width in enumerate(col_widths, 1):
        ws.column_dimensions[chr(64 + idx) if idx <= 26 else "A" + chr(64 + idx - 26)].width = width

    # Add a note row
    note_cell = ws.cell(row=4, column=1, value="说明：带 * 的字段为必填项。分类可选值：主食、荤菜、素菜、汤、其他")
    note_cell.font = Font(color="666666", italic=True)

    # Save to BytesIO
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="菜品导入模板.xlsx",
    )


# Excel column mapping (Chinese header -> field name), shared by Excel + ZIP import.
_DISH_IMPORT_COLUMN_MAPPING = {
    "菜品名称": "name", "菜品名称 *": "name",
    "分类": "category", "分类 *": "category",
    "单价(元)": "price", "单价(元) *": "price", "单价": "price",
    "份量(g)": "weight", "份量": "weight", "重量(g)": "weight",
    "视觉描述": "description",
    "配菜描述": "ingredients",
    "热量(kcal)": "calories", "热量": "calories",
    "蛋白质(g)": "protein", "蛋白质": "protein",
    "脂肪(g)": "fat", "脂肪": "fat",
    "碳水化合物(g)": "carbohydrate", "碳水化合物": "carbohydrate",
    "钠(mg)": "sodium", "钠": "sodium",
    "膳食纤维(g)": "fiber", "膳食纤维": "fiber",
}


def _normalize_dish_name(name):
    """Normalize a dish/folder name for matching: NFKC + collapse whitespace + casefold."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", str(name)).strip()
    s = re.sub(r"\s+", " ", s)
    return s.casefold()


def _parse_optional_number(val, default=None):
    try:
        return float(val) if val and str(val).strip() else default
    except (ValueError, TypeError):
        return default


def _upsert_dishes_from_dataframe(df):
    """Validate + upsert dishes from a cleaned (fillna'd) Excel DataFrame.

    Stages changes in the session (does NOT commit). Returns
    (created_names, updated_names, errors, name_normalized -> Dish map).
    Callers must flush()/commit() to populate ids on newly created dishes.
    """
    df.columns = [_DISH_IMPORT_COLUMN_MAPPING.get(str(c).strip(), str(c).strip()) for c in df.columns]
    valid_categories = [c.value for c in CategoryEnum]
    errors = []
    created = []
    updated = []
    dish_map = {}

    for idx, row in df.iterrows():
        row_num = idx + 2  # Excel row number (1-indexed + header)

        name = row.get("name", "").strip()
        category = row.get("category", "").strip()
        price_str = str(row.get("price", "")).strip()

        if not name:
            errors.append(f"第{row_num}行: 菜品名称不能为空")
            continue
        if not category:
            errors.append(f"第{row_num}行: 分类不能为空")
            continue
        if category not in valid_categories:
            errors.append(f"第{row_num}行: 分类「{category}」无效，可选: {valid_categories}")
            continue
        if not price_str:
            errors.append(f"第{row_num}行: 单价不能为空")
            continue

        try:
            price = float(price_str)
            if price < 0:
                raise ValueError()
        except ValueError:
            errors.append(f"第{row_num}行: 单价格式无效")
            continue

        weight = _parse_optional_number(row.get("weight"), 100)
        calories = _parse_optional_number(row.get("calories"))
        protein = _parse_optional_number(row.get("protein"))
        fat = _parse_optional_number(row.get("fat"))
        carbohydrate = _parse_optional_number(row.get("carbohydrate"))
        sodium = _parse_optional_number(row.get("sodium"))
        fiber = _parse_optional_number(row.get("fiber"))

        existing = Dish.query.filter(Dish.name.ilike(name)).first()

        if existing:
            # Update existing dish
            existing.price = price
            existing.category = category
            existing.weight = weight
            if row.get("description"):
                existing.description = row.get("description")
            if row.get("ingredients"):
                existing.ingredients = row.get("ingredients")
            if calories is not None:
                existing.calories = calories
            if protein is not None:
                existing.protein = protein
            if fat is not None:
                existing.fat = fat
            if carbohydrate is not None:
                existing.carbohydrate = carbohydrate
            if sodium is not None:
                existing.sodium = sodium
            if fiber is not None:
                existing.fiber = fiber
            existing.is_active = True
            updated.append(name)
            dish_map[_normalize_dish_name(name)] = existing
        else:
            # Create new dish
            dish = Dish(
                name=name,
                description=row.get("description") or None,
                ingredients=row.get("ingredients") or None,
                price=price,
                category=category,
                weight=weight,
                calories=calories,
                protein=protein,
                fat=fat,
                carbohydrate=carbohydrate,
                sodium=sodium,
                fiber=fiber,
            )
            db.session.add(dish)
            created.append(name)
            dish_map[_normalize_dish_name(name)] = dish

    return created, updated, errors, dish_map


@bp.route("/import", methods=["POST"])
@role_required(*ALLOWED_ROLES_WRITE)
def import_dishes():
    """Import dishes from Excel file."""
    if "file" not in request.files:
        return api_error("未上传文件")

    file = request.files["file"]
    if not file.filename or not file.filename.endswith((".xlsx", ".xls")):
        return api_error("请上传 Excel 文件 (.xlsx 或 .xls)")

    try:
        df = pd.read_excel(file, sheet_name=0, dtype=str)
        df = df.fillna("").map(lambda x: str(x).strip() if x else "")
    except Exception as e:
        logger.error(f"Failed to parse Excel: {e}")
        return api_error(f"解析 Excel 失败: {str(e)}")

    created, updated, errors, _ = _upsert_dishes_from_dataframe(df)

    if errors and not created and not updated:
        return api_error("导入失败:\n" + "\n".join(errors[:20]))

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to commit import: {e}")
        return api_error(f"保存失败: {str(e)}")

    result = {
        "created_count": len(created),
        "updated_count": len(updated),
        "created": created[:20],
        "updated": updated[:20],
    }
    if errors:
        result["warnings"] = errors[:10]

    return api_ok(result)


class _BytesFileStorage:
    """Minimal werkzeug.FileStorage stand-in wrapping in-memory bytes, so the
    existing _validate_sample_image_file / _save_sample_image_file helpers work
    unchanged for images read out of a ZIP archive."""

    def __init__(self, filename, data):
        self.filename = filename
        self._buf = io.BytesIO(data)

    @property
    def stream(self):
        return self._buf

    def save(self, dest_path):
        with open(dest_path, "wb") as f:
            f.write(self._buf.getvalue())


def _validate_image_magic_bytes(data):
    """Return None if data looks like a real image, else an error message.

    Degrades gracefully: if libmagic is unavailable (common on dev hosts
    without libmagic installed), trust the file extension and return None.
    """
    if not data:
        return "图片内容为空"
    try:
        import magic  # python-magic; lazy import (libmagic may be absent)
        mime = magic.from_buffer(data, mime=True)
    except Exception:
        return None
    if mime in {"image/jpeg", "image/png", "image/webp"}:
        return None
    return f"文件内容不是有效图片 ({mime})"


@bp.route("/import-zip", methods=["POST"])
@role_required(*ALLOWED_ROLES_WRITE)
def import_dishes_zip():
    """Import dishes + sample images from a ZIP archive for vectorization.

    ZIP layout: one Excel (.xlsx/.xls) + top-level folders named after each
    dish, each folder holding jpg/png sample images. The Excel is upserted
    exactly like /import; matched folders' images are saved as DishSampleImage
    rows (appended up to MAX_DISH_SAMPLE_IMAGES per dish) and a single embedding
    rebuild is triggered at the end.
    """
    if "file" not in request.files:
        return api_error("未上传文件")

    file = request.files["file"]
    filename = (file.filename or "").strip()
    if not filename or not filename.lower().endswith(".zip"):
        return api_error("请上传 ZIP 文件 (.zip)")

    max_zip_size = current_app.config.get("MAX_IMPORT_ZIP_SIZE", 1000 * 1024 * 1024)
    raw = file.read()
    if len(raw) > max_zip_size:
        return api_error(f"ZIP 文件过大，不能超过 {max_zip_size // (1024 * 1024)}MB")

    max_entries = current_app.config.get("MAX_ZIP_ENTRIES", 2000)
    max_extracted = current_app.config.get("MAX_ZIP_EXTRACTED_SIZE", 1000 * 1024 * 1024)

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as e:
        return api_error(f"无法读取 ZIP 文件: {str(e)}")

    with zf:
        bad = zf.testzip()
        if bad is not None:
            return api_error(f"ZIP 文件损坏: {bad}")

        infos = zf.infolist()
        if len(infos) > max_entries:
            return api_error(f"ZIP 包含超过 {max_entries} 个文件")

        total_uncompressed = sum(zi.file_size for zi in infos)
        if total_uncompressed > max_extracted:
            return api_error("ZIP 解压后总体积超过限制")

        # Path-traversal / symlink safety (we never extract to disk, but reject anyway).
        for zi in infos:
            name = zi.filename
            if name.startswith("/") or "\\" in name or ".." in Path(name).parts:
                return api_error(f"ZIP 包含非法路径: {name}")
            mode = (zi.external_attr >> 16) & 0o170000
            if mode == 0o120000:  # symlink
                return api_error(f"ZIP 包含符号链接: {name}")

        excel_candidates = [
            zi.filename for zi in infos
            if not zi.is_dir() and zi.filename.lower().endswith((".xlsx", ".xls"))
        ]
        if not excel_candidates:
            return api_error("ZIP 中未找到 Excel 文件 (.xlsx/.xls)")
        warnings = []
        excel_name = "菜品导入模板.xlsx" if "菜品导入模板.xlsx" in excel_candidates else excel_candidates[0]
        if len(excel_candidates) > 1:
            warnings.append(f"ZIP 中找到多个 Excel 文件，已使用「{excel_name}」")

        try:
            df = pd.read_excel(io.BytesIO(zf.read(excel_name)), sheet_name=0, dtype=str)
            df = df.fillna("").map(lambda x: str(x).strip() if x else "")
        except Exception as e:
            logger.error("Failed to parse Excel from zip: %s", e)
            return api_error(f"解析 Excel 失败: {str(e)}")

        try:
            created, updated, errors, dish_map = _upsert_dishes_from_dataframe(df)
        except Exception as e:
            db.session.rollback()
            logger.error("Failed to upsert dishes from zip: %s", e)
            return api_error(f"保存失败: {str(e)}")

        if errors and not created and not updated:
            return api_error("导入失败:\n" + "\n".join(errors[:20]))

        images_imported = 0
        images_skipped = 0
        dishes_with_images = 0
        folders_unmatched = 0

        try:
            db.session.flush()  # populate ids on newly created dishes

            # Group sample images by top-level folder. Only "<folder>/<file>"
            # entries are honored; deeper nesting and macOS metadata are skipped.
            folder_images = {}
            for zi in infos:
                if zi.is_dir():
                    continue
                name = zi.filename
                parts = name.split("/")
                if len(parts) > 2:
                    warnings.append(f"忽略嵌套目录文件: {name}")
                    continue
                if len(parts) != 2 or not parts[0] or not parts[1]:
                    continue
                folder, basename = parts[0], parts[1]
                if folder == "__MACOSX" or basename.startswith(".") or basename == ".DS_Store":
                    continue
                ext = os.path.splitext(basename)[1].lower()
                if ext not in ALLOWED_SAMPLE_IMAGE_EXTENSIONS:
                    continue
                folder_images.setdefault(folder, []).append(name)

            for folder, names in folder_images.items():
                dish = dish_map.get(_normalize_dish_name(folder))
                if dish is None:
                    folders_unmatched += 1
                    warnings.append(f"未找到与文件夹「{folder}」匹配的菜品")
                    continue

                names_sorted = sorted(names, key=os.path.basename)
                existing_active = DishSampleImage.query.filter_by(
                    dish_id=dish.id, is_active=True,
                ).count()
                if existing_active >= MAX_DISH_SAMPLE_IMAGES:
                    images_skipped += len(names_sorted)
                    warnings.append(
                        f"菜品「{dish.name}」样图已达上限({MAX_DISH_SAMPLE_IMAGES})，跳过 {len(names_sorted)} 张"
                    )
                    continue
                to_add = min(MAX_DISH_SAMPLE_IMAGES - existing_active, len(names_sorted))
                current_max_sort = db.session.query(db.func.max(DishSampleImage.sort_order)).filter(
                    DishSampleImage.dish_id == dish.id,
                    DishSampleImage.is_active.is_(True),
                ).scalar() or 0
                has_cover = db.session.query(DishSampleImage.id).filter(
                    DishSampleImage.dish_id == dish.id,
                    DishSampleImage.is_cover.is_(True),
                    DishSampleImage.is_active.is_(True),
                ).first()

                added_for_dish = 0
                for name in names_sorted:
                    if added_for_dish >= to_add:
                        images_skipped += 1
                        continue
                    try:
                        data = zf.read(name)
                    except Exception as e:  # pragma: no cover - defensive
                        warnings.append(f"读取 {name} 失败: {str(e)}")
                        images_skipped += 1
                        continue
                    basename = os.path.basename(name)
                    magic_err = _validate_image_magic_bytes(data)
                    if magic_err:
                        warnings.append(f"{basename}: {magic_err}")
                        images_skipped += 1
                        continue
                    adapter = _BytesFileStorage(basename, data)
                    val_err = _validate_sample_image_file(adapter)
                    if val_err:
                        warnings.append(f"{basename}: {val_err}")
                        images_skipped += 1
                        continue
                    current_max_sort += 1
                    image = _save_sample_image_file(
                        dish.id,
                        adapter,
                        sort_order=current_max_sort,
                        is_cover=(added_for_dish == 0 and not has_cover),
                    )
                    db.session.add(image)
                    images_imported += 1
                    added_for_dish += 1
                if added_for_dish > 0:
                    dishes_with_images += 1

            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error("Failed to commit zip import: %s", e)
            return api_error(f"保存失败: {str(e)}")

    try:
        trigger_local_embedding_rebuild(current_app.config, reason="zip_import")
    except Exception as e:
        logger.warning("Failed to trigger local embedding rebuild after zip import: %s", e)

    result = {
        "created_count": len(created),
        "updated_count": len(updated),
        "created": created[:20],
        "updated": updated[:20],
        "images_imported": images_imported,
        "images_skipped": images_skipped,
        "dishes_with_images": dishes_with_images,
        "folders_unmatched": folders_unmatched,
    }
    combined_warnings = (errors + warnings)[:10]
    if combined_warnings:
        result["warnings"] = combined_warnings

    return api_ok(result)


@bp.route("/analyze-nutrition-preview", methods=["POST"])
@role_required(*ALLOWED_ROLES_WRITE)
def preview_dish_nutrition():
    """Preview nutrition analysis for a dish name without saving."""
    data = request.get_json() or {}
    dish_name = data.get("dish_name", "").strip()
    weight = int(data.get("weight", 100))
    ingredients = data.get("ingredients", "").strip()

    if not dish_name:
        return api_error("菜品名称不能为空")

    if weight <= 0 or weight > 10000:
        return api_error("重量必须在 1-10000g 之间")

    config = current_app.config
    api_key = config.get("OPENAI_API_KEY", "")

    if not api_key:
        return api_error("营养分析服务未配置 (OPENAI_API_KEY)"), 503

    try:
        analyzer = DishAnalyzerService(config)
        result = analyzer.analyze_nutrition(dish_name, weight, ingredients)

        return api_ok({
            "dish_name": dish_name,
            "weight": weight,
            "category": result.get("category", ""),
            "nutrition": {
                "calories": result.get("calories"),
                "protein": result.get("protein"),
                "fat": result.get("fat"),
                "carbohydrate": result.get("carbohydrate"),
                "sodium": result.get("sodium"),
                "fiber": result.get("fiber"),
            },
            "description": result.get("description", ""),
            "structured_description": result.get("structured_description", {}),
            "notes": result.get("notes", ""),
        })
    except Exception as e:
        logger.error(f"Failed to preview dish nutrition: {e}")
        return api_error(f"营养分析失败: {str(e)}"), 500


@bp.route("/batch-analyze-nutrition", methods=["POST"])
@role_required(*ALLOWED_ROLES_WRITE)
def batch_analyze_nutrition():
    """Queue nutrition analysis for all dishes without nutrition data."""
    config = current_app.config
    api_key = config.get("OPENAI_API_KEY", "")

    if not api_key:
        return api_error("营养分析服务未配置 (OPENAI_API_KEY)"), 503

    active_task = TaskLog.query.filter(
        TaskLog.task_type == "dish_nutrition_analysis",
        TaskLog.status.in_(("pending", "running")),
    ).order_by(TaskLog.started_at.desc()).first()
    if active_task:
        return api_ok({
            "message": "已有批量营养分析任务正在执行",
            "task_id": active_task.id,
            "task": active_task.to_dict(),
        })

    total = Dish.query.filter(
        Dish.is_active.is_(True),
        Dish.calories.is_(None),
    ).count()

    if total == 0:
        return api_ok({"message": "没有需要分析的菜品", "total": 0, "success": 0, "failed": 0, "errors": []})

    from app.tasks.dishes import batch_analyze_dish_nutrition, create_batch_nutrition_task_log

    task_log = create_batch_nutrition_task_log(total)
    try:
        celery_task = batch_analyze_dish_nutrition.delay(task_log.id)
        task_log.meta = {
            **(task_log.meta or {}),
            "celery_task_id": celery_task.id,
            "status_text": "任务已提交，等待执行",
        }
        db.session.commit()
    except Exception as e:
        logger.error("Failed to submit dish nutrition analysis task: %s", e, exc_info=True)
        task_log.status = "failed"
        task_log.error_count = 1
        task_log.error_message = str(e)
        task_log.finished_at = datetime.utcnow()
        task_log.meta = {
            **(task_log.meta or {}),
            "status_text": "任务提交失败",
        }
        db.session.commit()
        return api_error(f"提交批量分析任务失败: {str(e)}"), 500

    return api_ok({
        "message": "批量营养分析任务已提交",
        "task_id": task_log.id,
        "task": task_log.to_dict(),
        "total": total,
    })


@bp.route("/generate-description", methods=["POST"])
@role_required(*ALLOWED_ROLES_WRITE)
def generate_dish_description():
    """Generate visual description for a dish from an uploaded sample image using VL model."""
    if "image" not in request.files:
        return api_error("请上传图片文件")

    file = request.files["image"]
    if not file.filename:
        return api_error("文件名无效")

    # Check file extension
    allowed_extensions = {".jpg", ".jpeg", ".png", ".webp"}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_extensions:
        return api_error(f"不支持的图片格式，请上传 {', '.join(allowed_extensions)} 格式")

    # Get optional dish name for context
    dish_name = request.form.get("dish_name", "").strip()

    config = current_app.config
    api_key = config.get("QWEN_API_KEY", "")
    if not api_key:
        return api_error("VL服务未配置 (QWEN_API_KEY)"), 503

    # Save to temp file and process
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            file.save(tmp.name)
            tmp_path = tmp.name

        vl_service = QwenVLService(config)
        result = vl_service.describe_dishes(tmp_path)

        # Clean up temp file
        os.unlink(tmp_path)

        description = result.get("description", "")
        if not description:
            return api_error("无法从图片生成描述")

        descriptions = result.get("descriptions", [])
        # If dish name provided, prepend it for context
        if dish_name:
            description = f"【{dish_name}】{description}"
            if descriptions:
                descriptions = [dict(descriptions[0], description=description), *descriptions[1:]]

        return api_ok({
            "description": description,
            "structured_description": result.get("structured_description", empty_structured_description()),
            "notes": result.get("notes", ""),
            "descriptions": descriptions,
            "dish_name": dish_name,
        })
    except Exception as e:
        logger.error(f"Failed to generate dish description: {e}")
        # Clean up temp file on error
        if "tmp_path" in locals():
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return api_error(f"生成描述失败: {str(e)}"), 500
