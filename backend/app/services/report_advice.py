"""Fixed, configurable report advice; select once when persisting a report."""

from copy import deepcopy
import json
import math
from pathlib import Path
import random
import re

CATALOG = json.loads(Path(__file__).with_name("report_advice_catalog.json").read_text(encoding="utf-8"))
RULES = {rule["id"]: rule for rule in CATALOG}
NUTRIENTS = ("protein", "fat", "calories", "carbohydrate", "fiber")
NUTRIENT_PRIORITY = {key: index for index, key in enumerate(NUTRIENTS)}
ICONS = {"diet": "fiber", "sports": "running", "family": "family", "coordination": "family"}
TOKEN = re.compile(r"【([^【】]+)】")
RULES_CONFIG_KEY = "REPORT_ADVICE_RULES_V2"
RUN_REFERENCES_CONFIG_KEY = "REPORT_RUN_ENERGY_REFERENCES"
SENIOR_GRADES = ("senior_1", "senior_2", "senior_3")
SEXES = ("男", "女")
REPORT_TYPES = {"personal_weekly", "class_weekly", "class_monthly", "grade_weekly", "grade_monthly"}


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
    overrides = config.get(RULES_CONFIG_KEY) or {}
    try:
        overrides = validate_report_advice_rules(overrides)
    except ValueError:
        overrides = {}
    return [{**deepcopy(rule), **overrides.get(rule["id"], {})} for rule in CATALOG]


def validate_run_energy_references(value):
    if not isinstance(value, dict) or set(value) - set(SENIOR_GRADES):
        raise ValueError("跑步能耗参考值仅支持高一至高三")
    result = {}
    for grade, thresholds in value.items():
        if not isinstance(thresholds, dict) or set(thresholds) - set(SEXES):
            raise ValueError("每个年级的跑步能耗参考值按男女配置")
        for sex, threshold in thresholds.items():
            if threshold is None:
                continue
            if type(threshold) not in (int, float) or not 0 < threshold < math.inf:
                raise ValueError("跑步能耗参考值须为正数（kcal/周）或留空")
            result.setdefault(grade, {})[sex] = threshold
    return result


def get_run_energy_references(config):
    try:
        return validate_run_energy_references(config.get(RUN_REFERENCES_CONFIG_KEY, {}))
    except ValueError:
        return {}


def _direction(current, previous):
    if current is None or previous is None:
        return None
    return "up" if current > previous else "down" if current < previous else "steady"


def _personal_context(content, config):
    from app.services.report_assessment import assess_personal_report, senior_grade
    from app.services.report_meals import observed_meal_pairs

    assessment = assess_personal_report(content)
    profile = content.get("student_profile") or {}
    grade = profile.get("senior_grade") or senior_grade(profile.get("grade_name"))
    body = {"thinness": "thin", "reference": "normal", "overweight": "heavy", "obesity": "heavy"}.get(
        (content.get("body_assessment") or {}).get("category"))
    sport = content.get("sports") or {}
    complete = sport.get("run_energy_complete")
    energy = sport.get("run_energy_kcal") if complete else None
    previous = sport.get("previous_run_energy_kcal") if sport.get("previous_run_energy_complete") else None
    threshold = get_run_energy_references(config).get(grade, {}).get(profile.get("gender"))
    run_status = None
    if sport and not sport.get("run_energy_count"):
        run_status = "missing"
    elif energy is not None and threshold is not None:
        run_status = "met" if energy >= threshold else "below"
    attendance = content.get("meal_statistics") or {}
    breakfast_count = sum(slot == "breakfast" for _, slot in observed_meal_pairs(attendance))
    context = {"grade": grade, "body": body, "run_status": run_status,
               "run_change": _direction(energy, previous), "nutrition_level": assessment["level"] or "unavailable",
               "breakfast_low": isinstance(attendance.get("records"), list) and breakfast_count < 2}
    return assessment["fields"], context, {}


def _class_context(summary):
    metrics = summary.get("advice_metrics") or {}
    current, previous = metrics.get("current") or {}, metrics.get("previous") or {}
    count = current.get("cd_count")
    assessed = current.get("assessed_students")
    cd_high = 10 * count > 3 * assessed if count is not None and assessed else None
    changes = {f"{key}_change": _direction(current.get(key), previous.get(key))
               for key in ("participants", "unrecorded", "breakfast", "fat", "d")}
    coord_keys = ("participants_change", "breakfast_change", "fat_change")
    family_keys = ("unrecorded_change", "breakfast_change", "fat_change", "d_change")
    routines = set()
    if cd_high is False:
        if all(changes[key] for key in coord_keys) and not (
            changes["participants_change"] == "down" or "up" in (changes["breakfast_change"], changes["fat_change"])
        ):
            routines.add("coordination")
        if all(changes[key] and changes[key] != "up" for key in family_keys):
            routines.add("family")
    tokens = {"本周人数": current.get("participants"), "C/D级人数": count,
              "C/D级占比": f"{count * 100 / assessed:.1f}%" if count is not None and assessed else None,
              "早餐次数阈值": metrics.get("breakfast_threshold")}
    for key, token in (("participants", "增加人数"), ("participants", "减少人数"), ("d", "D级增加人数"),
                       ("breakfast", "早餐记录较少增加人数"), ("fat", "脂肪偏高增加人数"),
                       ("unrecorded", "无体育记录增加人数")):
        if changes[f"{key}_change"] is not None:
            tokens[token] = abs(current[key] - previous[key])
    return {**changes, "cd_high": cd_high}, tokens, routines


def _group_context(content, scope):
    from app.services.report_assessment import senior_grade

    summary = content.get("health_summary") or {}
    if scope == "class":
        context, tokens, routines = _class_context(summary)
    else:
        run = (summary.get("advice_metrics") or {}).get("run_energy") or {}
        current, previous = run.get("current"), run.get("previous")
        context = {"grade": content.get("advice_grade") or senior_grade(content.get("scope_name")),
                   "run_change": _direction(current, previous)}
        tokens = {"本周消耗": current}
        if current is not None and previous is not None:
            tokens["变化消耗"] = round(abs(current - previous), 1)
        routines = set()
    return summary.get("core") or [], context, tokens, routines


def _nutrient_context(fields, scope):
    tones = {field["key"]: field["tone"] for field in fields
             if field["key"] in NUTRIENTS and field.get("tone") in ("ok", "low", "high")
             and (field["key"] != "fiber" or field["tone"] != "high" or field.get("upper") is not None)}
    all_normal = len(tones) == len(NUTRIENTS) and all(tone == "ok" for tone in tones.values())
    state = None
    if not tones:
        state = "unavailable"
    elif all_normal or (scope != "personal" and all(tone == "ok" for tone in tones.values())):
        state = "normal"
    return {"nutrients": tones, "nutrition_state": state, "all_nutrients_normal": all_normal}


def _matches(rule, context):
    return all(
        all(context["nutrients"].get(key) == tone for key, tone in expected.items())
        if dimension == "nutrients" else context.get(dimension) == expected
        for dimension, expected in rule["match"].items()
    )


def _render_item(rule, tokens, monthly, chooser):
    phrases = rule["phrases"]
    if monthly and rule["id"] == "class.family.breakfast_up":
        phrases = [re.sub(r"不多于\s*2\s*次", "不多于【早餐次数阈值】次", phrase) for phrase in phrases]
    phrases = [phrase for phrase in phrases if all(tokens.get(key) is not None for key in TOKEN.findall(phrase))]
    if not phrases:
        return None
    text = TOKEN.sub(lambda match: str(tokens[match[1]]), chooser(phrases))
    if monthly:
        text = text.replace("本周", "本月").replace("上周", "上月").replace("当周", "当月").replace("周报", "月报")
    return {"rule_id": rule["id"], "category": rule["category"], "icon": ICONS[rule["category"]],
            "title": rule["category_label"], "text": text}


def build_advice_snapshot(content, report_type, config, *, chooser=None):
    if report_type not in REPORT_TYPES:
        return None
    scope = report_type.split("_")[0]
    if scope == "personal":
        fields, context, tokens = _personal_context(content, config)
        routines = set()
    else:
        fields, context, tokens, routines = _group_context(content, scope)
    context.update(_nutrient_context(fields, scope))
    chooser = chooser or random.choice
    monthly = report_type.endswith("_monthly")
    categories = {"diet": 2, "sports": 1, "coordination": 2, "family": 3}
    rules = [rule for rule in get_report_advice_rules(config) if rule["scope"] == scope and rule["enabled"]]
    items = []
    for category, limit in categories.items():
        category_context = {**context, "routine": category if category in routines else None}
        eligible = [rule for rule in rules if rule["category"] == category and _matches(rule, category_context)]
        if category == "diet" or (scope == "grade" and category == "coordination"):
            eligible.sort(key=lambda rule: min(
                (NUTRIENT_PRIORITY[key] for key in rule["match"].get("nutrients", {})), default=len(NUTRIENT_PRIORITY),
            ))
        selected = []
        for rule in eligible:
            item = _render_item(rule, tokens, monthly, chooser)
            if item and all(item["text"] != previous_item["text"] for previous_item in selected):
                selected.append(item)
                if len(selected) == limit:
                    break
        items.extend(selected)
    return {"version": 2, "report_type": report_type, "items": items}


def update_personal_grade_advice(snapshot, level, config):
    """Return the advice to display with the current personal grade."""
    prefix = "personal.family.nutrition."
    rule_id = prefix + (level or "unavailable")
    items = snapshot.get("items", [])
    stale_index = next((index for index, item in enumerate(items)
                        if item["rule_id"].startswith(prefix) and item["rule_id"] != rule_id), None)
    if stale_index is None:
        return snapshot
    rule = next(rule for rule in get_report_advice_rules(config) if rule["id"] == rule_id)
    replacement = None
    if rule["enabled"]:
        replacement = _render_item(rule, {}, snapshot["report_type"].endswith("_monthly"), lambda phrases: phrases[0])
    items = list(items)
    items[stale_index:stale_index + 1] = [replacement] if replacement is not None else []
    return {**snapshot, "items": items}


def snapshot_items(content, *categories):
    snapshot = content.get("advice_snapshot") or {}
    return [item for item in snapshot.get("items", []) if not categories or item["category"] in categories]
