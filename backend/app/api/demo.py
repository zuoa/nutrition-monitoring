"""Demo API for real-time camera capture and analysis."""
import base64
import json
import logging
import os
import tempfile
from datetime import datetime

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


def _as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_nutrition_map(values: dict) -> dict:
    return {key: _as_float(value) for key, value in values.items()}


def _normalize_recognized_dishes(dishes: list) -> list:
    normalized = []
    for dish in dishes:
        normalized.append({
            "name": dish.get("name", ""),
            "confidence": _as_float(dish.get("confidence", 0)),
        })
    return normalized


def _extract_image_data_from_request():
    if "image" in request.files:
        return request.files["image"].read()

    data = request.get_json() if request.is_json else request.form
    image_base64 = (data or {}).get("image_base64", "")
    if not image_base64:
        return None

    if "," in image_base64:
        image_base64 = image_base64.split(",", 1)[1]
    return base64.b64decode(image_base64)


def _load_demo_candidate_dishes(reference_date=None):
    from app.models import DailyMenu, Dish

    dishes = []
    if reference_date is not None:
        menu = DailyMenu.query.filter_by(menu_date=reference_date).first()
        if menu and not menu.is_default and menu.dish_ids:
            dishes = Dish.query.filter(
                Dish.id.in_(menu.dish_ids),
                Dish.is_active.is_(True),
            ).all()

    if not dishes:
        dishes = Dish.query.filter(Dish.is_active.is_(True)).all()

    return dishes


def _find_matched_dish(recognized_name: str, dishes: list):
    normalized_name = str(recognized_name or "").strip().lower()
    if not normalized_name:
        return None

    exact_match = next(
        (dish for dish in dishes if str(dish.name or "").strip().lower() == normalized_name),
        None,
    )
    if exact_match:
        return exact_match

    contains_match = next(
        (
            dish
            for dish in dishes
            if normalized_name in str(dish.name or "").strip().lower()
            or str(dish.name or "").strip().lower() in normalized_name
        ),
        None,
    )
    return contains_match


def _build_demo_analysis_payload(image_data: bytes, *, reference_date=None, include_image_base64: bool = False) -> dict:
    from app.services.dish_recognition import DishRecognitionService

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(image_data)
        temp_path = f.name

    try:
        dishes = _load_demo_candidate_dishes(reference_date=reference_date)
        candidate_dishes = [
            {"id": dish.id, "name": dish.name, "description": dish.description or ""}
            for dish in dishes
        ]

        result = DishRecognitionService(current_app.config).recognize_dishes(temp_path, candidate_dishes)
        recognized_dishes = _normalize_recognized_dishes(result.get("dishes", []))

        nutrition_total = {
            "calories": 0,
            "protein": 0,
            "fat": 0,
            "carbohydrate": 0,
            "sodium": 0,
            "fiber": 0,
        }

        matched_dishes = []
        matched_ids = set()
        for recognized in recognized_dishes:
            matched = _find_matched_dish(recognized.get("name", ""), dishes)
            if not matched or matched.id in matched_ids:
                continue

            matched_ids.add(matched.id)
            matched_dishes.append({
                "id": matched.id,
                "name": matched.name,
                "category": matched.category.value if matched.category else None,
                "confidence": _as_float(recognized.get("confidence", 0)),
                "price": float(matched.price) if matched.price else 0,
                "calories": _as_float(matched.calories),
                "protein": _as_float(matched.protein),
                "fat": _as_float(matched.fat),
                "carbohydrate": _as_float(matched.carbohydrate),
                "sodium": _as_float(matched.sodium),
                "fiber": _as_float(matched.fiber),
            })

            for key in nutrition_total:
                nutrition_total[key] += _as_float(getattr(matched, key, 0))

        suggestions = generate_suggestions(nutrition_total, recognized_dishes)
        nutrition_total = _normalize_nutrition_map(nutrition_total)

        payload = {
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
        }
        try:
            from app.services.demo_agent import DemoAgentService

            payload["follow_up_questions"] = DemoAgentService({
                "OPENAI_API_KEY": current_app.config.get("OPENAI_API_KEY"),
                "OPENAI_BASE_URL": current_app.config.get("OPENAI_BASE_URL"),
                "OPENAI_MODEL": current_app.config.get("OPENAI_MODEL"),
                "OPENAI_TIMEOUT": current_app.config.get("OPENAI_TIMEOUT", 30),
            }).suggest_follow_up_questions_for_analysis(payload)
        except Exception as exc:
            logger.warning("Failed to build initial demo follow-up questions: %s", exc)
        if include_image_base64:
            payload["image_base64"] = base64.b64encode(image_data).decode("utf-8")
        return payload
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


@bp.route("/cameras", methods=["GET"])
@login_required
def list_cameras():
    """List available Hikvision cameras from config."""
    cameras_raw = current_app.config.get("HIKVISION_CAMERAS", "{}")
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
    image_data = _extract_image_data_from_request()

    if not image_data:
        return api_error("请提供图片数据")

    try:
        return api_ok(
            _build_demo_analysis_payload(
                image_data,
                reference_date=datetime.now().date(),
                include_image_base64=True,
            )
        )

    except Exception as e:
        logger.error(f"Failed to analyze image: {e}", exc_info=True)
        return api_error(f"分析失败: {str(e)}")


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

    try:
        return api_ok(
            _build_demo_analysis_payload(
                image_data,
                reference_date=datetime.now().date(),
            )
        )

    except Exception as e:
        logger.error(f"Quick analyze failed: {e}", exc_info=True)
        return api_error(f"分析失败: {str(e)}")


@bp.route("/chat", methods=["POST"])
@login_required
def chat_with_agent():
    """Chat with the nutrition insight agent using current analysis context."""
    data = request.get_json() or {}
    message = (data.get("message") or "").strip()
    history = data.get("history") or []
    analysis_result = data.get("analysis_result") or {}

    if not message:
        return api_error("请输入问题")

    try:
        from app.services.demo_agent import DemoAgentService

        agent = DemoAgentService({
            "OPENAI_API_KEY": current_app.config.get("OPENAI_API_KEY"),
            "OPENAI_BASE_URL": current_app.config.get("OPENAI_BASE_URL"),
            "OPENAI_MODEL": current_app.config.get("OPENAI_MODEL"),
            "OPENAI_TIMEOUT": current_app.config.get("OPENAI_TIMEOUT", 30),
        })

        reply_payload = agent.reply(
            message=message,
            history=history if isinstance(history, list) else [],
            analysis_result=analysis_result if isinstance(analysis_result, dict) else {},
        )

        return api_ok({
            "reply": reply_payload.get("reply", ""),
            "follow_up_questions": reply_payload.get("follow_up_questions", []),
            "answered_at": datetime.now().isoformat(),
            "agent": "nutrition-insight-agent",
        })
    except ValueError as e:
        return api_error(str(e)), 503
    except Exception as e:
        logger.error("Demo agent chat failed: %s", e, exc_info=True)
        return api_error(f"Agent 对话失败: {str(e)}")
