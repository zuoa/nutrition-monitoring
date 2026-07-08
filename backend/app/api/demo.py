"""Demo API for real-time camera capture and analysis."""
import base64
import logging
import os
import tempfile
import time
from datetime import datetime

from flask import Blueprint, request, current_app

from app.services.video_sources import VideoSourceConfigError, VideoSourceManager
from app.utils.jwt_utils import login_required, api_ok, api_error
from app.nutrition_metadata import DAILY_RECOMMENDED, NUTRITION_FIELD_KEYS

bp = Blueprint("demo", __name__)
logger = logging.getLogger(__name__)


def _elapsed_ms(started: float) -> int:
    return int(round((time.perf_counter() - started) * 1000))


def _as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_nutrition_map(values: dict) -> dict:
    return {key: _as_float(value) for key, value in values.items()}


def _normalize_bbox(bbox: object):
    if not isinstance(bbox, dict):
        return None

    try:
        x1 = float(bbox.get("x1"))
        y1 = float(bbox.get("y1"))
        x2 = float(bbox.get("x2"))
        y2 = float(bbox.get("y2"))
    except (TypeError, ValueError):
        return None

    left = min(x1, x2)
    top = min(y1, y2)
    right = max(x1, x2)
    bottom = max(y1, y2)
    if right - left < 2 or bottom - top < 2:
        return None

    return {
        "x1": round(left, 2),
        "y1": round(top, 2),
        "x2": round(right, 2),
        "y2": round(bottom, 2),
    }


def _normalize_recognized_dishes(dishes: list) -> list:
    normalized = []
    for dish in dishes:
        bbox = _normalize_bbox(dish.get("bbox"))
        normalized.append({
            "name": dish.get("name", ""),
            "confidence": _as_float(dish.get("confidence", 0)),
            "bbox": bbox,
            "bbox_source": str(dish.get("bbox_source") or ("pixels" if bbox else "")),
            "position": str(dish.get("position") or ""),
            "notes": str(dish.get("notes") or ""),
        })
    return normalized


def _normalize_regions(items: list) -> list:
    normalized = []
    for index, item in enumerate(items or [], start=1):
        if not isinstance(item, dict):
            continue

        bbox = _normalize_bbox(item.get("bbox"))
        if not bbox:
            continue

        normalized.append({
            "index": int(item.get("index") or index),
            "bbox": bbox,
            "confidence": _as_float(item.get("confidence", item.get("score", 0))),
            "source": str(item.get("source") or ""),
        })
    return normalized


def _normalize_region_results(items: list) -> list:
    normalized = []
    for index, item in enumerate(items or [], start=1):
        if not isinstance(item, dict):
            continue

        bbox = _normalize_bbox(item.get("bbox"))
        if not bbox:
            continue

        normalized.append({
            "index": int(item.get("index") or index),
            "bbox": bbox,
            "matched_name": str(item.get("matched_name") or item.get("dish_name") or ""),
            "confidence": _as_float(item.get("confidence", 0)),
            "notes": str(item.get("notes") or ""),
            "timings_ms": item.get("timings_ms") if isinstance(item.get("timings_ms"), dict) else {},
        })
    return normalized


def _extract_preview_regions(result: dict) -> list:
    direct_regions = _normalize_regions(result.get("regions") or [])
    if direct_regions:
        return direct_regions

    raw_response = result.get("raw_response") or {}
    mode = str(raw_response.get("mode") or "")
    if mode == "region_two_stage":
        return _normalize_regions(raw_response.get("regions") or [])
    if mode == "full_image_fallback":
        region_detection = raw_response.get("region_detection") or {}
        return _normalize_regions(region_detection.get("regions") or [])
    return []


def _extract_preview_region_results(result: dict) -> list:
    direct_region_results = _normalize_region_results(result.get("region_results") or [])
    if direct_region_results:
        return direct_region_results

    raw_response = result.get("raw_response") or {}
    mode = str(raw_response.get("mode") or "")
    if mode == "region_two_stage":
        return _normalize_region_results(raw_response.get("regions") or [])
    if mode == "full_image_fallback":
        region_detection = raw_response.get("region_detection") or {}
        return _normalize_region_results(region_detection.get("regions") or [])
    return []


def _build_preview_suggestions(
    *,
    has_dishes: bool,
    matched_dishes: list,
    preview_regions: list,
    preview_region_results: list,
    nutrition_total: dict,
) -> list:
    if not has_dishes:
        return [{
            "type": "info",
            "title": "当前未发现菜品",
            "message": "预览画面里还没有稳定检测到菜品，系统会继续等待下一帧。",
        }]

    if not matched_dishes:
        has_region_hits = bool(preview_regions) or bool(preview_region_results)
        return [{
            "type": "info",
            "title": "检测到菜品待复核",
            "message": (
                "画面里已检测到菜区，但暂未匹配到可确认的菜品。"
                if has_region_hits
                else "画面里可能有菜品，但当前还没有稳定匹配结果。"
            ) + " 请调整角度、补光后重试，或进入人工复核。",
        }]

    return generate_suggestions(nutrition_total, matched_dishes)


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
    from app.services.runtime_config import get_effective_config

    dishes = []
    if reference_date is not None:
        cfg = get_effective_config(current_app.config)
        menu = DailyMenu.query.filter_by(menu_date=reference_date).first()
        if menu and not menu.is_default:
            ordered_ids = menu.aggregated_dish_ids(cfg)
            if ordered_ids:
                matched = Dish.query.filter(
                    Dish.id.in_(ordered_ids),
                    Dish.is_active.is_(True),
                ).all()
                dish_by_id = {dish.id: dish for dish in matched}
                dishes = [dish_by_id[dish_id] for dish_id in ordered_ids if dish_id in dish_by_id]

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


def _build_demo_analysis_payload(
    image_data: bytes,
    *,
    reference_date=None,
    include_image_base64: bool = False,
    include_follow_up_questions: bool = True,
    initial_timings_ms: dict | None = None,
    initial_started_at: float | None = None,
) -> dict:
    from app.services.dish_recognition import DishRecognitionService

    total_started = initial_started_at or time.perf_counter()
    timings_ms = dict(initial_timings_ms or {})
    write_temp_started = time.perf_counter()
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(image_data)
        temp_path = f.name
    timings_ms["write_temp"] = _elapsed_ms(write_temp_started)

    try:
        load_candidates_started = time.perf_counter()
        dishes = _load_demo_candidate_dishes(reference_date=reference_date)
        candidate_dishes = [
            {"id": dish.id, "name": dish.name, "description": dish.description or ""}
            for dish in dishes
        ]
        timings_ms["load_candidates"] = _elapsed_ms(load_candidates_started)

        recognize_started = time.perf_counter()
        result = DishRecognitionService(current_app.config).recognize_dishes(temp_path, candidate_dishes)
        timings_ms["recognize"] = _elapsed_ms(recognize_started)
        recognition_timings = result.get("timings_ms") if isinstance(result.get("timings_ms"), dict) else {}
        if recognition_timings:
            timings_ms["recognition"] = recognition_timings

        normalize_started = time.perf_counter()
        recognized_dishes = _normalize_recognized_dishes(result.get("dishes", []))
        preview_regions = _extract_preview_regions(result)
        preview_region_results = _extract_preview_region_results(result)
        has_dishes = bool(recognized_dishes) or bool(preview_regions) or bool(preview_region_results)
        timings_ms["normalize_recognition"] = _elapsed_ms(normalize_started)

        match_started = time.perf_counter()
        nutrition_total = {key: 0 for key in NUTRITION_FIELD_KEYS}

        matched_dishes = []
        matched_ids = set()
        if has_dishes:
            for recognized in recognized_dishes:
                matched = _find_matched_dish(recognized.get("name", ""), dishes)
                if not matched or matched.id in matched_ids:
                    continue

                matched_ids.add(matched.id)
                matched_payload = {
                    "id": matched.id,
                    "name": matched.name,
                    "category": matched.category.value if matched.category else None,
                    "confidence": _as_float(recognized.get("confidence", 0)),
                    "price": float(matched.price) if matched.price else 0,
                    "bbox": recognized.get("bbox"),
                    "bbox_source": recognized.get("bbox_source", ""),
                    "position": recognized.get("position", ""),
                }
                for key in NUTRITION_FIELD_KEYS:
                    matched_payload[key] = _as_float(getattr(matched, key, 0))
                matched_dishes.append(matched_payload)

                for key in nutrition_total:
                    nutrition_total[key] += _as_float(getattr(matched, key, 0))
        timings_ms["match_nutrition"] = _elapsed_ms(match_started)

        suggestions_started = time.perf_counter()
        suggestions = _build_preview_suggestions(
            has_dishes=has_dishes,
            matched_dishes=matched_dishes,
            preview_regions=preview_regions,
            preview_region_results=preview_region_results,
            nutrition_total=nutrition_total,
        )
        nutrition_total = _normalize_nutrition_map(nutrition_total)
        timings_ms["suggestions"] = _elapsed_ms(suggestions_started)

        payload = {
            "has_dishes": has_dishes,
            "recognized_dishes": recognized_dishes,
            "matched_dishes": matched_dishes,
            "regions": preview_regions,
            "region_results": preview_region_results,
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
        if include_follow_up_questions and matched_dishes:
            try:
                from app.services.demo_agent import DemoAgentService

                follow_up_started = time.perf_counter()
                payload["follow_up_questions"] = DemoAgentService({
                    "OPENAI_API_KEY": current_app.config.get("OPENAI_API_KEY"),
                    "OPENAI_BASE_URL": current_app.config.get("OPENAI_BASE_URL"),
                    "OPENAI_MODEL": current_app.config.get("OPENAI_MODEL"),
                    "OPENAI_TIMEOUT": current_app.config.get("OPENAI_TIMEOUT", 30),
                }).suggest_follow_up_questions_for_analysis(payload)
                timings_ms["follow_up"] = _elapsed_ms(follow_up_started)
            except Exception as exc:
                logger.warning("Failed to build initial demo follow-up questions: %s", exc)
                timings_ms["follow_up"] = _elapsed_ms(follow_up_started) if "follow_up_started" in locals() else 0
        else:
            payload["follow_up_questions"] = []
            timings_ms["follow_up"] = 0
        if include_image_base64:
            image_base64_started = time.perf_counter()
            payload["image_base64"] = base64.b64encode(image_data).decode("utf-8")
            timings_ms["image_base64"] = _elapsed_ms(image_base64_started)
        timings_ms["total"] = _elapsed_ms(total_started)
        payload["timings_ms"] = timings_ms
        logger.info(
            "Demo analysis timings_ms=%s matched=%s regions=%s follow_up=%s",
            timings_ms,
            len(matched_dishes),
            len(preview_regions),
            include_follow_up_questions,
        )
        return payload
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


@bp.route("/cameras", methods=["GET"])
@login_required
def list_cameras():
    try:
        payload = VideoSourceManager(current_app.config).list_cameras()
    except VideoSourceConfigError:
        payload = {
            "active_video_source": None,
            "supports_snapshot": False,
            "cameras": [
                {"channel_id": "1", "host": "", "port": 80, "name": "演示摄像头 1", "supports_snapshot": False},
                {"channel_id": "2", "host": "", "port": 80, "name": "演示摄像头 2", "supports_snapshot": False},
            ],
        }
    return api_ok(payload)


@bp.route("/capture", methods=["POST"])
@login_required
def capture_snapshot():
    """Capture a snapshot from the configured video source or a temporary Hikvision override.

    Request body:
        - channel_id: Optional camera channel ID
        - host: Optional temporary camera IP override
        - port: Optional temporary camera port override
        - username: Optional temporary camera username override
        - password: Optional temporary camera password override
    """
    data = request.get_json() or {}
    try:
        result = VideoSourceManager(current_app.config).capture_snapshot(
            channel_id=str(data.get("channel_id", "") or ""),
            host=str(data.get("host", "") or ""),
            port=data.get("port"),
            username=str(data.get("username", "") or ""),
            password=str(data.get("password", "") or ""),
        )
        return api_ok({
            "image_base64": base64.b64encode(result["content"]).decode("utf-8"),
            "content_type": result.get("content_type", "image/jpeg"),
            "captured_at": datetime.now().isoformat(),
            "channel_id": result.get("channel_id", str(data.get("channel_id", "") or "")),
        })
    except VideoSourceConfigError as e:
        return api_error(str(e))
    except Exception as e:
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
                include_follow_up_questions=True,
            )
        )

    except Exception as e:
        logger.error(f"Failed to analyze image: {e}", exc_info=True)
        return api_error(f"分析失败: {str(e)}")


def generate_suggestions(nutrition: dict, dishes: list) -> list:
    """Generate nutrition suggestions based on analysis."""
    suggestions = []

    # Check each nutrient
    if nutrition.get("calories", 0) > 0:
        cal_pct = (nutrition["calories"] / DAILY_RECOMMENDED["calories"]) * 100
        if cal_pct > 40:
            suggestions.append({
                "type": "warning",
                "title": "能量较高",
                "message": f"本餐能量约 {nutrition['calories']} kcal，占全天建议的 {cal_pct:.0f}%，建议适当控制。",
            })
        elif cal_pct < 20:
            suggestions.append({
                "type": "info",
                "title": "能量适中",
                "message": f"本餐能量约 {nutrition['calories']} kcal，占全天建议的 {cal_pct:.0f}%，搭配合理。",
            })

    if nutrition.get("protein", 0) > 0:
        pro_pct = (nutrition["protein"] / DAILY_RECOMMENDED["protein"]) * 100
        if pro_pct < 15:
            suggestions.append({
                "type": "suggestion",
                "title": "蛋白质摄入不足",
                "message": "建议增加优质蛋白摄入，如瘦肉、鱼类、蛋类或豆制品。",
            })

    if nutrition.get("fat", 0) > 0:
        fat_pct = (nutrition["fat"] / DAILY_RECOMMENDED["fat"]) * 100
        if fat_pct > 50:
            suggestions.append({
                "type": "warning",
                "title": "脂肪含量较高",
                "message": "本餐脂肪含量较高，建议后续餐次减少油腻食物。",
            })

    if nutrition.get("sodium", 0) > 0:
        sod_pct = (nutrition["sodium"] / DAILY_RECOMMENDED["sodium"]) * 100
        if sod_pct > 50:
            suggestions.append({
                "type": "warning",
                "title": "钠含量偏高",
                "message": "本餐钠含量偏高，建议多喝水，后续餐次选择清淡饮食。",
            })

    if nutrition.get("fiber", 0) > 0:
        fib_pct = (nutrition["fiber"] / DAILY_RECOMMENDED["fiber"]) * 100
        if fib_pct < 20:
            suggestions.append({
                "type": "suggestion",
                "title": "膳食纤维不足",
                "message": "建议增加蔬菜、水果或全谷物摄入，补充膳食纤维。",
            })

    if nutrition.get("added_sugar", 0) > DAILY_RECOMMENDED["added_sugar"] * 0.35:
        suggestions.append({
            "type": "warning",
            "title": "添加糖偏高",
            "message": "本餐添加糖占比较高，建议减少甜点、含糖饮料或糖醋类菜品。",
        })

    if nutrition.get("calcium", 0) > 0 and nutrition["calcium"] < DAILY_RECOMMENDED["calcium"] * 0.15:
        suggestions.append({
            "type": "suggestion",
            "title": "钙摄入不足",
            "message": "建议搭配奶类、豆制品或深绿色蔬菜，补充钙摄入。",
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
    started = time.perf_counter()
    data = request.get_json() or {}
    image_base64 = data.get("image_base64", "")
    include_follow_up_questions = data.get("include_follow_up_questions", True) is not False

    if not image_base64:
        return api_error("请提供图片数据")

    # Remove data URL prefix if present
    if "," in image_base64:
        image_base64 = image_base64.split(",", 1)[1]

    try:
        decode_started = time.perf_counter()
        image_data = base64.b64decode(image_base64)
        decode_ms = _elapsed_ms(decode_started)
    except Exception:
        return api_error("图片数据格式无效")

    try:
        return api_ok(
            _build_demo_analysis_payload(
                image_data,
                reference_date=datetime.now().date(),
                include_follow_up_questions=include_follow_up_questions,
                initial_timings_ms={"decode": decode_ms},
                initial_started_at=started,
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
