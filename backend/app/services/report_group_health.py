"""Anonymous health summaries for class and grade report snapshots."""

from collections import Counter, defaultdict
from datetime import date
import math

from app.models import Report
from app.nutrition_metadata import NUTRITION_FIELD_LABELS, NUTRITION_FIELD_UNITS
from app.services.report_assessment import assess_personal_report, report_nutrient_values, student_reference_profile
from app.services.report_brief import ICONS, _comparison_chart
from app.services.report_dining_features import period_dining_features
from app.services.report_health import period_health_context, previous_report_period
from app.services.report_reference import select_reference

KEYS = ("calories", "protein", "carbohydrate", "fiber", "fat", "calcium")
LEVELS = (("A", "达标良好", "#008457"), ("B", "基本达标", "#8BD87E"), ("C", "需改善", "#FFD23F"), ("D", "重点关注", "#FF6336"))


def _mean(values):
    values = [value for value in values if value is not None]
    return round(sum(values) / len(values), 1) if values else None


def _fields(samples):
    fields = []
    for key in KEYS:
        measured = [sample for sample in samples if sample["values"].get(key) is not None]
        value = _mean(sample["values"][key] for sample in measured)
        bounds = [sample["fields"].get(key, {}) for sample in measured]
        valid = bool(bounds) and all(field.get("lower") is not None for field in bounds)
        lower = _mean(field["lower"] for field in bounds) if valid else None
        upper = _mean(field.get("upper") for field in bounds) if valid else None
        calcium_references = [sample["calcium_reference"] for sample in measured] if key == "calcium" else []
        chart_reference = _mean(calcium_references) if calcium_references and all(value is not None for value in calcium_references) else None
        tone = "unknown" if value is None or lower is None else "low" if value < lower else "high" if upper is not None and value > upper else "ok"
        reference = f"{lower:g}–{upper:g}" if lower is not None and upper is not None else f"≥{lower:g}" if lower is not None else ""
        fields.append({"key": key, "label": NUTRITION_FIELD_LABELS[key], "unit": NUTRITION_FIELD_UNITS[key],
                       "value": value, "result": f"{value:g}" if value is not None else "—", "count": len(measured),
                       "lower": lower, "upper": upper, "reference": reference, "chart_reference": chart_reference, "icon": ICONS.get(key), "tone": tone,
                       "status": "暂无数据" if value is None else {"ok": "正常", "low": "偏低", "high": "偏高", "unknown": "参考供给"}[tone]})
    return fields


def group_period_comparison(content, report_type=None):
    start, end = date.fromisoformat(content["period_start"]), date.fromisoformat(content["period_end"])
    previous_start, previous_end, personal_type = previous_report_period(start, end, report_type)
    kind = f"{content['scope_type']}_{personal_type.split('_')[1]}"
    previous = Report.query.filter_by(report_type=kind, target_id=str(content["scope_id"]),
                                      period_start=previous_start, period_end=previous_end).order_by(Report.created_at.desc(), Report.id.desc()).first()
    old = (previous.content or {}) if previous else {}
    health = old.get("health_summary") or {}
    return {"has_report": previous is not None, "period_start": previous_start.isoformat(), "period_end": previous_end.isoformat(),
            "participating_students": health.get("participating_students"), "nutrients": report_nutrient_values(old)}


def build_group_health(students, student_averages, attendance, start, end, *, assessment_ranges=None):
    requests = [(student.id, student.id, start, end) for student in students]
    health = period_health_context(requests)
    dining = period_dining_features(requests)
    samples = []
    for student, average in zip(students, student_averages):
        profile = student_reference_profile(student, end)
        assessment = assess_personal_report({"period_end": end.isoformat(), "student_profile": profile,
                                             "report_assessment_ranges": assessment_ranges,
                                             "meal_projection": average["projection"]})
        reference = select_reference(profile) or {}
        samples.append({"class_id": student.class_id, "values": average["projection"]["avg_nutrients"],
                        "level": assessment["level"], "fields": {field["key"]: field for field in assessment["fields"]},
                        "calcium_reference": reference.get("calcium")})
    counts = Counter(sample["level"] for sample in samples)
    assessed = sum(counts[level] for level, _, _ in LEVELS)
    distribution = [{"level": level, "label": label, "color": color, "count": counts[level],
                     "percentage": round(counts[level] * 100 / assessed, 1) if assessed else 0} for level, label, color in LEVELS]
    projects, old_projects = Counter(), Counter()
    participants = unrecorded = dining_participants = 0
    focus = Counter()
    composition, choices = Counter(), {}
    for student, sample in zip(students, samples):
        sport = health[student.id]["sports"]
        practiced = sport["practice_count"] > 0
        participants += practiced
        unrecorded += not practiced
        focus["fat_high"] += sample["fields"]["fat"]["tone"] == "high"
        records = attendance.get(student.id, {}).get("records", [])
        # Only directly observed meals support a breakfast participation claim.
        observed = {(row["date"], row["slot"]) for row in records if row.get("source") != "estimated"}
        dining_participants += bool(observed)
        focus["breakfast_low"] += bool(observed) and sum(slot == "breakfast" for _, slot in observed) <= 2
        focus["nutrition_and_sports"] += sample["level"] == "D" and not practiced
        for project in sport["projects"]:
            projects[project["sport_type"]] += project["count"]
        # Per-person previous totals are complete, including projects absent this period.
        old_projects["total"] += sport["previous_practice_count"]
        features = dining[student.id]
        for row in features.get("composition", []):
            composition[row["label"]] += row["count"]
        for dish in features.get("dish_choices", []):
            choice = choices.setdefault(dish["id"], {**dish, "count": 0})
            choice["count"] += dish["count"]
    from app.services.sport_ingestion_service import SPORT_NAMES
    total, old_total = sum(projects.values()), old_projects["total"]
    ranked = [{"name": SPORT_NAMES.get(key, f"项目 {key}"), "count": count}
              for key, count in sorted(projects.items(), key=lambda item: (-item[1], item[0]))[:3]]
    previous_has_records = any(item["sports"]["previous_has_records"] for item in health.values())
    by_class = defaultdict(list)
    for sample in samples:
        if sample["class_id"] is not None:
            by_class[sample["class_id"]].append(sample)
    class_fields = [_fields(items) for items in by_class.values()]
    achievement = []
    for index, key in enumerate(KEYS):
        eligible = [fields[index] for fields in class_fields if fields[index]["tone"] != "unknown"]
        passed = sum(field["tone"] == "ok" for field in eligible)
        achievement.append({"key": key, "label": NUTRITION_FIELD_LABELS[key], "passed": passed,
                            "assessed": len(eligible), "total": len(by_class), "rate": round(passed / len(eligible) * 100, 1) if eligible else None})
    selection_count = sum(composition.values())
    return {"version": 1, "core": _fields(samples), "distribution": distribution, "assessed_students": assessed,
            "unassessed_students": len(students) - assessed, "class_count": len(by_class), "class_achievement": achievement,
            "participating_students": dining_participants,
            "sports": {"practice_count": total, "previous_practice_count": old_total,
                       "practice_delta": total - old_total if previous_has_records else None,
                       "previous_has_records": previous_has_records, "participants": participants, "unrecorded_students": unrecorded,
                       "top_projects": ranked, "other_count": total - sum(row["count"] for row in ranked)},
            "focus": [{"label": "脂肪供给偏高", "count": focus["fat_high"], "icon": "fat"},
                      {"label": "早餐记录不多于2次", "count": focus["breakfast_low"], "icon": "breakfast"},
                      {"label": "未见体育练习记录", "count": unrecorded, "icon": "running"},
                      {"label": "营养D级且未见练习记录", "count": focus["nutrition_and_sports"], "icon": "family"}],
            "dining": {"composition": [{"label": label, "share": round(count * 100 / selection_count, 1)} for label, count in composition.items()] if selection_count else [],
                       "popular": sorted(choices.values(), key=lambda item: (-item["count"], item["id"]))[:3],
                       "varied": sorted([item for item in choices.values() if len(item["food_groups"]) >= 2],
                                        key=lambda item: (-len(item["food_groups"]), -item["count"], item["id"]))[:3]}}


def group_report_view(content):
    summary = content.get("health_summary") or {}
    core = summary.get("core")
    if core is None:
        values = report_nutrient_values(content)
        core = [{"key": key, "label": NUTRITION_FIELD_LABELS[key], "unit": NUTRITION_FIELD_UNITS[key], "icon": ICONS.get(key),
                 "value": values.get(key), "result": f"{values[key]:g}" if values.get(key) is not None else "—",
                 "tone": "unknown", "status": "参考供给" if values.get(key) is not None else "暂无数据", "reference": ""} for key in KEYS]
    previous = content.get("period_comparison") or {}
    monthly = content.get("report_type", "").endswith("_monthly")
    organization = content.get("organization_name") or "校园膳食营养监测"
    distribution = [dict(row) for row in summary.get("distribution", [])]
    offset = 0
    for row in distribution:
        row.update(dash=row["percentage"] * 4.3982, offset=-offset * 4.3982)
        angle = math.radians(-90 + (offset + row["percentage"] / 2) * 3.6)
        row.update(x=round(160 + math.cos(angle) * 91, 1), y=round(160 + math.sin(angle) * 91, 1))
        offset += row["percentage"]
    # Shared chart logic normalizes each nutrient to its own reference value.
    chart = _comparison_chart(core, {"profile": {}}, previous.get("nutrients") or {})
    current, old = summary.get("participating_students"), previous.get("participating_students")
    concerns = [field["label"] + field["status"] for field in core if field["tone"] in ("low", "high")]
    conclusion = "；".join(concerns) + "。" if concerns else "各项已评价指标处于参考范围，继续保持多样搭配。" if any(field["tone"] == "ok" for field in core) else "当前评价资料不足，补充营养记录后持续观察。"
    return {"summary": summary, "core": core, "chart": chart, "distribution": distribution,
            "period_label": "本月" if monthly else "本周", "previous_label": "上月" if monthly else "上周",
            "cadence": "月报" if monthly else "周报", "scope_label": {"class": "班级", "grade": "年级", "campus": "校区"}[content["scope_type"]],
            "organization": organization, "school_emblem": "杭州第四中学" in organization or "杭州四中" in organization,
            "sports": summary.get("sports") or {}, "dining": summary.get("dining") or {}, "comparison": previous,
            "participating_students": current, "participation_delta": current - old if current is not None and old is not None else None,
            "conclusion": conclusion, "fat_contributors": (content.get("group_analysis") or {}).get("fat_contributors", {}).get("items", [])[:2]}
