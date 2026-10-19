"""A shared, self-contained report for web preview and PNG export."""

import base64
import math
import io
import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.nutrition_metadata import DAILY_RECOMMENDED, REPORT_NUTRITION_FIELDS, REPORT_NUTRITION_KEYS
from app.services.report_meals import report_period_meal_statistics
from app.services.report_assessment import assess_personal_report, student_reference_profile

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates" / "reports"
CORE_KEYS = REPORT_NUTRITION_KEYS


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def _legacy_personal_report_view(content):
    avg = content.get("avg_nutrients") or {}
    rec = content.get("recommended_nutrients") or DAILY_RECOMMENDED
    counts = content.get("nutrient_sample_counts")
    fields = []
    compliance = []
    for field in REPORT_NUTRITION_FIELDS:
        value = _number(avg.get(field.key))
        recommended = _number(rec.get(field.key))
        measured = value is not None and (counts is None or counts.get(field.key, 0) > 0)
        ratio = value / recommended if measured and recommended else None
        tone, status = "unknown", "暂无数据"
        if ratio is not None:
            tone = "high" if ratio > 1.2 else "ok" if field.upper_limit or ratio >= .8 else "low"
            status = {"high": "偏高", "ok": "控制良好" if field.upper_limit else "达标", "low": "偏低"}[tone]
            if field.upper_limit:
                compliance.append(100 if ratio <= 1 else max(0, 100 - (ratio - 1) * 100))
            elif .8 <= ratio <= 1.2:
                compliance.append(100)
            elif ratio < .8:
                compliance.append(ratio / .8 * 100)
            else:
                compliance.append(max(0, 100 - (ratio - 1.2) * 150))
        fields.append({
            "key": field.key, "label": field.label, "unit": field.unit,
            "value": f"{value:g}" if measured else "—",
            "recommended": f"{recommended:g}" if recommended else "—",
            "tone": tone, "status": status, "measured": measured,
            "upper_limit": field.upper_limit,
        })
    by_key = {f["key"]: f for f in fields}
    pct = round(sum(compliance) / len(compliance)) if compliance else None
    assessment = "暂无评价" if pct is None else "较均衡" if pct >= 85 else "基本均衡" if pct >= 70 else "待改善"
    focus = [f for f in fields if f["tone"] in ("low", "high")]
    suggestions = [
        f"{f['label']}摄入{f['status']}，下一周期关注{'控制' if f['tone'] == 'high' else '补足'}，结合推荐参考值调整。"
        for f in focus
    ]
    if not suggestions:
        suggestions = ["继续保持当前摄入水平，并关注后续记录。" if compliance else "暂无足够营养数据，待补充有效记录后再评估。"]
    energy = None
    if all(by_key[key]["measured"] for key in ("protein", "fat", "carbohydrate")):
        energies = [float(avg["carbohydrate"]) * 4, float(avg["fat"]) * 9, float(avg["protein"]) * 4]
        total = sum(energies)
        if total > 0:
            percentages = [round(value * 100 / total) for value in energies]
            percentages[percentages.index(max(percentages))] += 100 - sum(percentages)
            energy = [dict(label=label, value=value, color=color) for label, value, color in zip(
                ("碳水化合物", "脂肪", "蛋白质"), percentages, ("#EDAA35", "#13845B", "#5183BB"),
            )]
    score = _number(content.get("overall_score")) if compliance else None
    summary = (f"周期日均营养整体{assessment}，综合评分 {score:g} 分。" if score is not None
               else "暂无足够营养数据，暂不进行综合评价。")
    if focus:
        summary += f"有 {len(focus)} 项摄入指标需要关注。"
    return {
        "fields": fields, "core": [by_key[k] for k in CORE_KEYS],
        "compliance": pct, "assessment": assessment, "score": f"{score:g}" if score is not None else "—",
        "focus": focus, "suggestions": suggestions, "summary": summary, "energy": energy,
        "measured_count": sum(f["measured"] for f in fields),
    }


@lru_cache(maxsize=1)
def _environment():
    return Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=select_autoescape(["html", "svg"]))


@lru_cache(maxsize=1)
def _background():
    return "data:image/svg+xml;base64," + base64.b64encode((TEMPLATE_DIR / "background.svg").read_bytes()).decode()


@lru_cache(maxsize=16)
def _font_css(characters):
    """Subset bundled, licensed fonts to this document; no network at render time."""
    from fontTools import subset
    from fontTools.ttLib import TTFont
    rules = []
    for family, prefix in (("Report Sans", "noto-sans-sc"), ("Report Numbers", "dm-sans")):
        for weight in (400, 600):
            path = TEMPLATE_DIR / "fonts" / f"{prefix}-{weight}.woff2"
            font = TTFont(path)
            options = subset.Options()
            options.flavor = "woff2"
            sub = subset.Subsetter(options=options)
            sub.populate(text=characters)
            sub.subset(font)
            output = io.BytesIO()
            font.flavor = "woff2"
            font.save(output)
            data = base64.b64encode(output.getvalue()).decode()
            rules.append(f"@font-face{{font-family:'{family}';font-style:normal;font-weight:{weight};src:url(data:font/woff2;base64,{data}) format('woff2');}}")
    return "".join(rules)


def _trend(series, key):
    values = [_number(item.get("nutrients", {}).get(key)) for item in series]
    maximum = max((value for value in values if value is not None), default=0)
    ceiling = math.ceil(maximum * 1.15 / (100 if maximum > 100 else 10)) * (100 if maximum > 100 else 10) or 1
    dots, paths, segment = [], [], []
    for i, value in enumerate(values):
        x = round(12 + i * 192 / max(1, len(values) - 1), 2)
        if value is None:
            if segment:
                paths.append(" ".join(segment))
            segment = []
            continue
        y = round(100 - value * 80 / ceiling, 2)
        dots.append({"x": x, "y": y, "value": value, "date": series[i]["date"]})
        segment.append(f"{x},{y}")
    if segment:
        paths.append(" ".join(segment))
    return {"paths": paths, "dots": dots, "ceiling": ceiling, "sample_count": len(dots),
            "start": series[0]["date"][5:] if series else "", "end": series[-1]["date"][5:] if series else ""}


def personal_report_view(content):
    if content.get("report_version") != 2:
        return _legacy_personal_report_view(content)
    from app.services.report_reference import GENERAL_SUGGESTIONS, LIMITATION
    counts = content.get("nutrient_sample_counts") or {}
    averages = content.get("avg_nutrients") or {}
    coverage = content.get("coverage") or {}
    projection = content.get("meal_projection") or {}
    projected = projection.get("avg_nutrients") or {}
    assessment = assess_personal_report(content)
    statuses = {field["key"]: field for field in assessment["fields"]}
    fields = []
    for field in REPORT_NUTRITION_FIELDS:
        value = _number(averages.get(field.key))
        count = counts.get(field.key, 0)
        measured = value is not None and count > 0
        equivalent = _number(projected.get(field.key))
        result = equivalent if projection else (value if measured else None)
        fields.append({"key": field.key, "label": field.label, "unit": field.unit,
                       "value": f"{value:g}" if measured else "—", "measured": measured, "count": count,
                       "equivalent": f"{equivalent:g}" if equivalent is not None else "—",
                       "result": f"{result:g}" if result is not None else "—",
                       "status": statuses[field.key]["status"], "tone": statuses[field.key]["tone"],
                       "projection_meals": (projection.get("nutrient_meal_counts") or {}).get(field.key, 0),
                       "projection_days": (projection.get("nutrient_sample_counts") or {}).get(field.key, 0),
                       "pct": round(count * 100 / coverage["nutrition_days"]) if coverage.get("nutrition_days") else 0})
    by_key = {field["key"]: field for field in fields}
    stats = content.get("meal_statistics") or {}
    consumed = {(item["date"], item["slot"]) for item in stats.get("records", [])}
    estimated = {(item["date"], item["slot"]) for item in stats.get("records", []) if item.get("source") == "estimated"}
    skipped = {(item["date"], item["slot"]) for item in stats.get("likely_skipped", [])}
    recognized = {(item["date"], item["slot"]) for item in content.get("nutrition_records", [])}
    slots = stats.get("slots") or [{"key": key, "label": label} for key, label in (("breakfast", "早餐"), ("lunch", "午餐"), ("dinner", "晚餐"))]
    start, end = date.fromisoformat(content["period_start"]), date.fromisoformat(content["period_end"])
    days = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    weeks = []
    for offset in range(0, len(days), 7):
        chunk = days[offset:offset + 7]
        weeks.append({"days": [day.strftime("%m.%d") for day in chunk], "rows": [
            {"label": slot["label"], "cells": ["estimated" if (day.isoformat(), slot["key"]) in estimated
                                               else "nutrition" if (day.isoformat(), slot["key"]) in recognized
                                               else "consumed" if (day.isoformat(), slot["key"]) in consumed
                                               else "skipped" if (day.isoformat(), slot["key"]) in skipped else "absent" for day in chunk]}
            for slot in slots]})
    energy = []
    summary = content.get("energy_summary") or {}
    percentages = summary.get("percentages")
    offset = 0
    if percentages:
        for key, label, color in (("carbohydrate", "碳水化合物", "#D69A28"), ("fat", "脂肪", "#126448"), ("protein", "蛋白质", "#417CB2")):
            value = percentages[key]
            energy.append({"label": label, "color": color, "value": value, "dash": value * 2.7646, "offset": -offset * 2.7646})
            offset += value
    return {"fields": fields, "core": [by_key[key] for key in CORE_KEYS], "weeks": weeks,
            "projection": projection, "assessment": assessment,
            "trends": [{**by_key[key], **_trend(content.get("daily_series") or [], key), "color": color}
                       for key, color in (("calories", "#126448"), ("protein", "#417CB2"), ("fat", "#D69A28"))],
            "energy": energy, "reference_charts": _reference_charts(assessment),
            "suggestions": content.get("suggestions") or list(GENERAL_SUGGESTIONS), "coverage": coverage, "limitations": LIMITATION,
            "unknown_nutrition": sum(item["slot"] == "unknown" for item in content.get("nutrition_records", [])),
            "summary": f"本周期有 {coverage.get('recorded_days', 0)} 天在校消费记录，{coverage.get('nutrition_days', 0)} 天有效营养数据。"}


def _reference_charts(assessment):
    charts = []
    for field in assessment["fields"]:
        value, lower, upper = field["value"], field["lower"], field["upper"]
        maximum = max((upper or lower or 1) * 1.5, (value or 0) * 1.15)
        step = 100 if maximum > 1000 else 10 if maximum > 100 else 5 if maximum > 25 else 1
        maximum = math.ceil(maximum / step) * step

        def position(number):
            return round(number / maximum * 100, 2)

        lo, hi = position(lower or 0), position(upper if upper is not None else maximum)
        label = (f"≥ {lower:g}" if upper is None else f"{lower:g} – {upper:g}") if lower is not None else ""
        charts.append({**field, "result": f"{value:g}" if value is not None else "—", "range_label": label,
                       "lower_x": lo, "upper_x": hi, "range_width": hi - lo, "high_width": 100 - hi, "maximum": f"{maximum:g}",
                       "value_x": position(value) if value is not None else None})
    return charts


def render_personal_html(content, *, page_height=None):
    modern = content.get("report_version") == 2
    template = "personal.html" if modern else "legacy_personal.html"
    view = personal_report_view(content)
    font_css = ""
    if modern:
        characters = "".join(sorted(set(json.dumps([content, view], ensure_ascii=False) + (TEMPLATE_DIR / template).read_text())))
        font_css = _font_css(characters)
    return _environment().get_template(template).render(
        content=content, view=view, background=_background() if not modern else "", page_height=page_height, font_css=font_css,
    )


def serialize_personal_reports(reports, *, include_html=False):
    """Enrich legacy snapshots without changing their stored nutrition analysis."""
    stats = report_period_meal_statistics([
        (r.id, int(r.target_id), r.period_start, r.period_end) for r in reports
        if r.report_type.value.startswith("personal_") and str(r.target_id).isdigit() and (r.content or {}).get("report_version") != 2
    ])
    from app.services.report_meal_completion import MEAL_COMPLETION_VERSION, enrich_report_meals, report_period_completed_meals
    from app.services.runtime_config import get_effective_config
    from flask import current_app
    incomplete = [(r.id, int(r.target_id), r.period_start, r.period_end) for r in reports
                  if r.report_type.value.startswith("personal_") and str(r.target_id).isdigit() and (r.content or {}).get("report_version") == 2
                  and ((r.content or {}).get("meal_statistics") or {}).get("completion_version") != MEAL_COMPLETION_VERSION]
    completions = report_period_completed_meals(incomplete, get_effective_config(current_app.config)) if incomplete else {}
    student_ids = {int(r.target_id) for r in reports if str(r.target_id).isdigit()
                   and r.report_type.value.startswith("personal_") and (r.content or {}).get("report_version") == 2
                   and "student_profile" not in (r.content or {})}
    students = {}
    if student_ids:
        from sqlalchemy.orm import joinedload
        from app.models import Student
        from app.modules.students.models.organization import Class, Grade
        students = {student.id: student for student in Student.query.options(
            joinedload(Student.class_).joinedload(Class.grade).joinedload(Grade.stage),
        ).filter(Student.id.in_(student_ids)).all()}
    result = []
    for report in reports:
        data = report.to_dict(include_content=True)
        if report.report_type.value.startswith("personal_"):
            content = dict(data.get("content") or {})
            content.update(period_start=report.period_start.isoformat(), period_end=report.period_end.isoformat(), report_type=report.report_type.value)
            if content.get("report_version") == 2:
                if report.id in completions:
                    enrich_report_meals(content, completions[report.id], int(report.target_id))
                student = students.get(int(report.target_id)) if str(report.target_id).isdigit() else None
                if student is not None:
                    content["student_profile"] = student_reference_profile(student, report.period_end)
                content["nutrition_assessment"] = assess_personal_report(content)
            if report.id in stats:
                content["meal_statistics"] = stats[report.id]
                content["suggestions"] = personal_report_view(content)["suggestions"]
            if include_html:
                content["presentation_html"] = render_personal_html(content)
            data["content"] = content
        result.append(data)
    return result
