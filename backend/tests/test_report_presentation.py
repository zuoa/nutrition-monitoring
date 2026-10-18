import io

from PIL import Image

from app.nutrition_metadata import DAILY_RECOMMENDED
from app.services.report_exports import render_personal_png, safe_filename
from app.services.report_presentation import personal_report_view, render_personal_html


def report_content():
    return {
        "student_name": "王家文", "class_name": "高一（5）班", "student_id": 1,
        "period_start": "2026-09-09", "period_end": "2026-09-15",
        "meal_statistics": {"meal_count": 18, "breakfast_count": 6},
        "avg_nutrients": dict(DAILY_RECOMMENDED), "recommended_nutrients": DAILY_RECOMMENDED,
        "overall_score": 100,
    }


def test_missing_values_do_not_create_evaluation_or_energy_chart():
    content = report_content()
    content["avg_nutrients"] = {key: None for key in DAILY_RECOMMENDED}
    view = personal_report_view(content)
    assert view["compliance"] is None
    assert view["score"] == "—"
    assert view["energy"] is None
    assert view["assessment"] == "暂无评价"
    assert "均衡" not in view["summary"]
    assert "暂无足够" in view["suggestions"][0]


def test_real_zero_is_measured_but_absent_sample_does_not_count():
    content = report_content()
    content["avg_nutrients"]["calcium"] = 0
    content["avg_nutrients"]["fat"] = 0
    content["nutrient_sample_counts"] = {key: 1 for key in DAILY_RECOMMENDED}
    content["nutrient_sample_counts"]["fat"] = 0
    view = personal_report_view(content)
    assert next(f for f in view["fields"] if f["key"] == "calcium")["status"] == "偏低"
    assert next(f for f in view["fields"] if f["key"] == "fat")["status"] == "暂无数据"
    assert view["energy"] is None
    assert not any("奶类" in suggestion or "蔬菜" in suggestion for suggestion in view["suggestions"])


def test_html_escapes_student_content_and_filename_prevents_paths():
    content = report_content()
    content["student_name"] = '<script>alert("test")</script>'
    html = render_personal_html(content)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "/" not in safe_filename("../../student/name\\test\x00")


def test_actual_png_is_high_resolution_and_contains_long_report():
    content = report_content()
    content["student_name"] = "测试学生姓名较长" * 4
    content["avg_nutrients"] = {key: value * .2 for key, value in DAILY_RECOMMENDED.items()}
    with Image.open(io.BytesIO(render_personal_png(content))) as image:
        assert image.format == "PNG"
        assert image.width == 1600
        assert image.height > 2400
        assert image.height < 28000


def modern_content():
    from app.services.report_reference import reference_snapshot
    from datetime import date, timedelta
    content = {**report_content(), **reference_snapshot()}
    from types import SimpleNamespace
    from app.services.report_projection import project_meals
    content["meal_projection"] = project_meals([SimpleNamespace(
        log_date=date(2026, 9, 9), meal_count=1,
        nutrient_totals={"_quality_version": 2, "_meal_slots": ["lunch"], **{key: value * .4 for key, value in DAILY_RECOMMENDED.items()}},
    )], date(2026, 9, 9), date(2026, 9, 15))
    content.update(coverage={"period_days": 7, "recorded_days": 5, "nutrition_days": 3, "historical_quality_days": 0},
                   nutrient_sample_counts={key: 3 for key in DAILY_RECOMMENDED},
                   meal_statistics={"meal_count": 8, "breakfast_count": 2, "unknown_count": 0,
                                    "records": [{"date": "2026-09-09", "slot": "lunch"}],
                                    "slots": [{"key": "lunch", "label": "午餐"}]},
                   nutrition_records=[{"date": "2026-09-09", "slot": "lunch"}],
                   energy_summary={"sample_count": 3, "percentages": {"carbohydrate": 55, "fat": 30, "protein": 15}},
                   daily_series=[{"date": (date(2026, 9, 9) + timedelta(days=i)).isoformat(),
                                  "nutrients": dict(DAILY_RECOMMENDED) if i in (0, 2, 3) else {}}
                                 for i in range(7)])
    return content


def test_modern_trends_preserve_gaps_and_single_points():
    content = modern_content()
    view = personal_report_view(content)
    trend = view["trends"][0]
    assert len(trend["dots"]) == 3
    assert len(trend["paths"]) == 2
    assert " " not in trend["paths"][0]  # isolated sample, no connection across the gap
    assert len(view["energy"]) == 3
    assert view["weeks"][0]["rows"][0]["cells"][0] == "nutrition"
    assert view["weeks"][0]["rows"][0]["cells"][1] == "absent"


def test_reference_values_use_verified_age_and_sex_groups():
    content = modern_content()
    rows = content["reference_table"]
    assert len(rows) == 8
    assert rows[0]["calories"] == 1700
    assert rows[1]["calories"] == 1550
    assert rows[4]["calcium"] == rows[5]["calcium"] == 950
    assert rows[6]["protein"] == 75 and rows[7]["protein"] == 60
    assert all("sodium" not in row for row in rows)


def test_modern_html_is_self_contained_and_has_no_diagnostic_score():
    content = modern_content()
    content["student_name"] = '<script>alert("test")</script>'
    html = render_personal_html(content)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "data:font/woff2;base64," in html
    assert "综合评分" not in html
    assert "综合达标" not in html
    assert "<svg" in html
    assert "不代表全天实际摄入" in html
    assert "早餐 25%" in html and "午餐 40%" in html and "晚餐 35%" in html
    assert "全天折算" in html and "能量参考比" in html
    view = personal_report_view(content)
    assert view["core"][0]["equivalent"] == "2000"


def test_modern_png_exports_entire_report_with_embedded_fonts():
    content = modern_content()
    content["student_name"] = "测试学生较长姓名" * 3
    with Image.open(io.BytesIO(render_personal_png(content))) as image:
        assert image.width == 1600
        assert 3000 < image.height < 28000
