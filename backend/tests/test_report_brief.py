"""The brief must never infer food habits from nutrient totals or missing data."""

import io
from copy import deepcopy

import pytest
from PIL import Image

from app.services.report_assessment import assess_personal_report
from app.services.report_brief import brief_report_view
from app.services.report_exports import render_personal_png
from app.services.report_presentation import render_personal_html
from tests.test_report_presentation import modern_content, report_content
from tests.test_report_assessment import screenshot_content


def assessed_content():
    content = modern_content()
    content.update(age=15, gender="男", organization_name="杭州第四中学江东学校")
    content["meal_projection"]["avg_nutrients"].update(calories=2900, protein=94.5, fat=110, carbohydrate=400, fiber=0, calcium=850)
    return content


def test_brief_uses_same_results_severity_and_continuous_score_without_mutation():
    content = assessed_content()
    original = deepcopy(content)
    view = brief_report_view(content)
    assert view["assessment"] == assess_personal_report(content)
    fields = {field["key"]: field for field in view["core"]}
    assert fields["fiber"]["result"] == "0" and fields["fiber"]["tone"] == "low"
    assert fields["protein"]["status"] == "轻微偏高"
    assert fields["calcium"]["result"] == "850" and fields["calcium"]["unit"] == "mg"
    assert fields["calcium"]["status"] == "仅展示"
    assert "cholesterol" not in fields
    assert len(view["core"]) == 6
    assert view["assessment"]["score"] != view["assessment"]["passed_count"] / 5 * 100
    assert any(item["title"] == "控制脂肪供给" for item in view["suggestions"])
    assert "脂肪" in view["summary"] and "膳食纤维" in view["summary"]
    assert content == original


def test_brief_and_detailed_reports_show_b_for_moderate_imbalances():
    content = assessed_content()
    scenario = screenshot_content()
    content.update(report_assessment_ranges=scenario["report_assessment_ranges"], meal_projection=scenario["meal_projection"])
    original = deepcopy(content)
    for report_format in ("brief", "detailed"):
        html = render_personal_html(content, report_format=report_format)
        assert "B级 · 基本达标" in html and "C级 · 需改善" not in html
        assert "轻微偏高" in html and "偏低" in html and "偏高" in html
    assert content == original


def test_brief_does_not_invent_food_group_counts_or_habits():
    content = assessed_content()
    html = render_personal_html(content, report_format="brief")
    for unsupported in ("水果次数", "水果摄入次数", "油炸类摄入过多", "早餐摄入达标", "奶类及豆制品摄入适量", "胆固醇", "达标率"):
        assert unsupported not in html
    assert "就餐特征分析" in html
    assert "暂无实际就餐记录" in html
    assert "data:image/png;base64," in html and "data:font/woff2;base64," in html
    assessment = assess_personal_report(content)
    assert f'aria-label="营养等级 {assessment["level"]}级 · {assessment["label"]}"' in html
    assert 'fill="#005438">' + assessment["level"] + '</text>' in html
    assert "综合评分" not in html and "100分" not in html
    assert "score-ring" not in html
    assert "仅展示" not in html and "不参与评级" not in html
    assert 'data-nutrient="calcium"' in html and 'class="metric-label">钙</p>' in html
    assert 'data-nutrient="cholesterol"' not in html
    assert 'aria-hidden="true">Ca</div>' in html


def test_reports_show_fixed_reference_and_range_for_2391_kcal():
    content = assessed_content()
    content["meal_projection"]["avg_nutrients"]["calories"] = 2391.6
    content["report_nutrient_gains"] = {"calories": 2}
    for report_format in ("brief", "detailed"):
        html = render_personal_html(content, report_format=report_format)
        assert "2391.6" in html and "轻微偏低" in html
        assert "能量参考 2900 kcal/天" in html
        assert "参考 2610–3190 kcal/天" in html
        assert "参考 64.4–96.7 g/天" in html
        assert "不随营养素系数变化" in html


def test_missing_estimates_never_fall_back_to_raw_values_or_create_advice():
    content = assessed_content()
    content["meal_projection"]["avg_nutrients"].update(calories=None, fat=None, fiber=None, calcium=None)
    view = brief_report_view(content)
    assert view["assessment"]["level"] is None
    fields = {field["key"]: field for field in view["core"]}
    assert fields["calories"]["result"] == "—"
    assert fields["fat"]["result"] == "—"
    assert fields["calcium"]["result"] == "—" and fields["calcium"]["status"] == "暂无数据"
    assert not any(item["title"] in ("控制脂肪供给", "补充膳食纤维") for item in view["suggestions"])
    assert view["features"][1]["status"] == "暂无数据"
    assert [item["title"] for item in view["features"]] == ["就餐规律", "选餐多样性", "搭配习惯", "食物选择倾向"]
    html = render_personal_html(content, report_format="brief")
    assert 'aria-label="营养等级待评价"' in html and "综合评分" not in html


def test_old_snapshot_without_projection_preserves_data_but_has_no_student_rating():
    content = report_content()
    view = brief_report_view(content)
    assert view["assessment"]["score"] is None
    assert view["core"][0]["result"] == "2000"
    assert view["core"][-1]["key"] == "calcium" and view["core"][-1]["result"] == "1000"
    assert view["energy"] == []
    assert all(field["tone"] == "unknown" for field in view["core"])
    assert "暂不作营养达标判断" in view["summary"]


@pytest.mark.parametrize("percentages", [
    None, {"fat": 100}, {"fat": -1, "carbohydrate": 86, "protein": 15},
    {"fat": 50, "carbohydrate": 55, "protein": 15}, {"fat": float("nan"), "carbohydrate": 55, "protein": 15},
])
def test_incomplete_or_invalid_energy_samples_never_make_a_chart(percentages):
    content = assessed_content()
    content["energy_summary"] = {"percentages": percentages}
    assert brief_report_view(content)["energy"] == []


def test_monthly_copy_and_safe_names_and_conditional_branding():
    content = assessed_content()
    content.update(report_type="personal_monthly", organization_name="其他学校", student_name='<script>alert("test")</script>')
    html = render_personal_html(content, report_format="brief")
    assert "学生体质健康月报" in html and "本月体育锻炼分析" in html and "较上月变化" in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert 'alt="杭州第四中学校徽"' not in html
    assert 'alt="杭州第四中学校徽"' in render_personal_html(assessed_content(), report_format="brief")


def test_brief_png_contains_the_whole_report_and_preserves_detailed_template():
    content = assessed_content()
    with Image.open(io.BytesIO(render_personal_png(content, report_format="brief"))) as image:
        assert image.width == 1600
        assert 1600 < image.height < 4000
    detailed = render_personal_html(content)
    assert "营养变化趋势" in detailed and "用餐记录" in detailed
    assert "学生营养核心指标" not in detailed
    assert 'data-nutrient="cholesterol"' in detailed and 'data-nutrient="calcium"' not in detailed
    assert detailed == render_personal_html(content, report_format="detailed")
    with pytest.raises(ValueError, match="不支持的报告版本"):
        render_personal_html(content, report_format="unknown")
