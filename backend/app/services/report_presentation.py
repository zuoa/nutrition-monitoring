"""A shared, self-contained report for web preview and PNG export."""

import base64
import math
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.nutrition_metadata import DAILY_RECOMMENDED, NUTRITION_FIELDS
from app.services.report_meals import report_period_meal_statistics

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates" / "reports"
CORE_KEYS = ("calories", "protein", "carbohydrate", "fiber", "fat", "calcium")


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def personal_report_view(content):
    avg = content.get("avg_nutrients") or {}
    rec = content.get("recommended_nutrients") or DAILY_RECOMMENDED
    counts = content.get("nutrient_sample_counts")
    fields = []
    compliance = []
    for field in NUTRITION_FIELDS:
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


def render_personal_html(content, *, page_height=None):
    return _environment().get_template("personal.html").render(
        content=content, view=personal_report_view(content), background=_background(), page_height=page_height,
    )


def serialize_personal_reports(reports, *, include_html=False):
    """Enrich legacy snapshots without changing their stored nutrition analysis."""
    stats = report_period_meal_statistics([
        (r.id, int(r.target_id), r.period_start, r.period_end) for r in reports
        if r.report_type.value.startswith("personal_") and str(r.target_id).isdigit()
    ])
    result = []
    for report in reports:
        data = report.to_dict(include_content=True)
        if report.id in stats:
            content = dict(data.get("content") or {})
            content.update(period_start=report.period_start.isoformat(), period_end=report.period_end.isoformat(), report_type=report.report_type.value)
            content["meal_statistics"] = stats[report.id]
            content["suggestions"] = personal_report_view(content)["suggestions"]
            if include_html:
                content["presentation_html"] = render_personal_html(content)
            data["content"] = content
        result.append(data)
    return result
