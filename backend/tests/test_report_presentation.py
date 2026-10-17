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
