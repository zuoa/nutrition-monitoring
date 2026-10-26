"""Group health reports retain personal reference rules without identifying students."""

import io
from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from PIL import Image
import pytest

from app import db
from app.api.reports import bp as reports_bp
from app.models import Campus, Class, Dish, Grade, NutritionLog, Report, ReportTypeEnum, School, Stage, Student
from app.services.nutrition_service import NutritionService
from app.services.report_exports import render_group_png
from app.services.report_group_health import build_group_health, group_period_comparison, group_report_view
from app.services.report_presentation import render_group_html, serialize_group_report
from tests.test_report_dining_features import add_recognition
from tests.test_report_health import sport

START, END = date(2026, 10, 19), date(2026, 10, 23)


@pytest.fixture
def group_db(tmp_path):
    app = Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI="sqlite:///:memory:", SQLALCHEMY_TRACK_MODIFICATIONS=False,
                      APP_TIMEZONE="Asia/Shanghai", LOCAL_RUNTIME_CONFIG_PATH=str(tmp_path / "runtime.json"))
    db.init_app(app)
    app.register_blueprint(reports_bp, url_prefix="/api/v1/reports")
    with app.app_context():
        db.create_all()
        school = School(name="测试学校")
        db.session.add(school)
        db.session.flush()
        campus = Campus(name="东校区", school_id=school.id)
        db.session.add(campus)
        db.session.flush()
        stage = Stage(name="高中", campus_id=campus.id)
        db.session.add(stage)
        db.session.flush()
        grade = Grade(name="高二", stage_id=stage.id)
        db.session.add(grade)
        db.session.flush()
        classes = [Class(name=f"{index}班", grade_id=grade.id) for index in (1, 2)]
        db.session.add_all(classes)
        db.session.flush()
        students = [Student(student_no=f"group-{index}", name=f"不应公开学生{index}", class_id=classes[index // 2].id,
                            age=16, gender="男" if index != 1 else "女") for index in range(3)]
        db.session.add_all(students)
        db.session.commit()
        yield SimpleNamespace(app=app, campus=campus, grade=grade, classes=classes, students=students)
        db.session.remove()
        db.drop_all()


def averages(**values):
    return {"projection": {"avg_nutrients": {"calories": 2900, "protein": 75, "fat": 80, "carbohydrate": 420,
                                             "fiber": 25, "calcium": 800, **values}}}


def report_content(summary=None, scope="class"):
    return {"scope_type": scope, "scope_id": 1, "scope_name": "高二（2）班" if scope == "class" else "高二年级",
            "period_start": START.isoformat(), "period_end": END.isoformat(), "report_type": f"{scope}_weekly",
            "student_count": 3, "organization_name": "测试学校", "health_summary": summary or {},
            "analysis_basis": "student_period_average"}


def test_equal_student_weights_missing_references_and_calcium_chart(group_db):
    students = group_db.students
    students[2].gender = None
    inputs = [averages(), averages(calories=2350, protein=60, fat=65, carbohydrate=340, calcium=0),
              averages(calories=2000, protein=None, fat=None, carbohydrate=None, fiber=None, calcium=None)]
    result = build_group_health(students, inputs, {}, START, END)
    fields = {field["key"]: field for field in result["core"]}
    assert result["assessed_students"] == 2 and result["unassessed_students"] == 1
    assert [row["count"] for row in result["distribution"]] == [2, 0, 0, 0]
    assert sum(row["percentage"] for row in result["distribution"]) == 100
    assert fields["calories"]["value"] == 2416.7
    assert fields["calories"]["tone"] == "unknown"  # No mismatched reference denominator.
    assert fields["protein"]["value"] == 67.5 and fields["protein"]["count"] == 2
    assert fields["calcium"]["value"] == 400 and fields["calcium"]["tone"] == "unknown"
    achievement = {row["key"]: row for row in result["class_achievement"]}
    assert achievement["calories"] == {"key": "calories", "label": "能量", "passed": 1, "assessed": 1, "total": 2, "rate": 100}
    assert achievement["calcium"]["assessed"] == 0 and achievement["calcium"]["rate"] is None
    view = group_report_view(report_content(result))
    calcium = next(point for point in view["chart"]["points"] if point["index"] == 5)
    assert calcium["ratio"] == 50
    assert not any(point["index"] == 0 for point in view["chart"]["points"])
    assert not any(student.name in str(result) or student.student_no in str(result) for student in students)


def test_sports_scope_modes_previous_projects_and_observed_participation(group_db):
    first, second, third = group_db.students
    for kind in (1, 1, 1, 2, 99):
        sport(first, kind=kind)
    sport(third, kind=6)
    sport(third, kind=6)
    sport(first, "2026-10-12T00:00:00", kind=7)
    for kind, mode in ((1, 2), (9, 1), (10, 1)):
        sport(second, kind=kind, mode=mode)
    outsider = Student(student_no="outside-group", name="其他范围学生")
    db.session.add(outsider)
    db.session.flush()
    sport(outsider)
    observed = {"date": START.isoformat(), "slot": "lunch", "source": "observed"}
    estimated = {"date": START.isoformat(), "slot": "breakfast", "source": "estimated"}
    attendance = {first.id: {"records": [observed, observed, estimated]}, second.id: {"records": [estimated]}}
    result = build_group_health(group_db.students, [averages(), averages(fat=400), averages()], attendance, START, END)
    assert result["participating_students"] == 1
    assert result["sports"] == {"practice_count": 7, "previous_practice_count": 1, "practice_delta": 6,
                                "previous_has_records": True, "participants": 2, "unrecorded_students": 1,
                                "top_projects": [{"name": "跳绳", "count": 3}, {"name": "仰卧起坐", "count": 2},
                                                 {"name": "跳远", "count": 1}], "other_count": 1}
    focus = {row["label"]: row["count"] for row in result["focus"]}
    assert focus["早餐记录不多于2次"] == 1
    assert focus["营养D级且未见练习记录"] == 1


def test_empty_group_and_missing_previous_sports_do_not_invent_changes(group_db):
    empty = build_group_health([], [], {}, START, END)
    assert empty["assessed_students"] == empty["unassessed_students"] == 0
    assert empty["sports"]["practice_delta"] is None
    assert all(field["value"] is None for field in empty["core"])
    sport(group_db.students[0])
    result = build_group_health(group_db.students, [averages()] * 3, {}, START, END)
    assert result["sports"]["practice_count"] == 1 and result["sports"]["practice_delta"] is None
    html = render_group_html(report_content(result))
    assert "暂无对比" in html and "体重超标" not in html


def test_popular_dishes_composition_and_food_groups_use_real_recognition(group_db):
    first, second, _ = group_db.students
    image, mixed = add_recognition(first)
    mixed.name, mixed.food_groups = "鸡肉配蔬菜", ["禽肉", "蔬菜"]
    add_recognition(first, image=image, dish=mixed)  # Duplicate recognition is one choice.
    add_recognition(second, dish=mixed)
    vegetable = Dish(name="清炒青菜", category="素菜", price=2, food_groups=["蔬菜"])
    db.session.add(vegetable)
    add_recognition(second, hour=18, dish=vegetable)
    result = build_group_health(group_db.students, [averages()] * 3, {}, START, END)
    assert result["dining"]["popular"] == [
        {"id": mixed.id, "name": mixed.name, "count": 2, "food_groups": ["禽肉", "蔬菜"]},
        {"id": vegetable.id, "name": vegetable.name, "count": 1, "food_groups": ["蔬菜"]},
    ]
    assert result["dining"]["varied"] == result["dining"]["popular"][:1]
    assert result["dining"]["composition"] == [
        {"label": "荤菜", "share": 66.7}, {"label": "素菜", "share": 33.3},
        {"label": "主食", "share": 0}, {"label": "其他", "share": 0},
    ]


def test_previous_snapshot_requires_same_scope_type_dates_and_latest_version(group_db):
    content = report_content()
    content["scope_id"] = group_db.classes[0].id
    previous_start, previous_end = START - timedelta(days=7), END - timedelta(days=7)
    for count, scope, sid, end in [(4, "class", content["scope_id"], previous_end),
                                   (99, "grade", content["scope_id"], previous_end),
                                   (98, "class", group_db.classes[1].id, previous_end),
                                   (97, "class", content["scope_id"], previous_end + timedelta(days=1)),
                                   (5, "class", content["scope_id"], previous_end)]:
        db.session.add(Report(report_type=ReportTypeEnum(f"{scope}_weekly"), target_id=str(sid),
                              period_start=previous_start, period_end=end,
                              content={"health_summary": {"participating_students": count},
                                       "meal_projection": {"avg_nutrients": {"protein": 0, "calories": None}}}))
    db.session.commit()
    comparison = group_period_comparison(content)
    assert comparison["participating_students"] == 5
    assert comparison["nutrients"] == {"protein": 0, "calories": None}
    monthly = group_period_comparison({**content, "period_start": "2026-03-01", "period_end": "2026-03-31"}, "class_monthly")
    assert monthly["period_start"] == "2026-02-01" and monthly["period_end"] == "2026-02-28"
    assert monthly["has_report"] is False


def test_generation_and_legacy_preview_preserve_stored_snapshots(group_db):
    student = group_db.students[0]
    sport(student)
    lunch = {"slot": "lunch", "nutrients": {
        "calories": 1160, "protein": 30, "fat": 32, "carbohydrate": 168, "fiber": 10, "calcium": 320,
    }}
    db.session.add(NutritionLog(student_id=student.id, log_date=START, meal_count=1, nutrient_totals={
        "_quality_version": 3, "_meal_slots": ["lunch"], "_meals": [lunch],
    }))
    db.session.commit()
    generated = NutritionService().generate_group_report("grade", group_db.grade.id, START, END)
    assert generated["health_summary"]["sports"]["practice_count"] == 1
    assert generated["health_summary"]["class_count"] == 2
    assert generated["health_summary"]["core"][0]["value"] == 2900
    stored = deepcopy(generated)
    current = Report(report_type=ReportTypeEnum.grade_weekly, target_id=str(group_db.grade.id),
                     period_start=START, period_end=END, content=generated)
    legacy = Report(report_type=ReportTypeEnum.class_weekly, target_id=str(group_db.classes[0].id),
                    period_start=START, period_end=END, content={**report_content(), "health_summary": {}})
    db.session.add_all([current, legacy])
    db.session.commit()
    sport(student)
    with group_db.app.test_request_context():
        serialized = serialize_group_report(current, include_html=True)
        historical = serialize_group_report(legacy, include_html=True)
    assert serialized["content"]["health_summary"]["sports"]["practice_count"] == 1
    assert current.content == stored
    assert "health_summary" in historical["content"] and not historical["content"]["health_summary"]
    assert "此为历史报告" in historical["content"]["presentation_html"]
    assert "period_comparison" not in legacy.content


@pytest.mark.parametrize("scope", ["class", "grade", "campus"])
def test_png_complete_layout_and_macro_font_characters(group_db, scope):
    summary = build_group_health(group_db.students, [averages()] * 3, {}, START, END)
    content = report_content(summary, scope)
    assert len(group_report_view(content)["chart"]["points"]) == 6
    content.update(report_type=f"{scope}_monthly", scope_name='<script>alert("test")</script>', organization_name="其他学校")
    original = deepcopy(content)
    with patch("app.services.report_presentation._font_css", return_value="") as fonts:
        html = render_group_html(content)
    characters = set(fonts.call_args.args[0])
    assert set("次数不代表运动时长或强度规律早餐家校沟通提醒") <= characters
    assert "体质健康月报" in html and "较上月变化" in html
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert 'alt="杭州第四中学校徽"' not in html
    with Image.open(io.BytesIO(render_group_png(content))) as image:
        assert image.width == 1600 and 1500 < image.height < 4500
    assert content == original
