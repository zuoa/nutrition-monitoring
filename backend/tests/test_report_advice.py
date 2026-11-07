"""Advice selection, workbook ingestion, admin authorization and stored rendering."""

from copy import deepcopy
from datetime import date
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from flask import Flask
from openpyxl import Workbook, load_workbook
import pytest

from app import db
from app.api.admin import bp as admin_bp
from app.api.reports import bp as reports_bp
from app.nutrition_metadata import NUTRITION_FIELD_LABELS, NUTRITION_FIELD_UNITS
from app.services.report_advice import CATALOG, RULES, build_advice_snapshot, validate_report_advice_rules
from app.services.report_advice_import import advice_import_template, preview_advice_import
from app.services.report_assessment_ranges import DEFAULT_ASSESSMENT_RANGES
from app.services.report_brief import brief_report_view
from app.services.report_health import summarize_sports
from app.services.report_presentation import render_group_html, render_personal_html, serialize_personal_reports
from app.services.runtime_config import get_effective_config, persist_runtime_overrides
from app.tasks.reports import _save_report
from tests.test_report_assessment import content as personal_content
from tests.test_report_group_health import report_content


def personal(body="reference", **nutrients):
    content = personal_content()
    content["body_assessment"] = {"category": body}
    content["meal_projection"]["avg_nutrients"].update(nutrients)
    content["sports"] = {**summarize_sports([], date(2026, 10, 12), date(2026, 10, 18), date(2026, 10, 5), date(2026, 10, 11), ZoneInfo("Asia/Shanghai")),
                         "practice_count": 3, "active_days": 2, "project_count": 1}
    return content


def group(scope="class", **tones):
    content = report_content(scope=scope)
    content["health_summary"] = {
        "core": [{"key": key, "tone": tones.get(key, "ok"), "lower": 10, "upper": 20,
                  "label": NUTRITION_FIELD_LABELS[key], "unit": NUTRITION_FIELD_UNITS[key],
                  "status": {"ok": "正常", "low": "偏低", "high": "偏高"}[tones.get(key, "ok")],
                  "result": "5" if tones.get(key) == "low" else "30" if tones.get(key) == "high" else "15", "icon": "fiber",
                  "value": 5 if tones.get(key) == "low" else 30 if tones.get(key) == "high" else 15}
                 for key in ("calories", "protein", "carbohydrate", "fiber", "fat", "calcium")],
        "sports": {"participants": 4, "practice_count": 12, "previous_has_records": True, "practice_delta": -3},
    }
    return content


def diet(snapshot):
    return next(item for item in snapshot["items"] if item["category"] == "diet")


@pytest.mark.parametrize("body,suffix", [("thinness", "thin"), ("reference", "normal"), ("overweight", "heavy"), ("obesity", "heavy")])
def test_personal_body_stratification(body, suffix):
    selected = diet(build_advice_snapshot(personal(body, protein=20), "personal_weekly", {}))
    assert selected["rule_id"] == f"personal.diet.protein.low.{suffix}"
    assert selected["text"] == RULES[selected["rule_id"]]["phrases"][0]


def test_unknown_body_never_defaults_to_normal_and_missing_nutrients_do_not_trigger():
    assert diet(build_advice_snapshot(personal("unknown", protein=20), "personal_weekly", {}))["rule_id"] == "personal.diet.generic"
    content = personal()
    content["meal_projection"]["avg_nutrients"] = {}
    snapshot = build_advice_snapshot(content, "personal_weekly", {})
    assert diet(snapshot)["rule_id"] == "personal.diet.incomplete"
    assert all(".low." not in item["rule_id"] and ".high." not in item["rule_id"] for item in snapshot["items"])


def test_protection_combinations_and_largest_deviation_priority():
    content = personal("overweight", calories=1000, protein=20, fiber=3, fat=200)
    assert diet(build_advice_snapshot(content, "personal_weekly", {}))["rule_id"] == "personal.diet.heavy_energy_low"
    content["body_assessment"]["category"] = "thinness"
    assert diet(build_advice_snapshot(content, "personal_weekly", {}))["rule_id"] == "personal.diet.triple_low"
    content["meal_projection"]["avg_nutrients"].update(protein=75, fiber=25)
    assert diet(build_advice_snapshot(content, "personal_weekly", {}))["rule_id"] == "personal.diet.energy_fat"
    content = personal("overweight", carbohydrate=200, fat=150)
    assert diet(build_advice_snapshot(content, "personal_weekly", {}))["rule_id"] == "personal.diet.heavy_carb_fat"
    content = personal(calories=2500, protein=20)
    assert diet(build_advice_snapshot(content, "personal_weekly", {}))["rule_id"] == "personal.diet.protein.low.normal"


def test_fiber_high_requires_an_upper_boundary():
    content = personal(fiber=300)
    assert ".fiber.high." not in diet(build_advice_snapshot(content, "personal_weekly", {}))["rule_id"]
    ranges = deepcopy(DEFAULT_ASSESSMENT_RANGES)
    ranges["enabled"] = True
    ranges["nutrients"]["fiber"]["upper"] = 30
    content["report_assessment_ranges"] = ranges
    assert diet(build_advice_snapshot(content, "personal_weekly", {}))["rule_id"] == "personal.diet.fiber.high.normal"


def test_multiline_random_selection_and_disabled_rule_fallback():
    key = "personal.diet.protein.low.normal"
    config = {"REPORT_ADVICE_RULES": validate_report_advice_rules({key: {"phrases": " 第一条 \r\n\n第二条\n第一条"}})}
    assert config["REPORT_ADVICE_RULES"][key]["phrases"] == ["第一条", "第二条"]

    def chooser(choices):
        return choices[-1]
    assert diet(build_advice_snapshot(personal(protein=20), "personal_weekly", config, chooser=chooser))["text"] == "第二条"
    config["REPORT_ADVICE_RULES"][key]["enabled"] = False
    assert diet(build_advice_snapshot(personal(protein=20), "personal_weekly", config))["rule_id"] != key
    disabled = {"REPORT_ADVICE_RULES": {r["id"]: {"enabled": False, "phrases": []} for r in CATALOG}}
    assert all(item["rule_id"] == "system.fallback" for item in build_advice_snapshot(personal(), "personal_weekly", disabled)["items"])


def test_breakfast_uses_observed_records_and_family_is_bounded():
    content = personal("thinness")
    content["sports"] = {"practice_count": 0}
    content["meal_statistics"] = {"records": [{"date": "2026-10-12", "slot": "lunch", "source": "estimated"}]}
    snapshot = build_advice_snapshot(content, "personal_weekly", {})
    assert not any("breakfast_low" in item["rule_id"] for item in snapshot["items"])
    content["meal_statistics"]["records"][0]["source"] = "observed"
    family = [item for item in build_advice_snapshot(content, "personal_weekly", {})["items"] if item["category"] == "family"]
    assert len(family) == 2 and family[1]["rule_id"] == "personal.family.breakfast_low"


def test_group_individual_support_and_grade_factual_placeholders():
    content = group()
    content["health_summary"]["advice_signals"] = {"nutrition_abnormal": 2, "body_support": 1}
    snapshot = build_advice_snapshot(content, "class_weekly", {})
    assert snapshot["items"][-1]["rule_id"] == "class.family.body_support"
    content["health_summary"]["advice_signals"]["body_support"] = 0
    assert build_advice_snapshot(content, "class_weekly", {})["items"][-1]["rule_id"] == "class.family.any_abnormal"
    content = group("grade", calories="low", protein="low", fiber="low")
    snapshot = build_advice_snapshot(content, "grade_weekly", {})
    assert diet(snapshot)["rule_id"] == "grade.diet.triple_low"
    sports = next(item for item in snapshot["items"] if item["category"] == "sports")
    assert "4人" in sports["text"] and "12次" in sports["text"] and "下降3次" in sports["text"]
    assert "【" not in str(snapshot)
    assert not any("run_complete" in item["rule_id"] for item in snapshot["items"])
    del content["health_summary"]["sports"]["participants"]
    snapshot = build_advice_snapshot(content, "grade_weekly", {})
    assert not any("None" in item["text"] or "【" in item["text"] for item in snapshot["items"])


@pytest.mark.parametrize("kind", ["personal_monthly", "class_monthly", "grade_monthly", "campus_weekly", "school_monthly"])
def test_other_report_types_are_unchanged(kind):
    assert build_advice_snapshot(personal(), kind, {}) is None


@pytest.mark.parametrize("change", [
    {"phrases": []}, {"phrases": "【未知】"}, {"phrases": "{arbitrary.code}"}, {"phrases": "【人数】"},
    {"enabled": "false"}, {"match": {}}, {"priority": 200}, {"description": "x" * 501},
])
def test_configuration_rejects_invalid_content_and_immutable_fields(change):
    with pytest.raises(ValueError):
        validate_report_advice_rules({"personal.diet.generic": change})


def workbook_bytes(rows, name="标准模板"):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = name
    for row in rows:
        sheet.append(row)
    result = BytesIO()
    workbook.save(result)
    result.seek(0)
    return result


def test_standard_import_appends_deduplicates_and_preserves_config():
    key = "personal.diet.generic"
    config = {"REPORT_ADVICE_RULES": {key: {"enabled": False, "description": "我的说明", "phrases": ["已保存"]}}}
    imported = workbook_bytes([["策略编号", "策略说明", "多行话术"], [key, "导入说明", "已保存\n 新话术 \n新话术"]])
    preview = preview_advice_import(imported, config)
    assert not preview["errors"] and preview["added_count"] == 1
    assert preview["updates"][key] == {"phrases": ["已保存", "新话术"]}
    merged = validate_report_advice_rules(preview["updates"], config["REPORT_ADVICE_RULES"])
    assert merged[key]["enabled"] is False and merged[key]["description"] == "我的说明"
    assert config["REPORT_ADVICE_RULES"][key]["phrases"] == ["已保存"]


def test_import_errors_are_atomic_and_located():
    key = "personal.diet.generic"
    imported = workbook_bytes([["策略编号", "策略说明", "多行话术"], [key, "", "有效话术"], [key, "", "重复编号"],
                               ["missing", "", "错误"], ["class.diet.generic", "", "【人数】"]])
    preview = preview_advice_import(imported, {})
    assert not preview["updates"] and len(preview["errors"]) == 3
    assert all(f"标准模板!C{row}" in preview["errors"][index] for index, row in enumerate((3, 4, 5)))
    with pytest.raises(ValueError):
        preview_advice_import(BytesIO(b"not xlsx"), {})


def test_original_layout_imports_by_section_condition_and_column():
    original = workbook_bytes([["个人周报"], ["1 营养与体型匹配"], ["指标状态", "偏瘦学生", "体型正常学生", "偏胖学生"],
                               ["能量偏低", "瘦一\n瘦二", "正常话术", "胖话术"], ["2 按体型匹配运动建议"], ["偏瘦", "运动新话术"],
                               ["4 组合与冲突处理"], ["建议数量", "不作为话术"], ["组合示例条件：测试"], ["膳食／运动小建议", "示例"]], "个人周报")
    preview = preview_advice_import(original, {})
    assert not preview["errors"] and preview["strategy_count"] == 4
    assert preview["updates"]["personal.diet.calories.low.thin"]["phrases"][-2:] == ["瘦一", "瘦二"]
    assert preview["updates"]["personal.sports.thin"]["phrases"][-1] == "运动新话术"


def test_missing_condition_cell_reports_error_instead_of_crashing():
    original = workbook_bytes([["个人周报"], ["1 营养与体型匹配"], [None, "缺少条件的话术"]], "个人周报")
    preview = preview_advice_import(original, {})
    assert not preview["updates"] and "个人周报!A3" in preview["errors"][0]


def test_dimensionless_streaming_workbook_is_supported():
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("标准模板")
    sheet.append(["策略编号", "策略说明", "多行话术"])
    sheet.append(["personal.diet.generic", None, "新增通用话术"])
    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    preview = preview_advice_import(stream, {})
    assert not preview["errors"] and preview["added_count"] == 1


def test_template_round_trip_includes_all_rules_as_literal_text():
    config = {"REPORT_ADVICE_RULES": {"personal.diet.generic": {"phrases": ["=字面文本"]}}}
    output = advice_import_template(config)
    workbook = load_workbook(output)
    row = next(row for row in workbook.active if row[0].value == "personal.diet.generic")
    assert row[2].data_type == "s"
    output.seek(0)
    preview = preview_advice_import(output, config)
    assert not preview["errors"] and preview["strategy_count"] == len(CATALOG) and preview["added_count"] == 0


@pytest.fixture
def admin_app(tmp_path):
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:", SQLALCHEMY_TRACK_MODIFICATIONS=False,
                      LOCAL_RUNTIME_CONFIG_PATH=str(tmp_path / "runtime.json"), SERVER_NAME="localhost")
    db.init_app(app)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(reports_bp, url_prefix="/reports")
    return app


@pytest.mark.parametrize("role,status", [(None, 401), ("teacher", 403), ("admin", 200)])
def test_import_admin_authorization_and_preview_is_read_only(admin_app, role, status):
    user = SimpleNamespace(is_active=True, role=SimpleNamespace(value=role)) if role else None
    with patch("app.utils.jwt_utils.get_current_user", return_value=user):
        response = admin_app.test_client().post("/admin/report-advice/import-preview", data={"file": (advice_import_template({}), "test.xlsx")})
        assert response.status_code == status
        assert admin_app.test_client().get("/admin/report-advice/import-template").status_code == status
    with admin_app.app_context():
        assert not get_effective_config(admin_app.config).get("REPORT_ADVICE_RULES")


def test_config_save_preserves_other_settings_and_rejects_entire_invalid_update(admin_app):
    user = SimpleNamespace(is_active=True, role=SimpleNamespace(value="admin"))
    with patch("app.utils.jwt_utils.get_current_user", return_value=user):
        client = admin_app.test_client()
        assert client.put("/admin/config", json={"report_advice_rules": {"personal.diet.generic": {"phrases": "甲\n乙\n甲"}}}).status_code == 200
        assert client.put("/admin/config", json={"report_advice_rules": {"personal.diet.generic": {"phrases": ""}}, "video_analysis_max_concurrency": 8}).status_code == 400
    with admin_app.app_context():
        config = get_effective_config(admin_app.config)
        assert config["REPORT_ADVICE_RULES"]["personal.diet.generic"]["phrases"] == ["甲", "乙"]
        assert config.get("VIDEO_ANALYSIS_MAX_CONCURRENCY") is None


def test_saved_advice_is_stable_across_serialization_html_and_configuration_changes(admin_app):
    with admin_app.app_context():
        db.create_all()
        start, end = date(2026, 10, 12), date(2026, 10, 18)
        content = personal(protein=20)
        content.update(student_id=1, student_profile={"age_group": "15—17岁", "gender": "男"}, dining_features={"items": []})
        # New snapshots already contain current health and meal-completion versions.
        content["sports"]["version"] = 2
        content["period_comparison"] = {}
        from app.services.report_meal_completion import MEAL_COMPLETION_VERSION
        content["meal_statistics"] = {"completion_version": MEAL_COMPLETION_VERSION, "records": [], "meal_count": 0}
        content["body_assessment"] = {"category": "reference"}
        with patch("app.services.report_advice.random.choice", side_effect=lambda choices: choices[-1]):
            report = _save_report(None, "personal_weekly", 1, start, end, content, "test")
        original = deepcopy(report.content["advice_snapshot"])
        persist_runtime_overrides(admin_app.config, {"REPORT_ADVICE_RULES": {"personal.diet.protein.low.normal": {"phrases": ["已修改的新话术"]}}})
        serialized = serialize_personal_reports([report])[0]["content"]
        assert serialized["advice_snapshot"] == original
        assert brief_report_view(serialized)["health_suggestions"][:2] == original["items"][:2]
        assert brief_report_view(serialized)["health_suggestions"][-1]["title"] == "饮食运动协同"
        for report_format in ("brief", "detailed"):
            html = render_personal_html(serialized, report_format=report_format, embed_fonts=False)
            assert all(item["text"] in html for item in original["items"])
            assert "已修改的新话术" not in html
        for scope in ("class", "grade"):
            group_content = group(scope, calories="low")
            group_content["advice_snapshot"] = build_advice_snapshot(group_content, f"{scope}_weekly", {})
            html = render_group_html(group_content, embed_fonts=False)
            assert all(item["text"] in html for item in group_content["advice_snapshot"]["items"])
            assert "持续优化用油与菜品份量" not in html
        db.session.remove()
        db.drop_all()
