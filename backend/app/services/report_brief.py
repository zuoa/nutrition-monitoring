"""Personal health report: nutrition, observed dining and sports evidence."""

import math

from app.nutrition_metadata import NUTRITION_FIELD_LABELS, NUTRITION_FIELD_UNITS
from app.services.report_assessment import assess_personal_report, report_nutrient_values
from app.services.report_reference import LIMITATION
from app.services.report_reference import select_reference
from app.services.report_dining_features import empty_dining_features


ICONS = {"calories": "energy", "protein": "protein", "carbohydrate": "carbohydrate", "fiber": "fiber", "fat": "fat"}
ADVICE = {
    "calories": {"low": ("补足能量供给", "规律安排三餐，适当增加主食和其他食物。"),
                 "high": ("调整能量供给", "合理安排食物份量，关注能量供给。"),
                 "ok": ("保持能量供给", "保持规律用餐和适宜的食物份量。")},
    "protein": {"low": ("补充蛋白质", "适当搭配鱼、肉、蛋和豆制品。"),
                "high": ("调整蛋白质供给", "适当调整鱼、肉、蛋、豆类份量，均衡搭配。"),
                "ok": ("保持蛋白质供给", "延续多样的蛋白质食物搭配。")},
    "fat": {"low": ("合理补充脂肪", "适当搭配坚果、豆类等食物，合理用油。"),
            "high": ("控制脂肪供给", "优先蒸、煮、炖，适量使用烹调油。"),
            "ok": ("保持脂肪供给", "继续合理用油，保持适宜的脂肪供能比例。")},
    "carbohydrate": {"low": ("合理增加主食", "适当增加谷薯类主食，搭配全谷杂粮。"),
                     "high": ("调整主食搭配", "适当调整主食份量，搭配蛋白质食物和蔬菜。"),
                     "ok": ("保持主食搭配", "保持适量主食，注意谷薯类食物多样性。")},
    "fiber": {"low": ("补充膳食纤维", "适当增加全谷物、蔬菜和豆类食物。"),
              "ok": ("保持纤维供给", "继续搭配全谷物、蔬菜和豆类食物。")},
}


def _comparison_chart(core, assessment, previous):
    profile = assessment["profile"]
    row = select_reference(profile) or {}
    points = []
    for index, field in enumerate(core):
        lower, upper = field.get("lower"), field.get("upper")
        reference = (lower + upper) / 2 if lower is not None and upper is not None else lower
        if field["key"] == "calcium":
            reference = field.get("chart_reference", row.get("calcium"))
        if not reference:
            continue
        for series, value in (("current", field["value"]), ("previous", previous.get(field["key"]))):
            if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                points.append({"index": index, "x": 42 + index * 57, "ratio": round(value / reference * 100, 1),
                               "series": series, "label": field["label"], "value": value, "unit": field["unit"]})
    ceiling = max(150, max((point["ratio"] for point in points), default=0))
    ceiling = (int(ceiling / 50) + 1) * 50
    for point in points:
        point["y"] = round(232 - point["ratio"] / ceiling * 200, 1)
    paths = []
    for series in ("current", "previous"):
        segment, last = [], -2
        for point in [point for point in points if point["series"] == series]:
            if point["index"] != last + 1 and segment:
                paths.append({"series": series, "points": " ".join(segment)})
                segment = []
            segment.append(f"{point['x']},{point['y']}")
            last = point["index"]
        if segment:
            paths.append({"series": series, "points": " ".join(segment)})
    return {"points": points, "paths": paths, "labels": [{"x": 42 + i * 57, "text": "碳水" if field["key"] == "carbohydrate" else field["label"]} for i, field in enumerate(core)],
            "ticks": [{"value": round(ceiling * i / 4), "y": 232 - i * 50} for i in range(5)],
            "reference_y": round(232 - 100 / ceiling * 200, 1)}


def brief_report_view(content):
    assessment = assess_personal_report(content)
    by_key = {field["key"]: {
        **field, "result": f"{field['value']:g}" if field["value"] is not None else "—",
        "icon": ICONS.get(field["key"]),
    } for field in assessment["fields"]}
    calcium = report_nutrient_values(content).get("calcium")
    by_key["calcium"] = {
        "key": "calcium", "label": NUTRITION_FIELD_LABELS["calcium"], "unit": NUTRITION_FIELD_UNITS["calcium"],
        "value": calcium, "result": f"{calcium:g}" if calcium is not None else "—",
        "tone": "unknown", "status": "仅展示" if calcium is not None else "暂无数据", "icon": None,
    }
    core = [by_key[key] for key in ("calories", "protein", "carbohydrate", "fiber", "fat", "calcium")]
    assessed = [field for field in core if field["tone"] != "unknown"]
    passed = [field["label"] for field in assessed if field["tone"] == "ok"]
    focus = [field for field in assessed if field["tone"] in ("low", "high")]
    parts = []
    if passed:
        parts.append("、".join(passed) + "达标")
    if focus:
        parts.append("、".join(field["label"] + field["status"] for field in focus))
    if len(assessed) < assessment["reference_count"]:
        parts.append("部分指标待评价" if assessed else "当前资料不足，暂不作营养达标判断")
    summary = "；".join(parts) + "。"
    dining = content.get("dining_features") or empty_dining_features()
    features = dining["items"]
    suggestions = []
    for field in focus + [field for field in assessed if field["tone"] == "ok"]:
        advice = ADVICE.get(field["key"], {}).get(field["tone"])
        if advice:
            suggestions.append({"icon": "cooking-pot" if field["key"] == "fat" else field["icon"],
                                "title": advice[0], "text": advice[1]})
    if not assessment["level"]:
        suggestions.insert(0, {"icon": "fiber", "title": "补充评价资料", "text": assessment["reason"] + "，补充后再评价。"})
    suggestions = suggestions[:4]
    suggestions.insert(0, {"icon": "breakfast", "title": "合理安排早餐", "text": "规律安排早餐，关注每日三餐搭配。"})
    # Use the stored common-sample energy distribution, never independent nutrient averages.
    energy = []
    percentages = (content.get("energy_summary") or {}).get("percentages")
    if isinstance(percentages, dict):
        keys = ("carbohydrate", "fat", "protein")
        values = [percentages.get(key) for key in keys]
        if all(isinstance(value, (float, int)) and 0 <= value <= 100 for value in values) and abs(sum(values) - 100) < .1:
            offset = 0
            for key, value, color in zip(keys, values, ("#F58A16", "#147A49", "#1764CE")):
                energy.append({"label": by_key[key]["label"], "value": value, "color": color,
                               "dash": value * 2.7646, "offset": -offset * 2.7646})
                offset += value
    organization = content.get("organization_name") or "校园膳食营养监测"
    sports = content.get("sports") or {}
    comparison = content.get("period_comparison") or {}
    monthly = content.get("report_type") == "personal_monthly"
    period_label, previous_label = ("本月", "上月") if monthly else ("本周", "上周")
    stats = content.get("meal_statistics") or {}
    current_meals, old_meals = stats.get("meal_count"), comparison.get("meal_count")
    meal_delta = current_meals - old_meals if current_meals is not None and old_meals is not None else None
    meal_counts = {}
    for slot in ("breakfast", "lunch", "dinner"):
        count = sum(record["slot"] == slot for record in stats["records"]) if "records" in stats else None
        meal_counts[slot] = stats.get(f"{slot}_count", count)
    highlights = [{"title": "、".join(passed) + "达到参考水平", "text": "继续保持均衡搭配。"}] if passed else []
    if sports.get("practice_count"):
        text = f"较{previous_label}增加 {sports['practice_delta']} 次。" if sports.get("previous_has_records") and sports.get("practice_delta", 0) > 0 else "保持参与，逐步形成运动习惯。"
        highlights.append({"title": f"体育练习 {sports['practice_count']} 次", "text": text})
        highlights.append({"title": f"参与 {sports['project_count']} 类运动项目", "text": "常练项目：" + "、".join(item["name"] for item in sports.get("top_projects", [])) + "。"})
    if len(highlights) < 3:
        variety = next((item for item in features if item["key"] == "variety"), {})
        unique = (variety.get("evidence") or {}).get("unique_dishes")
        if unique:
            highlights.append({"title": f"选过 {unique} 种菜品", "text": "继续尝试多样食物。"})
    if not highlights:
        highlights.append({"title": "持续记录，关注成长", "text": "补充就餐与体育记录后，可查看个人亮点。"})
    health_suggestions = [item for item in suggestions if item["icon"] != "breakfast"][:1] + [suggestions[0]]
    health_suggestions += [{"icon": "running", "title": "运动保持规律", "text": "分散安排练习，注意休息，避免集中堆量。"},
                           {"icon": "dumbbell", "title": "项目均衡搭配", "text": "结合自身情况，交替安排跳绳、跑步与柔韧练习。"}]
    family = [{"icon": "cooking-pot", "title": "家中餐食合理用油", "text": "优先蒸、煮、炖，适量使用烹调油。"},
              {"icon": "fiber", "title": "家校互补", "text": "家庭用餐搭配蔬菜、全谷及奶豆类食物。"},
              {"icon": "family", "title": "一起关注饮食与运动", "text": "结合其他窗口及家庭用餐，陪伴孩子规律活动。"}]
    return {"core": core, "assessment": assessment, "summary": summary, "energy": energy,
            "structure": [by_key[key] for key in ("carbohydrate", "protein", "fat", "fiber")],
            "features": features, "suggestions": suggestions, "health_suggestions": health_suggestions, "family": family,
            "sports": sports, "comparison": comparison, "meal_delta": meal_delta, "meal_counts": meal_counts,
            "chart": _comparison_chart(core, assessment, comparison.get("nutrients") or {}),
            "dining": dining, "highlights": highlights[:3], "period_label": period_label, "previous_label": previous_label,
            "next_summary": "；".join(item["title"] for item in suggestions) + "。",
            "limitations": LIMITATION,
            "organization": organization, "school_emblem": "杭州第四中学" in organization or "杭州四中" in organization}
