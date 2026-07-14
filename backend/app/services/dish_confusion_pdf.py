"""Render dish confusion analysis results as a self-contained A4 PDF."""

import base64
import io
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import current_app, has_app_context
from jinja2 import Environment, select_autoescape

from app.models import DishSampleImage


class DishConfusionPdfError(ValueError):
    pass


def render_dish_confusion_pdf(report: dict) -> bytes:
    normalized = _normalize_report(report)
    normalized["sample_images"] = _sample_image_data(normalized["pairs"])
    normalized["assessment"] = _assessment(normalized)
    normalized["risk_distribution"] = _risk_distribution(normalized["summary"])

    html = _template().render(report=normalized)
    try:
        from weasyprint import HTML
    except ImportError as exc:
        raise DishConfusionPdfError("PDF 组件未安装，请检查 WeasyPrint 运行环境") from exc
    try:
        pdf = HTML(string=html).write_pdf()
    except Exception as exc:
        raise DishConfusionPdfError(f"PDF 生成失败：{exc}") from exc
    if not pdf:
        raise DishConfusionPdfError("PDF 生成结果为空")
    return pdf


def _normalize_report(report: dict) -> dict:
    if not isinstance(report, dict):
        raise DishConfusionPdfError("体检报告数据无效")
    summary = report.get("summary")
    thresholds = report.get("thresholds")
    if not isinstance(summary, dict) or not isinstance(thresholds, dict):
        raise DishConfusionPdfError("体检报告缺少汇总或阈值数据")

    pipeline = str(report.get("pipeline") or "qwen").strip().lower()
    if pipeline not in {"qwen", "visual"}:
        raise DishConfusionPdfError("体检报告识别模式无效")
    pairs = report.get("pairs") or []
    if not isinstance(pairs, list) or len(pairs) > 100:
        raise DishConfusionPdfError("体检报告菜品对数量无效")
    recommendations = report.get("recommendations") or []
    if not isinstance(recommendations, list):
        recommendations = []
    not_analyzed_dishes = report.get("not_analyzed_dishes") or []
    if not isinstance(not_analyzed_dishes, list):
        not_analyzed_dishes = []

    normalized_pairs = []
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        left = _normalize_side(pair.get("left"))
        right = _normalize_side(pair.get("right"))
        if not left or not right:
            continue
        risk = str(pair.get("risk_level") or "medium")
        normalized_pairs.append({
            "risk_level": risk if risk in {"high", "medium"} else "medium",
            "max_similarity": _float(pair.get("max_similarity")),
            "similar_sample_pair_count": _integer(pair.get("similar_sample_pair_count")),
            "left": left,
            "right": right,
        })

    generated_at = str(report.get("generated_at") or "")
    try:
        generated_time = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        if generated_time.tzinfo and has_app_context():
            generated_time = generated_time.astimezone(ZoneInfo(current_app.config.get("APP_TIMEZONE", "Asia/Shanghai")))
        generated_label = generated_time.strftime("%Y年%m月%d日 %H:%M")
    except (ValueError, TypeError):
        generated_label = generated_at or datetime.now().strftime("%Y年%m月%d日 %H:%M")

    return {
        "pipeline": pipeline,
        "pipeline_label": "纯视觉（SigLIP2 + DINOv3）" if pipeline == "visual" else "Qwen3-VL",
        "index_ready": bool(report.get("index_ready")),
        "generated_at": generated_at,
        "generated_label": generated_label,
        "thresholds": {
            "high": _float(thresholds.get("high")),
            "medium": _float(thresholds.get("medium")),
        },
        "summary": {
            key: _integer(summary.get(key))
            for key in (
                "total_active_dish_count",
                "indexed_dish_count",
                "indexed_sample_count",
                "invalid_sample_count",
                "analyzed_pair_count",
                "high_risk_pair_count",
                "medium_risk_pair_count",
                "safe_pair_count",
                "returned_pair_count",
                "truncated_pair_count",
                "not_analyzed_dish_count",
                "stale_indexed_dish_count",
            )
        },
        "pairs": normalized_pairs,
        "recommendations": [str(item)[:500] for item in recommendations if str(item).strip()][:20],
        "not_analyzed_dishes": [
            {
                "dish_name": str(item.get("dish_name") or "未命名菜品")[:100],
                "category": str(item.get("category") or "未分类")[:50],
                "sample_image_count": _integer(item.get("sample_image_count")),
            }
            for item in not_analyzed_dishes[:200]
            if isinstance(item, dict)
        ],
    }


def _normalize_side(side) -> dict | None:
    if not isinstance(side, dict):
        return None
    dish_id = _integer(side.get("dish_id"))
    if dish_id <= 0:
        return None
    return {
        "dish_id": dish_id,
        "dish_name": str(side.get("dish_name") or f"菜品 #{dish_id}")[:100],
        "category": str(side.get("category") or "未分类")[:50],
        "sample_count": _integer(side.get("sample_count")),
        "sample_image_id": _integer(side.get("sample_image_id")) or None,
        "sample_filename": str(side.get("sample_filename") or "")[:200],
        "exists": side.get("exists") is not False,
        "is_active": side.get("is_active") is not False,
    }


def _sample_image_data(pairs: list[dict]) -> dict[int, str]:
    references = {
        side["sample_image_id"]: side["dish_id"]
        for pair in pairs
        for side in (pair["left"], pair["right"])
        if side.get("sample_image_id")
    }
    if not references:
        return {}
    images = DishSampleImage.query.filter(DishSampleImage.id.in_(references)).all()
    result = {}
    for sample in images:
        if references.get(sample.id) != sample.dish_id or not sample.image_path or not os.path.isfile(sample.image_path):
            continue
        encoded = _thumbnail_data_uri(sample.image_path)
        if encoded:
            result[sample.id] = encoded
    return result


def _thumbnail_data_uri(path: str) -> str | None:
    try:
        from PIL import Image, ImageOps

        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((720, 540))
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=78, optimize=True)
        return f"data:image/jpeg;base64,{base64.b64encode(output.getvalue()).decode('ascii')}"
    except Exception:
        return None


def _assessment(report: dict) -> dict:
    summary = report["summary"]
    if not report["index_ready"]:
        return {"tone": "amber", "label": "索引未就绪", "message": "请先重建当前识别模式的样图索引，再重新体检。"}
    if summary["analyzed_pair_count"] == 0:
        return {"tone": "amber", "label": "暂无法评估", "message": "可用于跨菜品对比的索引菜品不足两个。"}
    if summary["high_risk_pair_count"] > 0:
        return {"tone": "red", "label": "需要优先处理", "message": f"发现 {summary['high_risk_pair_count']} 对高风险菜品，建议先复核最高相似样图。"}
    if summary["medium_risk_pair_count"] > 0:
        return {"tone": "amber", "label": "建议安排复核", "message": f"发现 {summary['medium_risk_pair_count']} 对中风险菜品，可通过补充差异化样图降低风险。"}
    return {"tone": "green", "label": "当前状态良好", "message": "当前索引内未发现达到预警阈值的跨菜品样图。"}


def _risk_distribution(summary: dict) -> dict:
    total = max(1, summary["analyzed_pair_count"])
    return {
        "high": summary["high_risk_pair_count"] / total * 100,
        "medium": summary["medium_risk_pair_count"] / total * 100,
        "safe": summary["safe_pair_count"] / total * 100,
    }


def _integer(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _float(value) -> float:
    try:
        return max(0.0, min(1.0, float(value or 0)))
    except (TypeError, ValueError):
        return 0.0


def _template():
    environment = Environment(autoescape=select_autoescape(default=True))
    environment.filters["pct"] = lambda value: f"{float(value or 0) * 100:.1f}%"
    return environment.from_string(_PDF_TEMPLATE)


_PDF_TEMPLATE = r"""
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
  @page {
    size: A4;
    margin: 15mm 14mm 17mm;
    @bottom-left { content: "菜品混淆体检报告"; color: #64748b; font-size: 8px; }
    @bottom-right {
      content: "第 " counter(page) " 页 / 共 " counter(pages) " 页";
      color: #64748b;
      font-size: 8px;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; color: #172033; font-family: "Noto Sans CJK SC", "PingFang SC", "Microsoft YaHei", sans-serif; font-size: 10px; line-height: 1.55; }
  h1, h2, h3, p { margin: 0; }
  .hero { padding: 22px 24px; border-radius: 14px; color: #fff; background: #0f172a; }
  .eyebrow { color: #5eead4; font-size: 8px; font-weight: 700; letter-spacing: 2px; }
  .hero h1 { margin-top: 7px; font-size: 23px; letter-spacing: -.5px; }
  .hero-meta { margin-top: 6px; color: #cbd5e1; font-size: 9px; }
  .assessment { display: table; width: 100%; margin-top: 12px; padding: 12px 14px; border: 1px solid #e2e8f0; border-radius: 10px; background: #f8fafc; }
  .assessment > div { display: table-cell; vertical-align: middle; }
  .assessment-label { width: 132px; font-size: 15px; font-weight: 700; }
  .tone-red { color: #b91c1c; } .tone-amber { color: #b45309; } .tone-green { color: #047857; }
  .metrics { display: table; width: 100%; table-layout: fixed; margin-top: 10px; border-spacing: 7px 0; }
  .metric { display: table-cell; padding: 10px 11px; border: 1px solid #e2e8f0; border-radius: 9px; }
  .metric-label { color: #64748b; font-size: 8px; } .metric-value { margin-top: 3px; font-size: 18px; font-weight: 700; } .metric-note { color: #94a3b8; font-size: 7px; }
  .section { margin-top: 18px; }
  .section-head { padding-bottom: 6px; border-bottom: 1px solid #cbd5e1; }
  .section-head h2 { font-size: 14px; } .section-head p { margin-top: 2px; color: #64748b; font-size: 8px; }
  .risk-bar { display: table; width: 100%; height: 8px; margin-top: 9px; overflow: hidden; border-radius: 99px; background: #e2e8f0; }
  .risk-bar span { display: table-cell; } .risk-high { background: #dc2626; } .risk-medium { background: #f59e0b; } .risk-safe { background: #10b981; }
  .risk-legend { margin-top: 5px; color: #64748b; font-size: 8px; }
  .pair { margin-top: 10px; padding: 12px; border: 1px solid #e2e8f0; border-left-width: 4px; border-radius: 10px; page-break-inside: avoid; }
  .pair-high { border-left-color: #dc2626; } .pair-medium { border-left-color: #f59e0b; }
  .pair-top { display: table; width: 100%; } .pair-top > div { display: table-cell; vertical-align: middle; }
  .badge { display: inline-block; padding: 2px 7px; border-radius: 99px; font-size: 8px; font-weight: 700; }
  .badge-high { color: #991b1b; background: #fee2e2; } .badge-medium { color: #92400e; background: #fef3c7; }
  .similarity { text-align: right; font-size: 15px; font-weight: 700; }
  .pair-grid { display: table; width: 100%; table-layout: fixed; margin-top: 9px; }
  .dish-side { display: table-cell; width: 46%; vertical-align: top; } .versus { display: table-cell; width: 8%; text-align: center; vertical-align: middle; color: #94a3b8; font-weight: 700; }
  .sample { width: 100%; height: 118px; object-fit: cover; border-radius: 7px; background: #f1f5f9; }
  .sample-empty { height: 118px; padding-top: 48px; border-radius: 7px; text-align: center; color: #94a3b8; background: #f1f5f9; }
  .dish-name { margin-top: 5px; font-size: 11px; font-weight: 700; } .dish-meta { color: #64748b; font-size: 8px; }
  .pair-note { margin-top: 8px; padding: 7px 9px; border-radius: 6px; color: #475569; background: #f8fafc; }
  .recommendations { margin: 8px 0 0; padding-left: 18px; } .recommendations li { margin: 4px 0; }
  table.uncovered { width: 100%; margin-top: 8px; border-collapse: collapse; }
  .uncovered th, .uncovered td {
    padding: 6px 7px;
    border-bottom: 1px solid #e2e8f0;
    text-align: left;
  }
  .uncovered th { color: #64748b; background: #f8fafc; }
  .footnote { margin-top: 16px; padding: 9px 11px; border-radius: 7px; color: #475569; background: #eff6ff; font-size: 8px; }
</style>
</head>
<body>
  <header class="hero">
    <div class="eyebrow">RECOGNITION RISK CHECK</div>
    <h1>菜品混淆体检报告</h1>
    <div class="hero-meta">{{ report.pipeline_label }} · 生成于 {{ report.generated_label }}</div>
  </header>
  <section class="assessment">
    <div class="assessment-label tone-{{ report.assessment.tone }}">{{ report.assessment.label }}</div>
    <div>{{ report.assessment.message }}</div>
  </section>
  <div class="metrics">
    <div class="metric">
      <div class="metric-label">高风险菜品对</div>
      <div class="metric-value tone-red">{{ report.summary.high_risk_pair_count }}</div>
      <div class="metric-note">阈值 ≥ {{ report.thresholds.high|pct }}</div>
    </div>
    <div class="metric">
      <div class="metric-label">中风险菜品对</div>
      <div class="metric-value tone-amber">{{ report.summary.medium_risk_pair_count }}</div>
      <div class="metric-note">阈值 ≥ {{ report.thresholds.medium|pct }}</div>
    </div>
    <div class="metric">
      <div class="metric-label">已完成对比</div>
      <div class="metric-value">{{ report.summary.analyzed_pair_count }}</div>
      <div class="metric-note">安全 {{ report.summary.safe_pair_count }} 对</div>
    </div>
    <div class="metric">
      <div class="metric-label">菜品覆盖率</div>
      <div class="metric-value">
        {{ (report.summary.indexed_dish_count / report.summary.total_active_dish_count
            if report.summary.total_active_dish_count else 0)|pct }}
      </div>
      <div class="metric-note">
        {{ report.summary.indexed_dish_count }} / {{ report.summary.total_active_dish_count }} 个菜品
      </div>
    </div>
  </div>
  <section class="section">
    <div class="section-head"><h2>风险分布</h2><p>基于当前索引内所有跨菜品组合</p></div>
    <div class="risk-bar">
      <span class="risk-high" style="width:{{ report.risk_distribution.high }}%"></span>
      <span class="risk-medium" style="width:{{ report.risk_distribution.medium }}%"></span>
      <span class="risk-safe" style="width:{{ report.risk_distribution.safe }}%"></span>
    </div>
    <div class="risk-legend">
      高风险 {{ report.summary.high_risk_pair_count }} 对　·　
      中风险 {{ report.summary.medium_risk_pair_count }} 对　·　
      安全 {{ report.summary.safe_pair_count }} 对
    </div>
  </section>
  <section class="section">
    <div class="section-head">
      <h2>优先复核清单</h2>
      <p>按最高相似度从高到低排列，共 {{ report.pairs|length }} 对</p>
    </div>
    {% if report.pairs %}
      {% for pair in report.pairs %}
      <article class="pair pair-{{ pair.risk_level }}">
        <div class="pair-top">
          <div><span class="badge badge-{{ pair.risk_level }}">
            {{ '高风险' if pair.risk_level == 'high' else '中风险' }}
          </span></div>
          <div class="similarity">{{ pair.max_similarity|pct }}</div>
        </div>
        <div class="pair-grid">
          {% for side in [pair.left, pair.right] %}
          <div class="dish-side">
            {% if side.sample_image_id and report.sample_images.get(side.sample_image_id) %}
            <img class="sample" src="{{ report.sample_images.get(side.sample_image_id) }}">
            {% else %}
            <div class="sample-empty">样图不可预览</div>
            {% endif %}
            <div class="dish-name">
              {{ side.dish_name }}
              {% if not side.exists %}（已删除）{% elif not side.is_active %}（已停用）{% endif %}
            </div>
            <div class="dish-meta">{{ side.category }} · 共 {{ side.sample_count }} 张样图</div>
          </div>
          {% if loop.first %}<div class="versus">VS</div>{% endif %}
          {% endfor %}
        </div>
        <div class="pair-note">
          有 {{ pair.similar_sample_pair_count }} 组跨菜品样图达到预警线。优先排查错标、重复图及裁剪范围过近。
        </div>
      </article>
      {% endfor %}
    {% else %}
      <div class="pair-note">当前没有需要展开复核的菜品对。</div>
    {% endif %}
  </section>
  <section class="section">
    <div class="section-head"><h2>处理建议</h2></div>
    <ol class="recommendations">
      {% for item in report.recommendations %}<li>{{ item }}</li>
      {% else %}<li>当前无额外处理建议。</li>{% endfor %}
    </ol>
  </section>
  {% if report.not_analyzed_dishes %}
  <section class="section">
    <div class="section-head">
      <h2>未纳入分析的菜品</h2>
      <p>共 {{ report.not_analyzed_dishes|length }} 个，需补齐样图并完成向量化</p>
    </div>
    <table class="uncovered">
      <thead><tr><th>菜品</th><th>分类</th><th>有效样图</th></tr></thead>
      <tbody>{% for dish in report.not_analyzed_dishes %}<tr>
        <td>{{ dish.dish_name }}</td>
        <td>{{ dish.category }}</td>
        <td>{{ dish.sample_image_count }}</td>
      </tr>{% endfor %}</tbody>
    </table>
  </section>
  {% endif %}
  <div class="footnote">说明：本报告比较当前识别索引中的全局样图向量，用于预警潜在混淆，不代表一定会发生误识别。实际结果还受餐盘裁剪、光线、候选召回和重排模型影响。</div>
</body></html>
"""
