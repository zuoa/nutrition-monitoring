"""Fixed, configurable report advice; select once when persisting a report."""

from copy import deepcopy
import json
from pathlib import Path
import random
import re

CATALOG = json.loads(Path(__file__).with_name("report_advice_catalog.json").read_text(encoding="utf-8"))
RULES = {rule["id"]: rule for rule in CATALOG}
NUTRIENTS = ("calories", "protein", "carbohydrate", "fiber", "fat")
LABELS = {"diet": "膳食建议", "sports": "运动建议", "family": "家庭协同建议", "coordination": "食动配合"}
ICONS = {"diet": "fiber", "sports": "running", "family": "family", "coordination": "family"}
TOKEN = re.compile(r"【([^【】]+)】")
FALLBACK = {
    "diet": "结合实际饮食核实记录，完善餐食搭配。",
    "sports": "了解课内外活动情况，支持规律参与。",
    "family": "家校共同保持均衡饮食与规律活动。",
    "coordination": "结合实际餐食与活动安排提供支持。",
}


def normalize_phrases(value, rule):
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("话术必须是多行文本或文本列表")
    phrases = list(dict.fromkeys(line.strip() for item in value for line in item.splitlines() if line.strip()))
    if len(phrases) > 100:
        raise ValueError("每条策略最多配置100条话术")
    for phrase in phrases:
        if len(phrase) > 2000:
            raise ValueError("每条话术最多2000字")
        tokens = set(TOKEN.findall(phrase))
        remainder = TOKEN.sub("", phrase)
        if tokens - set(rule["placeholders"]) or any(char in remainder for char in "【】{}"):
            raise ValueError("话术含不支持的占位符；可用：" + ("、".join(rule["placeholders"]) or "无"))
    return phrases


def validate_report_advice_rules(value, current=None):
    """Persist only editable fields; conditions and precedence belong to code."""
    if not isinstance(value, dict) or set(value) - set(RULES):
        raise ValueError("建议配置包含未知策略编号")
    result = deepcopy(current or {})
    for key, change in value.items():
        rule = RULES[key]
        if not isinstance(change, dict) or set(change) - {"description", "enabled", "phrases"}:
            raise ValueError(f"{key}：仅可修改策略说明、启停和话术")
        merged = {name: rule[name] for name in ("description", "enabled", "phrases")}
        merged.update(result.get(key, {}))
        merged.update(change)
        if type(merged["enabled"]) is not bool:
            raise ValueError(f"{key}：启停必须为布尔值")
        if not isinstance(merged["description"], str) or len(merged["description"].strip()) > 500:
            raise ValueError(f"{key}：策略说明最多500字")
        merged["description"] = merged["description"].strip()
        try:
            merged["phrases"] = normalize_phrases(merged["phrases"], rule)
        except ValueError as exc:
            raise ValueError(f"{key}：{exc}") from exc
        if merged["enabled"] and not merged["phrases"]:
            raise ValueError(f"{key}：启用策略至少需要一条话术")
        result[key] = merged
    return result


def get_report_advice_rules(config):
    overrides = config.get("REPORT_ADVICE_RULES") or {}
    try:
        overrides = validate_report_advice_rules(overrides)
    except ValueError:
        overrides = {}
    return [{**deepcopy(rule), **overrides.get(rule["id"], {})} for rule in CATALOG]


def _group_strategy_events(content, summary, tones, sports):
    total = content.get("student_count") or 0
    participants = sports.get("participants")
    coverage = participants / total if total and participants is not None else None
    sufficient = coverage is not None and coverage >= 0.5
    low_sports = coverage is not None and coverage < 0.5
    body_counts = summary.get("body_distribution") or {}
    measured = sum(body_counts.get(key, 0) for key in ("thinness", "reference", "overweight", "obesity"))

    def body_share(count):
        return measured > 0 and count / measured >= 0.5
    heavy = body_share(body_counts.get("overweight", 0) + body_counts.get("obesity", 0))
    thin = body_share(body_counts.get("thinness", 0))
    normal = body_share(body_counts.get("reference", 0))
    mixed_body = measured > 0 and not any((heavy, thin, normal))
    low_count = sum(tone == "low" for tone in tones.values())
    high_count = sum(tone == "high" for tone in tones.values())
    nutrition_normal = all(tone == "ok" for tone in tones.values())
    slots = summary.get("meal_slot_counts") or {}
    diners = summary.get("participating_students")
    regular_meals = bool(total and diners is not None and diners / total >= 0.5
                         and all(slots.get(slot, 0) > 0 for slot in ("breakfast", "lunch", "dinner")))
    low_meals = diners is not None and total > 0 and diners / total < 0.5
    missing_records = (participants == 0 if participants is not None else sports.get("practice_count") is None) or diners == 0
    delta = sports.get("practice_delta") if sports.get("previous_has_records") else None
    top = (sports.get("top_projects") or [{}])[0]
    practice_count = sports.get("practice_count") or 0
    run_count = sports.get("run_count") or 0
    run_complete = run_count > 0 and sports.get("run_energy_count") == run_count
    return {
        "multi_low": low_count >= 2, "multi_high": high_count >= 2,
        "multi_normal": nutrition_normal, "evaluated_normal": any(tone == "ok" for tone in tones.values()) and not (low_count or high_count),
        "meal_low": low_meals or bool((summary.get("advice_signals") or {}).get("breakfast_low")),
        "sports_coverage_sufficient": sufficient, "sports_coverage_low": low_sports,
        "sports_stable": delta == 0 if delta is not None else False,
        "sports_concentrated": practice_count > 0 and top.get("count", 0) / practice_count >= 0.6,
        "sports_few_students": coverage is not None and 0 < coverage < 0.25,
        "heavy_low_sports_high_supply_regular": heavy and low_sports and (tones.get("calories") == "high" or tones.get("fat") == "high") and regular_meals,
        "heavy_sports_high_supply": heavy and sufficient and high_count > 0,
        "thin_low_sports_low_supply": thin and low_sports and (tones.get("calories") == "low" or tones.get("protein") == "low"),
        "thin_sports_meal_low": thin and sufficient and low_meals,
        "normal_low_sports_normal_nutrition": normal and low_sports and nutrition_normal,
        "normal_sports_normal_nutrition": normal and sufficient and nutrition_normal,
        "mixed_body_mixed_nutrition": mixed_body and low_count > 0 and high_count > 0,
        "low_sports_low_fiber_regular": low_sports and tones.get("fiber") == "low" and regular_meals,
        "incomplete_records": missing_records,
        "normal_sports_regular": nutrition_normal and sufficient and regular_meals,
        "normal_low_sports": nutrition_normal and low_sports,
        "low_nutrition_low_sports": low_count > 0 and low_sports,
        "low_nutrition_sports": low_count > 0 and sufficient,
        "high_nutrition_low_sports": high_count > 0 and low_sports,
        "high_nutrition_sports": high_count > 0 and sufficient,
        "low_meals_sports_complete": low_meals and sufficient,
        "regular_meals_low_sports": regular_meals and low_sports,
        "sports_down_high_nutrition": delta is not None and delta < 0 and high_count > 0,
        "run_complete_nutrition": run_complete and (low_count > 0 or high_count > 0),
    }


def advice_context(content, scope):
    if scope == "personal":
        from app.services.report_assessment import assess_personal_report
        fields = assess_personal_report(content)["fields"]
        body = {"thinness": "thin", "reference": "normal", "overweight": "heavy", "obesity": "heavy"}.get(
            (content.get("body_assessment") or {}).get("category"), "unknown")
        sports = content.get("sports") or {}
        records = (content.get("meal_statistics") or {}).get("records", [])
        observed = {(row["date"], row["slot"]) for row in records if row.get("source") != "estimated"}
        breakfast_low = bool(observed) and sum(slot == "breakfast" for _, slot in observed) <= 2
        sports_low = not sports.get("practice_count") or sports.get("active_days") == 1
        individual_abnormal, body_support = False, body in ("thin", "heavy")
    else:
        summary = content.get("health_summary") or {}
        fields, body = summary.get("core") or [], "unknown"
        sports = summary.get("sports") or {}
        signals = summary.get("advice_signals") or {}
        breakfast_low, sports_low = signals.get("breakfast_low", 0) > 0, signals.get("sports_low", 0) > 0
        individual_abnormal = signals.get("nutrition_abnormal", 0) > 0
        body_support = signals.get("body_support", 0) > 0
    by_key = {field["key"]: field for field in fields}
    tones = {key: by_key.get(key, {}).get("tone", "unknown") for key in NUTRIENTS}
    abnormal = any(tone in ("low", "high") for tone in tones.values())
    count = sports.get("practice_count")
    delta = sports.get("practice_delta") if sports.get("previous_has_records") else None
    events = {
        "always": True, "diet_default": True, "family_default": True,
        "diet_incomplete": any(tone == "unknown" for tone in tones.values()),
        "all_normal": all(tone == "ok" for tone in tones.values()),
        "calcium_only": not abnormal and by_key.get("calcium", {}).get("value") is not None,
        "breakfast_low": breakfast_low, "sports_low": sports_low, "body_support": body_support,
        "any_abnormal": abnormal or individual_abnormal,
        "sports_present": count is not None and count > 0,
        "sports_missing": count is None or count == 0,
        "sports_down": delta is not None and delta < 0,
        "sports_up": delta is not None and delta > 0,
        "top_project": bool(sports.get("top_projects")),
        # Exercise counts do not establish energy expenditure completeness.
        "run_complete": False, "run_incomplete": False,
        "nutrition_sports": (abnormal or individual_abnormal) and count is not None and count > 0,
    }
    if scope != "personal":
        events.update(_group_strategy_events(content, summary, tones, sports))
    return {"fields": by_key, "tones": tones, "body": body, "events": events, "sports": sports, "scope": scope}


def _matches(rule, context):
    match = rule["match"]
    return (all(context["tones"].get(key) == tone for key, tone in match.get("nutrients", {}).items())
            and (not match.get("body") or match["body"] == context["body"])
            and (not match.get("event") or context["events"].get(match["event"], False)))


def _rank(rule, context):
    deviations = []
    for key, tone in rule["match"].get("nutrients", {}).items():
        field = context["fields"][key]
        boundary = field.get("lower" if tone == "low" else "upper")
        value = field.get("value")
        deviations.append(abs(value - boundary) / boundary if boundary and value is not None else 0)
    nutrient_order = min((NUTRIENTS.index(key) for key in rule["match"].get("nutrients", {})), default=len(NUTRIENTS))
    return (-rule["priority"], -len(deviations), -max(deviations, default=0), nutrient_order, rule["id"])


def _select(rules, context, category, chooser):
    for rule in sorted((r for r in rules if r["category"] == category and r["enabled"] and _matches(r, context)),
                       key=lambda r: _rank(r, context)):
        sports, event = context["sports"], rule["match"].get("event")
        project = (sports.get("top_projects") or [{}])[0]
        tokens = {"人数": sports.get("participants"), "次数": sports.get("practice_count"), "项目": project.get("name")}
        if event in ("sports_down", "sports_up"):
            tokens["次数"] = abs(sports["practice_delta"])
        elif event == "top_project":
            tokens["次数"] = project.get("count")
        phrases = [phrase for phrase in rule["phrases"] if all(tokens.get(key) is not None for key in TOKEN.findall(phrase))]
        if phrases:
            text = TOKEN.sub(lambda match: str(tokens[match[1]]), chooser(phrases))
            return {"rule_id": rule["id"], "category": category, "icon": ICONS[category], "title": LABELS[category], "text": text}
    return None


def _class_diet_text(fields, selected, rules, context, chooser):
    by_key = {field["key"]: field for field in fields}
    names = {"calories": "能量", "protein": "蛋白质", "carbohydrate": "碳水化合物",
             "fiber": "膳食纤维", "fat": "脂肪"}
    observations = []
    normal = []
    for key, label in names.items():
        field = by_key.get(key, {})
        tone = field.get("tone")
        if tone in ("low", "high"):
            observations.append(f"{label}{'偏低' if tone == 'low' else '偏高'}")
        elif tone == "ok":
            normal.append(label)
        elif tone == "unknown" or tone is None:
            observations.append(f"{label}{'缺少可比参考' if field.get('value') is not None else '暂无数据'}")
    if normal:
        observations.append("、".join(normal) + ("均" if len(normal) > 1 else "") + "处于参考范围")
    calcium = by_key.get("calcium", {}).get("value")
    observations.append(f"钙日均供给约 {calcium:g} 毫克/人·天，仅作供给参考" if calcium is not None else "钙暂无供给数据")
    actions = [selected["text"]]
    selected_rule = next((rule for rule in rules if rule["id"] == selected["rule_id"]), {})
    covered = (selected_rule.get("match") or {}).get("nutrients", {})
    for key in names:
        tone = by_key.get(key, {}).get("tone")
        if tone not in ("low", "high") or key in covered:
            continue
        matching = [rule for rule in rules if rule["id"] == f"class.diet.{key}.{tone}"]
        extra = _select(matching, context, "diet", chooser)
        if extra and extra["text"] not in actions:
            actions.append(extra["text"])
    return "班级日均供给：" + "；".join(observations) + "。" + "".join(
        action if action.endswith("。") else action + "。" for action in actions)


def _class_sports_text(sports):
    participants = sports.get("participants")
    unrecorded = sports.get("unrecorded_students")
    previous = sports.get("previous_participants")
    if participants is None or unrecorded is None:
        return None
    parts = [f"本周有 {unrecorded} 人未见体育练习记录，建议了解相关学生的实际参与情况。" if unrecorded
             else f"本周有 {participants} 人有体育练习记录。"]
    if sports.get("previous_has_records") and previous is not None:
        difference = participants - previous
        if difference > 0:
            new = sports.get("new_participants")
            newcomers = f"其中 {new} 人本周新见记录，" if new is not None else ""
            parts.append(f"有记录的学生较上周增加 {difference} 人，{newcomers}鼓励学生按自身情况规律参与，并持续关注本周新参与学生后续的参与情况。")
        elif difference < 0:
            parts.append(f"有记录的学生较上周减少 {-difference} 人，建议核实课程安排与记录情况，关注参与变化。")
        else:
            parts.append("有记录的学生人数与上周持平，鼓励学生按自身情况规律参与。")
    else:
        parts.append("上周暂无可比体育记录，鼓励学生按自身情况规律参与。")
    return "".join(parts)


def build_advice_snapshot(content, report_type, config, *, chooser=None):
    if report_type not in ("personal_weekly", "class_weekly", "grade_weekly", "class_monthly", "grade_monthly"):
        return None
    scope = report_type.split("_")[0]
    context = advice_context(content, scope)
    rules = [rule for rule in get_report_advice_rules(config) if rule["scope"] == scope]
    chooser = chooser or random.choice
    items = []
    categories = ("diet", "sports", "family") if scope == "personal" else (
        ("diet", "sports", "coordination", "family") if scope == "class" else ("diet", "sports", "coordination"))
    for category in categories:
        selected = _select(rules, context, category, chooser)
        if category == "sports" and scope == "grade" and selected and selected["rule_id"] in ("grade.sports.sports_up", "grade.sports.sports_down"):
            base = _select([r for r in rules if r["match"].get("event") == "sports_present"], context, category, chooser)
            if base:
                selected["text"] = base["text"].rstrip("。") + "；" + selected["text"]
                selected["rule_ids"] = [base["rule_id"], selected["rule_id"]]
        if scope == "class" and selected:
            if category == "diet" and context["fields"]:
                selected["text"] = _class_diet_text(context["fields"].values(), selected, rules, context, chooser)
            elif category == "sports":
                factual = _class_sports_text(context["sports"])
                if factual:
                    selected["text"] = factual + selected["text"]
        item = selected or {"rule_id": "system.fallback", "category": category, "icon": ICONS[category],
                            "title": LABELS[category], "text": FALLBACK[category]}
        if report_type.endswith("_monthly"):
            item["text"] = item["text"].replace("本周", "本月").replace("上周", "上月").replace("周报", "月报")
        items.append(item)
        if scope == "personal" and category == "family" and selected:
            additional = _select([r for r in rules if r["id"] != selected["rule_id"] and r["match"].get("event") in ("breakfast_low", "sports_low")],
                                 context, category, chooser)
            if additional and additional["text"] != selected["text"]:
                items.append(additional)
    return {"version": 1, "report_type": report_type, "items": items}


def snapshot_items(content, *categories):
    snapshot = content.get("advice_snapshot") or {}
    return [item for item in snapshot.get("items", []) if not categories or item["category"] in categories]
