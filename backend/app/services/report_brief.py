"""Concise report copy derived exclusively from existing nutrient assessments."""

from app.services.report_assessment import assess_personal_report
from app.services.report_reference import LIMITATION


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


def brief_report_view(content):
    assessment = assess_personal_report(content)
    by_key = {field["key"]: {
        **field, "result": f"{field['value']:g}" if field["value"] is not None else "—",
        "icon": ICONS.get(field["key"]),
    } for field in assessment["fields"]}
    core = [by_key[key] for key in ("calories", "protein", "carbohydrate", "fiber", "fat", "cholesterol")]
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
    stats = content.get("meal_statistics") or {}
    breakfast = stats.get("breakfast_count")
    features = [{"icon": "breakfast", "title": "早餐记录", "tone": "neutral",
                 "status": f"{breakfast} 次" if breakfast is not None else "暂无记录",
                 "description": (f"本周期记录早餐 {breakfast} 次，继续关注早餐安排。" if breakfast is not None
                                 else "暂无早餐次数，待补充就餐记录。")}]
    for key in ("fat", "fiber", "protein"):
        field = by_key[key]
        advice = ADVICE[key].get(field["tone"])
        features.append({"icon": field["icon"], "title": field["label"] + "供给", "tone": field["tone"],
                         "status": field["status"], "description": advice[1] if advice else "资料或营养数据不足，暂不判断。"})
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
    return {"core": core, "assessment": assessment, "summary": summary, "energy": energy,
            "structure": [by_key[key] for key in ("carbohydrate", "protein", "fat", "fiber")],
            "features": features, "suggestions": suggestions,
            "next_summary": "；".join(item["title"] for item in suggestions) + "。",
            "score_dash": (assessment["score"] or 0) * 3.2673, "limitations": LIMITATION,
            "organization": organization, "school_emblem": "杭州第四中学" in organization or "杭州四中" in organization}
