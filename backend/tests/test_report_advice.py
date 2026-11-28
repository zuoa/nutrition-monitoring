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
from app.services.report_advice import (
    CATALOG, RULES, RULES_CONFIG_KEY, RUN_REFERENCES_CONFIG_KEY, build_advice_snapshot, validate_report_advice_rules,
)
from app.services.report_advice_import import advice_import_template, preview_advice_import
from app.services.report_assessment_ranges import DEFAULT_ASSESSMENT_RANGES
from app.services.report_assessment_scoring import DEFAULT_ASSESSMENT_SCORING
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


def selected(content, report_type, category, config=None):
    snapshot = build_advice_snapshot(content, report_type, config or {}, chooser=lambda choices: choices[-1])
    return [item for item in snapshot["items"] if item["category"] == category]


def ids(items):
    return [item["rule_id"] for item in items]


@pytest.mark.parametrize("body,suffix", [("thinness", "thin"), ("reference", "normal"), ("overweight", "heavy"), ("obesity", "heavy")])
def test_personal_body_stratification(body, suffix):
    assert ids(selected(personal(body, protein=20), "personal_weekly", "diet")) == [f"personal.diet.protein.low.{suffix}"]


def test_personal_diet_uses_priority_and_requires_body_for_abnormalities():
    content = personal("overweight", calories=1000, protein=20, fiber=3, fat=200)
    assert ids(selected(content, "personal_weekly", "diet")) == ["personal.diet.protein.low.heavy", "personal.diet.fat.high.heavy"]
    content["body_assessment"]["category"] = "unknown"
    assert selected(content, "personal_weekly", "diet") == []
    del content["meal_projection"]["avg_nutrients"]["protein"]
    content["meal_projection"]["avg_nutrients"] = {"calcium": 800}
    assert ids(selected(content, "personal_weekly", "diet")) == ["personal.diet.unavailable"]
    assert ids(selected(content, "personal_weekly", "family")) == ["personal.family.nutrition.unavailable"]


def test_fiber_high_requires_an_upper_boundary():
    content = personal(calories=2000, protein=60, fat=50, carbohydrate=300, fiber=300)
    assert not any(".fiber.high." in key for key in ids(selected(content, "personal_weekly", "diet")))
    ranges = deepcopy(DEFAULT_ASSESSMENT_RANGES)
    ranges["enabled"] = True
    ranges["nutrients"]["fiber"]["upper"] = 30
    content["report_assessment_ranges"] = ranges
    assert "personal.diet.fiber.high.normal" in ids(selected(content, "personal_weekly", "diet"))


def test_saved_overrides_are_independent_and_disabled_rules_are_omitted():
    key = "personal.diet.protein.low.normal"
    legacy = {"REPORT_ADVICE_RULES": {key: {"phrases": ["旧配置"]}}}
    content = personal(protein=20)
    assert selected(content, "personal_weekly", "diet", legacy)[0]["text"] == RULES[key]["phrases"][0]
    config = {RULES_CONFIG_KEY: validate_report_advice_rules({key: {"phrases": " 第一条 \r\n\n第二条\n第一条"}})}
    assert config[RULES_CONFIG_KEY][key]["phrases"] == ["第一条", "第二条"]
    assert selected(content, "personal_weekly", "diet", config)[0]["text"] == "第二条"
    config[RULES_CONFIG_KEY][key]["enabled"] = False
    assert selected(content, "personal_weekly", "diet", config) == []


@pytest.mark.parametrize("count,expected", [(0, True), (1, True), (2, False)])
def test_personal_breakfast_threshold_excludes_estimated_and_duplicate_records(count, expected):
    content = personal()
    records = [{"date": f"2026-10-{12 + index}", "slot": "breakfast", "source": "observed"} for index in range(count)]
    content["meal_statistics"] = {"records": records + records + [{"date": "2026-10-15", "slot": "breakfast", "source": "estimated"}]}
    keys = ids(selected(content, "personal_weekly", "family"))
    assert ("personal.family.breakfast_low" in keys) is expected
    assert sum(".nutrition." in key for key in keys) == 1


def test_personal_run_energy_uses_grade_sex_and_two_complete_periods():
    content = personal()
    content["student_profile"] = {"grade_name": "高二年级", "gender": "男", "age_group": "15—17岁"}
    kind = "personal_weekly"
    assert ids(selected(content, kind, "sports")) == ["personal.sports.senior_2.missing"]
    sports = content["sports"]
    sports.update(run_energy_count=2, run_energy_complete=True, run_energy_kcal=80,
                  previous_run_energy_complete=True, previous_run_energy_kcal=60)
    config = {RUN_REFERENCES_CONFIG_KEY: {"senior_2": {"男": 80, "女": 100}}}
    assert selected(content, kind, "sports") == []
    assert ids(selected(content, kind, "sports", config)) == ["personal.sports.senior_2.normal.met"]
    content["student_profile"]["gender"] = "女"
    assert ids(selected(content, kind, "sports", config)) == ["personal.sports.senior_2.normal.below"]
    assert "personal.family.run.normal.up" in ids(selected(content, kind, "family"))
    sports["previous_run_energy_complete"] = False
    assert not any(".run." in key for key in ids(selected(content, kind, "family")))


def test_group_diet_priority_and_partial_nutrition():
    content = group(calories="low", protein="low", fiber="low")
    assert ids(selected(content, "class_weekly", "diet")) == ["class.diet.protein.low", "class.diet.calories.low"]
    content["health_summary"]["core"] = [field for field in content["health_summary"]["core"] if field["key"] in ("fat", "calcium")]
    assert ids(selected(content, "class_weekly", "diet")) == ["class.diet.normal"]
    content["health_summary"]["core"].pop(0)
    assert ids(selected(content, "class_weekly", "diet")) == ["class.diet.unavailable"]
    content = personal()
    ranges = deepcopy(DEFAULT_ASSESSMENT_RANGES)
    ranges["enabled"] = True
    content["report_assessment_ranges"] = ranges
    content["meal_projection"]["avg_nutrients"] = {"protein": 60}
    assert selected(content, "personal_weekly", "diet") == []
    content["meal_projection"]["avg_nutrients"].update(calories=2000, carbohydrate=300, fiber=25, fat=50)
    assert ids(selected(content, "personal_weekly", "diet")) == ["personal.diet.normal"]


def test_class_candidates_use_strict_cd_boundary_and_category_limits():
    content = group()
    current = {"assessed_students": 10, "cd_count": 3, "participants": 2, "unrecorded": 8, "breakfast": 3, "fat": 4, "d": 2}
    previous = {"participants": 4, "unrecorded": 6, "breakfast": 2, "fat": 3, "d": 1}
    content["health_summary"]["advice_metrics"] = {"current": current, "previous": previous, "breakfast_threshold": 2}
    assert ids(selected(content, "class_weekly", "coordination")) == ["class.coordination.breakfast_up", "class.coordination.fat_up"]
    assert ids(selected(content, "class_weekly", "family")) == ["class.family.d_up", "class.family.breakfast_up", "class.family.fat_up"]
    current["cd_count"] = 4
    assert ids(selected(content, "class_weekly", "coordination")) == ["class.coordination.cd_high", "class.coordination.breakfast_up"]
    family = selected(content, "class_weekly", "family")
    assert ids(family) == ["class.family.cd_high", "class.family.d_up", "class.family.breakfast_up"]
    assert "共4人" in family[0]["text"] and "40.0%" in family[0]["text"]
    assert "减少2人" in selected(content, "class_weekly", "sports")[0]["text"]


def test_class_routines_require_all_related_stats_and_no_trigger():
    content = group()
    current = {"assessed_students": 10, "cd_count": 3, "participants": 6, "unrecorded": 4, "breakfast": 3, "fat": 4, "d": 2}
    previous = {**current}
    metrics = {"current": current, "previous": previous, "breakfast_threshold": 2}
    content["health_summary"]["advice_metrics"] = metrics
    assert ids(selected(content, "class_weekly", "sports")) == ["class.sports.participants.steady"]
    assert ids(selected(content, "class_weekly", "coordination")) == ["class.coordination.routine"]
    assert ids(selected(content, "class_weekly", "family")) == ["class.family.routine"]
    del previous["breakfast"]
    assert selected(content, "class_weekly", "coordination") == []
    assert selected(content, "class_weekly", "family") == []
    del previous["participants"]
    assert selected(content, "class_weekly", "sports") == []


def test_class_monthly_uses_actual_breakfast_threshold_and_saveable_tokens(admin_app):
    content = group()
    content["report_type"] = "class_monthly"
    content["health_summary"]["advice_metrics"] = {
        "current": {"breakfast": 3, "participants": 4}, "previous": {"breakfast": 2, "participants": 3}, "breakfast_threshold": 9,
    }
    snapshot = build_advice_snapshot(content, "class_monthly", {})
    family = next(item for item in snapshot["items"] if item["category"] == "family")
    assert "早餐记录不多于9次" in family["text"] and "较上月增加1人" in family["text"]
    sports = next(item for item in snapshot["items"] if item["category"] == "sports")
    assert "本月有体育记录4人，较上月增加1人" in sports["text"]
    with admin_app.test_request_context():
        html = render_group_html({**content, "advice_snapshot": snapshot}, embed_fonts=False)
    assert all(item["text"] in html for item in snapshot["items"])


@pytest.mark.parametrize("current,direction", [(80.5, "up"), (20.3, "down"), (60, "steady"), (None, None)])
def test_grade_monthly_energy_change_and_nutrient_coordination(admin_app, current, direction):
    content = group("grade", calories="high", fat="low", protein="high")
    content["report_type"] = "grade_monthly"
    content["health_summary"]["advice_metrics"] = {"run_energy": {"current": current, "previous": 60}}
    snapshot = build_advice_snapshot(content, "grade_monthly", {})
    sports = [item for item in snapshot["items"] if item["category"] == "sports"]
    if direction:
        assert ids(sports) == [f"grade.sports.senior_2.{direction}"]
        assert f"本月估算跑步消耗{current}千卡" in sports[0]["text"]
    else:
        assert sports == []
    assert ids([item for item in snapshot["items"] if item["category"] == "coordination"]) == [
        "grade.coordination.protein.high.senior_2", "grade.coordination.fat.low.senior_2",
    ]
    with admin_app.test_request_context():
        html = render_group_html({**content, "advice_snapshot": snapshot}, embed_fonts=False)
    assert all(item["text"] in html for item in snapshot["items"])
    assert "【" not in html


def test_grade_coordination_normal_requires_all_five_nutrients():
    content = group("grade")
    assert ids(selected(content, "grade_weekly", "coordination")) == ["grade.coordination.senior_2.normal"]
    content["health_summary"]["core"] = [field for field in content["health_summary"]["core"] if field["key"] != "fiber"]
    assert selected(content, "grade_weekly", "coordination") == []


@pytest.mark.parametrize("kind", ["personal_monthly", "campus_weekly", "school_monthly"])
def test_other_report_types_are_unchanged(kind):
    assert build_advice_snapshot(personal(), kind, {}) is None


@pytest.mark.parametrize("change", [
    {"phrases": []}, {"phrases": "【未知】"}, {"phrases": "{arbitrary.code}"}, {"phrases": "【人数】"},
    {"enabled": "false"}, {"match": {}}, {"priority": 200}, {"description": "x" * 501},
])
def test_configuration_rejects_invalid_content_and_immutable_fields(change):
    with pytest.raises(ValueError):
        validate_report_advice_rules({"personal.diet.normal": change})


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
    key = "personal.diet.normal"
    config = {RULES_CONFIG_KEY: {key: {"enabled": False, "description": "我的说明", "phrases": ["已保存"]}}}
    imported = workbook_bytes([["策略编号", "策略说明", "多行话术"], [key, "导入说明", "已保存\n 新话术 \n新话术"]])
    preview = preview_advice_import(imported, config)
    assert not preview["errors"] and preview["added_count"] == 1
    assert preview["updates"][key] == {"phrases": ["已保存", "新话术"]}
    merged = validate_report_advice_rules(preview["updates"], config[RULES_CONFIG_KEY])
    assert merged[key]["enabled"] is False and merged[key]["description"] == "我的说明"
    assert config[RULES_CONFIG_KEY][key]["phrases"] == ["已保存"]


def test_import_errors_are_atomic_and_located():
    key = "personal.diet.normal"
    imported = workbook_bytes([["策略编号", "策略说明", "多行话术"], [key, "", "有效话术"], [key, "", "重复编号"],
                               ["missing", "", "错误"], ["class.diet.normal", "", "【人数】"]])
    preview = preview_advice_import(imported, {})
    assert not preview["updates"] and len(preview["errors"]) == 3
    assert all(f"标准模板!C{row}" in preview["errors"][index] for index, row in enumerate((3, 4, 5)))
    with pytest.raises(ValueError):
        preview_advice_import(BytesIO(b"not xlsx"), {})


def test_original_layout_imports_all_rules_and_matches_inherited_categories():
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, size in (("个人周报", 65), ("班级周报", 26), ("年级周报", 54)):
        sheet = workbook.create_sheet(title)
        sheet.append([title + "建议库"])
        sheet.append(["建议类别", "建议数量", "匹配规则", "策略说明", "候选话术"])
        rules = [rule for rule in CATALOG if rule["source"]["sheet"] == title]
        assert len(rules) == size
        category = None
        for rule in rules:
            source = rule["source"]
            sheet.append([source["category"] if source["category"] != category else None, None,
                          rule["condition"], source["description"], rule["phrases"][0]])
            category = source["category"]
    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    preview = preview_advice_import(stream, {})
    assert not preview["errors"] and preview["strategy_count"] == 145 and preview["added_count"] == 0


def test_missing_condition_cell_reports_error_instead_of_crashing():
    original = workbook_bytes([["个人周报建议库"], ["建议类别", "建议数量", "匹配规则", "策略说明", "候选话术"],
                               ["膳食建议", None, None, None, "缺少条件的话术"]], "个人周报")
    preview = preview_advice_import(original, {})
    assert not preview["updates"] and "个人周报!D3" in preview["errors"][0]


def test_dimensionless_streaming_workbook_is_supported():
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("标准模板")
    sheet.append(["策略编号", "策略说明", "多行话术"])
    sheet.append(["personal.diet.normal", None, "新增通用话术"])
    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    preview = preview_advice_import(stream, {})
    assert not preview["errors"] and preview["added_count"] == 1


def test_template_round_trip_includes_all_rules_as_literal_text():
    config = {RULES_CONFIG_KEY: {"personal.diet.normal": {"phrases": ["=字面文本"]}}}
    output = advice_import_template(config)
    workbook = load_workbook(output)
    row = next(row for row in workbook.active if row[0].value == "personal.diet.normal")
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
        assert not get_effective_config(admin_app.config).get(RULES_CONFIG_KEY)


def test_config_save_preserves_other_settings_and_rejects_entire_invalid_update(admin_app):
    user = SimpleNamespace(is_active=True, role=SimpleNamespace(value="admin"))
    with patch("app.utils.jwt_utils.get_current_user", return_value=user):
        client = admin_app.test_client()
        persist_runtime_overrides(admin_app.config, {"REPORT_ADVICE_RULES": {"personal.diet.normal": {"phrases": ["旧话术"]}}})
        assert client.put("/admin/config", json={"report_advice_rules": {"personal.diet.normal": {"phrases": "甲\n乙\n甲"}}}).status_code == 200
        assert client.put("/admin/config", json={"report_advice_rules": {"personal.diet.normal": {"phrases": ""}}, "video_analysis_max_concurrency": 8}).status_code == 400
    with admin_app.app_context():
        config = get_effective_config(admin_app.config)
        assert config[RULES_CONFIG_KEY]["personal.diet.normal"]["phrases"] == ["甲", "乙"]
        assert config["REPORT_ADVICE_RULES"]["personal.diet.normal"]["phrases"] == ["旧话术"]
        assert config.get("VIDEO_ANALYSIS_MAX_CONCURRENCY") is None


@pytest.mark.parametrize("invalid", [{"senior_4": {"男": 100}}, {"senior_2": {"女": 0}},
                                    {"senior_2": {"女": True}}, {"senior_2": {"女": "100"}}])
def test_admin_run_energy_references_accept_positive_values_and_blank(admin_app, invalid):
    user = SimpleNamespace(is_active=True, role=SimpleNamespace(value="admin"))
    client = admin_app.test_client()
    with patch("app.utils.jwt_utils.get_current_user", return_value=user):
        response = client.put("/admin/config", json={"report_run_energy_references": {"senior_2": {"男": 100.5, "女": None}}})
        assert response.status_code == 200
        assert client.put("/admin/config", json={"report_run_energy_references": invalid}).status_code == 400
    assert get_effective_config(admin_app.config)[RUN_REFERENCES_CONFIG_KEY] == {"senior_2": {"男": 100.5}}


def test_saved_advice_is_stable_across_serialization_html_and_configuration_changes(admin_app):
    with admin_app.app_context():
        db.create_all()
        start, end = date(2026, 10, 12), date(2026, 10, 18)
        content = personal(protein=20)
        content.update(student_id=1, student_profile={"age_group": "15—17岁", "gender": "男"}, dining_features={"items": []})
        content["report_assessment_scoring"] = DEFAULT_ASSESSMENT_SCORING
        # New snapshots already contain current health and meal-completion versions.
        content["sports"]["version"] = 2
        content["period_comparison"] = {}
        from app.services.report_meal_completion import MEAL_COMPLETION_VERSION
        content["meal_statistics"] = {"completion_version": MEAL_COMPLETION_VERSION, "records": [], "meal_count": 0}
        content["body_assessment"] = {"category": "reference"}
        with patch("app.services.report_advice.random.choice", side_effect=lambda choices: choices[-1]):
            report = _save_report(None, "personal_weekly", 1, start, end, content, "test")
        original = deepcopy(report.content["advice_snapshot"])
        persist_runtime_overrides(admin_app.config, {RULES_CONFIG_KEY: {"personal.diet.protein.low.normal": {"phrases": ["已修改的新话术"]}}})
        serialized = serialize_personal_reports([report])[0]["content"]
        assert serialized["advice_snapshot"] == original
        assert brief_report_view(serialized)["health_suggestions"] == [item for item in original["items"] if item["category"] in ("diet", "sports")]
        assert brief_report_view(serialized)["family"] == [item for item in original["items"] if item["category"] == "family"]
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
