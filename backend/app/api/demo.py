"""Demo API for real-time camera capture and analysis."""
import base64
import logging
import os
import tempfile
from datetime import datetime
from io import BytesIO

import requests
from flask import Blueprint, request, current_app
from requests.auth import HTTPDigestAuth

from app.utils.jwt_utils import login_required, api_ok, api_error

bp = Blueprint("demo", __name__)
logger = logging.getLogger(__name__)

# Daily recommended nutrition values
DAILY_RECOMMENDED = {
    "calories": 2000,
    "protein": 60,
    "fat": 65,
    "carbohydrate": 275,
    "sodium": 2000,
    "fiber": 25,
}


@bp.route("/cameras", methods=["GET"])
@login_required
def list_cameras():
    """List available Hikvision cameras from config."""
    cameras_raw = current_app.config.get("HIKVISION_CAMERAS", "{}")
    import json
    try:
        cameras = json.loads(cameras_raw) if isinstance(cameras_raw, str) else cameras_raw
    except json.JSONDecodeError:
        cameras = {}

    result = []
    for channel_id, cam_config in cameras.items():
        result.append({
            "channel_id": channel_id,
            "host": cam_config.get("host", ""),
            "port": cam_config.get("port", 80),
            "name": f"摄像头 {channel_id}",
        })

    # Add demo cameras if none configured
    if not result:
        result = [
            {"channel_id": "1", "host": "", "port": 80, "name": "演示摄像头 1"},
            {"channel_id": "2", "host": "", "port": 80, "name": "演示摄像头 2"},
        ]

    return api_ok({"cameras": result})


@bp.route("/capture", methods=["POST"])
@login_required
def capture_snapshot():
    """Capture a snapshot from Hikvision camera.

    Request body:
        - channel_id: Camera channel ID
        - host: Camera IP address (optional if configured)
        - port: Camera port (optional if configured)
        - username: Camera username (optional)
        - password: Camera password (optional)
    """
    data = request.get_json() or {}
    channel_id = data.get("channel_id", "1")

    # Get camera config
    cameras_raw = current_app.config.get("HIKVISION_CAMERAS", "{}")
    import json
    try:
        cameras = json.loads(cameras_raw) if isinstance(cameras_raw, str) else cameras_raw
    except json.JSONDecodeError:
        cameras = {}

    cam_config = cameras.get(channel_id, {})

    # Override with request params
    host = data.get("host") or cam_config.get("host", "")
    port = data.get("port", cam_config.get("port", 80))
    username = data.get("username", cam_config.get("username", "admin"))
    password = data.get("password", cam_config.get("password", ""))

    if not host:
        return api_error("请提供摄像头IP地址或配置 HIKVISION_CAMERAS 环境变量")

    # Capture snapshot via ISAPI
    snapshot_url = f"http://{host}:{port}/ISAPI/Streaming/Channels/{channel_id}01/picture"

    try:
        resp = requests.get(
            snapshot_url,
            auth=HTTPDigestAuth(username, password),
            timeout=10,
        )
        resp.raise_for_status()

        # Convert to base64
        image_base64 = base64.b64encode(resp.content).decode("utf-8")

        # Detect content type
        content_type = resp.headers.get("Content-Type", "image/jpeg")

        return api_ok({
            "image_base64": image_base64,
            "content_type": content_type,
            "captured_at": datetime.now().isoformat(),
            "channel_id": channel_id,
        })

    except requests.Timeout:
        return api_error("连接摄像头超时")
    except requests.RequestException as e:
        logger.error(f"Failed to capture snapshot: {e}")
        return api_error(f"抓拍失败: {str(e)}")


@bp.route("/analyze", methods=["POST"])
@login_required
def analyze_image():
    """Analyze an image for dish recognition and nutrition.

    Request body (multipart/form-data):
        - image: Image file
        OR
        - image_base64: Base64 encoded image data

    Returns:
        - dishes: List of recognized dishes with confidence
        - nutrition: Total nutrition summary
        - suggestions: AI-generated suggestions
    """
    # Get image data
    image_data = None

    if "image" in request.files:
        file = request.files["image"]
        image_data = file.read()
    elif request.is_json:
        data = request.get_json()
        image_base64 = data.get("image_base64", "")
        if image_base64:
            # Remove data URL prefix if present
            if "," in image_base64:
                image_base64 = image_base64.split(",", 1)[1]
            image_data = base64.b64decode(image_base64)
    else:
        data = request.form
        image_base64 = data.get("image_base64", "")
        if image_base64:
            if "," in image_base64:
                image_base64 = image_base64.split(",", 1)[1]
            image_data = base64.b64decode(image_base64)

    if not image_data:
        return api_error("请提供图片数据")

    # Save to temp file for processing
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(image_data)
        temp_path = f.name

    try:
        # Get today's menu dishes for better recognition
        from app.models import Dish, DailyMenu
        from app import db

        today = datetime.now().date()
        menu = DailyMenu.query.filter_by(menu_date=today).first()

        candidate_dishes = []
        if menu and menu.dish_ids:
            dishes = Dish.query.filter(Dish.id.in_(menu.dish_ids), Dish.is_active == True).all()
            candidate_dishes = [
                {"name": d.name, "description": d.description or ""}
                for d in dishes
            ]

        # If no menu, use all active dishes
        if not candidate_dishes:
            dishes = Dish.query.filter_by(is_active=True).limit(50).all()
            candidate_dishes = [
                {"name": d.name, "description": d.description or ""}
                for d in dishes
            ]

        # Call Qwen VL for recognition
        from app.services.qwen_vl import QwenVLService

        qwen = QwenVLService({
            "QWEN_API_KEY": current_app.config.get("QWEN_API_KEY"),
            "QWEN_API_URL": current_app.config.get("QWEN_API_URL"),
            "QWEN_MODEL": current_app.config.get("QWEN_MODEL"),
            "QWEN_TIMEOUT": 60,
            "QWEN_MAX_QPS": current_app.config.get("QWEN_MAX_QPS", 10),
        })

        result = qwen.recognize_dishes(temp_path, candidate_dishes)

        # Build response
        recognized_dishes = result.get("dishes", [])
        dish_names = [d.get("name") for d in recognized_dishes if d.get("name")]

        # Get nutrition info for recognized dishes
        nutrition_total = {
            "calories": 0,
            "protein": 0,
            "fat": 0,
            "carbohydrate": 0,
            "sodium": 0,
            "fiber": 0,
        }

        matched_dishes = []
        if dish_names:
            # Fuzzy match dish names
            from sqlalchemy import or_
            conditions = [Dish.name.contains(name) for name in dish_names]
            db_dishes = Dish.query.filter(or_(*conditions), Dish.is_active == True).all()

            for db_dish in db_dishes:
                matched_dishes.append({
                    "id": db_dish.id,
                    "name": db_dish.name,
                    "category": db_dish.category,
                    "price": float(db_dish.price) if db_dish.price else 0,
                    "calories": db_dish.calories or 0,
                    "protein": db_dish.protein or 0,
                    "fat": db_dish.fat or 0,
                    "carbohydrate": db_dish.carbohydrate or 0,
                    "sodium": db_dish.sodium or 0,
                    "fiber": db_dish.fiber or 0,
                })

                # Sum nutrition
                if db_dish.calories:
                    nutrition_total["calories"] += db_dish.calories
                if db_dish.protein:
                    nutrition_total["protein"] += db_dish.protein
                if db_dish.fat:
                    nutrition_total["fat"] += db_dish.fat
                if db_dish.carbohydrate:
                    nutrition_total["carbohydrate"] += db_dish.carbohydrate
                if db_dish.sodium:
                    nutrition_total["sodium"] += db_dish.sodium
                if db_dish.fiber:
                    nutrition_total["fiber"] += db_dish.fiber

        # Generate suggestions
        suggestions = generate_suggestions(nutrition_total, recognized_dishes)

        # Convert image to base64 for response
        image_base64 = base64.b64encode(image_data).decode("utf-8")

        return api_ok({
            "image_base64": image_base64,
            "recognized_dishes": recognized_dishes,
            "matched_dishes": matched_dishes,
            "nutrition": {
                "total": nutrition_total,
                "recommended": DAILY_RECOMMENDED,
                "percentages": {
                    k: round((v / DAILY_RECOMMENDED.get(k, 1)) * 100, 1) if DAILY_RECOMMENDED.get(k) else 0
                    for k, v in nutrition_total.items()
                },
            },
            "suggestions": suggestions,
            "notes": result.get("notes", ""),
            "analyzed_at": datetime.now().isoformat(),
        })

    except Exception as e:
        logger.error(f"Failed to analyze image: {e}", exc_info=True)
        return api_error(f"分析失败: {str(e)}")
    finally:
        # Cleanup temp file
        try:
            os.unlink(temp_path)
        except:
            pass


def generate_suggestions(nutrition: dict, dishes: list) -> list:
    """Generate nutrition suggestions based on analysis."""
    suggestions = []

    # Check each nutrient
    if nutrition["calories"] > 0:
        cal_pct = (nutrition["calories"] / DAILY_RECOMMENDED["calories"]) * 100
        if cal_pct > 40:
            suggestions.append({
                "type": "warning",
                "title": "热量较高",
                "message": f"本餐热量约 {nutrition['calories']} kcal，占全天建议的 {cal_pct:.0f}%，建议适当控制。",
            })
        elif cal_pct < 20:
            suggestions.append({
                "type": "info",
                "title": "热量适中",
                "message": f"本餐热量约 {nutrition['calories']} kcal，占全天建议的 {cal_pct:.0f}%，搭配合理。",
            })

    if nutrition["protein"] > 0:
        pro_pct = (nutrition["protein"] / DAILY_RECOMMENDED["protein"]) * 100
        if pro_pct < 15:
            suggestions.append({
                "type": "suggestion",
                "title": "蛋白质摄入不足",
                "message": "建议增加优质蛋白摄入，如瘦肉、鱼类、蛋类或豆制品。",
            })

    if nutrition["fat"] > 0:
        fat_pct = (nutrition["fat"] / DAILY_RECOMMENDED["fat"]) * 100
        if fat_pct > 50:
            suggestions.append({
                "type": "warning",
                "title": "脂肪含量较高",
                "message": "本餐脂肪含量较高，建议后续餐次减少油腻食物。",
            })

    if nutrition["sodium"] > 0:
        sod_pct = (nutrition["sodium"] / DAILY_RECOMMENDED["sodium"]) * 100
        if sod_pct > 50:
            suggestions.append({
                "type": "warning",
                "title": "钠含量偏高",
                "message": "本餐钠含量偏高，建议多喝水，后续餐次选择清淡饮食。",
            })

    if nutrition["fiber"] > 0:
        fib_pct = (nutrition["fiber"] / DAILY_RECOMMENDED["fiber"]) * 100
        if fib_pct < 20:
            suggestions.append({
                "type": "suggestion",
                "title": "膳食纤维不足",
                "message": "建议增加蔬菜、水果或全谷物摄入，补充膳食纤维。",
            })

    # General suggestions based on dish count
    dish_count = len(dishes)
    if dish_count >= 4:
        suggestions.append({
            "type": "info",
            "title": "菜品丰富",
            "message": f"本餐包含 {dish_count} 道菜品，营养搭配较丰富。",
        })
    elif dish_count <= 1:
        suggestions.append({
            "type": "suggestion",
            "title": "建议增加菜品",
            "message": "本餐菜品较少，建议搭配蔬菜和蛋白质来源，营养更均衡。",
        })

    # Default positive message if no issues
    if not suggestions:
        suggestions.append({
            "type": "success",
            "title": "营养均衡",
            "message": "本餐营养搭配良好，继续保持！",
        })

    return suggestions


@bp.route("/quick-analyze", methods=["POST"])
@login_required
def quick_analyze():
    """Quick analysis with base64 image - optimized for demo.

    Combines capture and analyze in one step for uploaded images.
    """
    data = request.get_json() or {}
    image_base64 = data.get("image_base64", "")

    if not image_base64:
        return api_error("请提供图片数据")

    # Remove data URL prefix if present
    if "," in image_base64:
        image_base64 = image_base64.split(",", 1)[1]

    try:
        image_data = base64.b64decode(image_base64)
    except Exception:
        return api_error("图片数据格式无效")

    # Save to temp file
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(image_data)
        temp_path = f.name

    try:
        # Get dishes for recognition
        from app.models import Dish
        from app import db

        dishes = Dish.query.filter_by(is_active=True).limit(100).all()
        candidate_dishes = [
            {"name": d.name, "description": d.description or ""}
            for d in dishes
        ]

        # Call Qwen VL
        from app.services.qwen_vl import QwenVLService

        qwen = QwenVLService({
            "QWEN_API_KEY": current_app.config.get("QWEN_API_KEY"),
            "QWEN_API_URL": current_app.config.get("QWEN_API_URL"),
            "QWEN_MODEL": current_app.config.get("QWEN_MODEL"),
            "QWEN_TIMEOUT": 60,
            "QWEN_MAX_QPS": current_app.config.get("QWEN_MAX_QPS", 10),
        })

        result = qwen.recognize_dishes(temp_path, candidate_dishes)
        recognized_dishes = result.get("dishes", [])

        # Quick nutrition lookup
        nutrition_total = {
            "calories": 0, "protein": 0, "fat": 0,
            "carbohydrate": 0, "sodium": 0, "fiber": 0,
        }

        matched_dishes = []
        for rd in recognized_dishes:
            name = rd.get("name", "")
            if not name:
                continue

            # Simple match
            dish = Dish.query.filter(
                Dish.name.contains(name),
                Dish.is_active == True
            ).first()

            if dish:
                matched_dishes.append({
                    "id": dish.id,
                    "name": dish.name,
                    "confidence": rd.get("confidence", 0),
                    "calories": dish.calories or 0,
                    "protein": dish.protein or 0,
                    "fat": dish.fat or 0,
                    "carbohydrate": dish.carbohydrate or 0,
                })

                for key in nutrition_total:
                    val = getattr(dish, key, 0) or 0
                    nutrition_total[key] += val

        suggestions = generate_suggestions(nutrition_total, recognized_dishes)

        return api_ok({
            "recognized_dishes": recognized_dishes,
            "matched_dishes": matched_dishes,
            "nutrition": {
                "total": nutrition_total,
                "recommended": DAILY_RECOMMENDED,
            },
            "suggestions": suggestions,
        })

    except Exception as e:
        logger.error(f"Quick analyze failed: {e}", exc_info=True)
        return api_error(f"分析失败: {str(e)}")
    finally:
        try:
            os.unlink(temp_path)
        except:
            pass
