import logging
from flask import Blueprint, request, current_app
from app import db
from app.models import Dish, CategoryEnum
from app.utils.jwt_utils import login_required, role_required, api_ok, api_error
from app.utils.pagination import paginate, paginated_response
from app.services.dish_analyzer import DishAnalyzerService

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

    for field in ["description", "image_url", "price", "category", "weight",
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


@bp.route("/analyze-nutrition-preview", methods=["POST"])
@role_required(*ALLOWED_ROLES_WRITE)
def preview_dish_nutrition():
    """Preview nutrition analysis for a dish name without saving."""
    data = request.get_json() or {}
    dish_name = data.get("dish_name", "").strip()
    weight = int(data.get("weight", 100))

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
        result = analyzer.analyze_nutrition(dish_name, weight)

        return api_ok({
            "dish_name": dish_name,
            "weight": weight,
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
