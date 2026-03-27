import io
import logging
import tempfile
import os
from flask import Blueprint, request, current_app, send_file
from app import db
from app.models import Dish, CategoryEnum
from app.utils.jwt_utils import login_required, role_required, api_ok, api_error
from app.utils.pagination import paginate, paginated_response
from app.services.dish_analyzer import DishAnalyzerService
from app.services.qwen_vl import QwenVLService
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

bp = Blueprint("dishes", __name__)
logger = logging.getLogger(__name__)

ALLOWED_ROLES_WRITE = ("admin", "canteen_manager")


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
        if result.get("description"):
            dish.description = result.get("description")

        db.session.commit()

        return api_ok({
            "dish": dish.to_dict(),
            "weight": weight,
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
        df = df.fillna("").applymap(lambda x: str(x).strip() if x else "")
    except Exception as e:
        logger.error(f"Failed to parse Excel: {e}")
        return api_error(f"解析 Excel 失败: {str(e)}")

    # Column mapping (Chinese -> field name)
    column_mapping = {
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

    # Rename columns
    df.columns = [column_mapping.get(str(c).strip(), str(c).strip()) for c in df.columns]

    valid_categories = [c.value for c in CategoryEnum]
    errors = []
    created = []
    updated = []

    for idx, row in df.iterrows():
        row_num = idx + 2  # Excel row number (1-indexed + header)

        # Validate required fields
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

        # Parse optional numeric fields
        def parse_num(val, default=None):
            try:
                return float(val) if val and str(val).strip() else default
            except (ValueError, TypeError):
                return default

        weight = parse_num(row.get("weight"), 100)
        calories = parse_num(row.get("calories"))
        protein = parse_num(row.get("protein"))
        fat = parse_num(row.get("fat"))
        carbohydrate = parse_num(row.get("carbohydrate"))
        sodium = parse_num(row.get("sodium"))
        fiber = parse_num(row.get("fiber"))

        # Check if dish exists
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
            "notes": result.get("notes", ""),
        })
    except Exception as e:
        logger.error(f"Failed to preview dish nutrition: {e}")
        return api_error(f"营养分析失败: {str(e)}"), 500


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

        # If dish name provided, prepend it for context
        if dish_name:
            description = f"【{dish_name}】{description}"

        return api_ok({
            "description": description,
            "dish_name": dish_name,
        })
    except Exception as e:
        logger.error(f"Failed to generate dish description: {e}")
        # Clean up temp file on error
        if "tmp_path" in locals():
            try:
                os.unlink(tmp_path)
            except:
                pass
        return api_error(f"生成描述失败: {str(e)}"), 500
