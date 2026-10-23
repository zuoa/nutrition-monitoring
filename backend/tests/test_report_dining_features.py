from copy import deepcopy
from datetime import date, datetime, timedelta

import pytest
from flask import Flask
from sqlalchemy import event

from app import db
from app.models import CapturedImage, ConsumptionRecord, Dish, DishRecognition, MatchResult, MatchStatusEnum, NutritionLog, Report, ReportTypeEnum, Student
from app.services.nutrition_service import NutritionService
from app.services.report_dining_features import period_dining_features, summarize_dining_features
from app.services.report_presentation import serialize_personal_reports

DAY = date(2026, 10, 19)
LABELS = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}


@pytest.fixture
def dining_db():
    app = Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI="sqlite:///:memory:", SQLALCHEMY_TRACK_MODIFICATIONS=False, APP_TIMEZONE="Asia/Shanghai")
    db.init_app(app)
    with app.app_context():
        db.create_all()
        student = Student(student_no="S1", name="测试学生")
        db.session.add(student)
        db.session.commit()
        yield student
        db.session.remove()
        db.drop_all()


def test_deduplicated_choices_pairing_and_partial_label_coverage():
    meals = {
        (DAY, "lunch"): {"dishes": {1: {"name": "鱼", "category": "荤菜", "food_groups": ["鱼虾"]},
                                    2: {"name": "青菜", "category": "素菜", "food_groups": ["蔬菜"]}}},
        (DAY, "dinner"): {"dishes": {1: {"name": "鱼", "category": "荤菜", "food_groups": ["鱼虾"]},
                                     3: {"name": "米饭", "category": "主食", "food_groups": []}}},
    }
    original = deepcopy(meals)
    regularity, variety, pairing, groups = summarize_dining_features(meals, LABELS)["items"]
    assert regularity["evidence"]["meal_counts"] == {"lunch": 1, "dinner": 1}
    assert variety["evidence"]["unique_dishes"] == 3
    assert variety["evidence"]["selection_count"] == 4
    assert variety["evidence"]["top_dishes"][0]["count"] == 2
    assert pairing["status"] == "荤素搭配 50%"
    assert pairing["evidence"]["staple_meals"] == 1
    assert groups["evidence"]["group_meal_counts"] == {"鱼虾": 2, "蔬菜": 1}
    assert "3/4" in groups["description"]
    assert meals == original


def test_time_samples_are_per_meal_and_need_five_observed_days():
    meals = {(DAY + timedelta(days=i), "lunch"): {"transaction_times": [720 + i * 5, 800]} for i in range(5)}
    meals[DAY, "unknown"] = {"transaction_times": [400]}
    item = summarize_dining_features(meals, LABELS)["items"][0]
    assert item["evidence"]["times"]["lunch"] == {"samples": 5, "median": "12:10", "middle_half": ["12:05", "12:15"]}
    assert item["status"] == "记录 5 餐"
    del meals[DAY, "lunch"]
    assert summarize_dining_features(meals, LABELS)["items"][0]["evidence"]["times"] == {}


def test_food_shares_use_selection_counts_and_contributions_require_complete_nutrients():
    meals = {
        (DAY, "lunch"): {"dishes": {1: {"name": "鱼", "category": "荤菜", "fat": 10, "protein": 20},
                                    2: {"name": "青菜", "category": "素菜", "fat": 0, "protein": 5}}},
        (DAY, "dinner"): {"dishes": {1: {"name": "鱼", "category": "荤菜", "fat": None, "protein": 20},
                                     3: {"name": "米饭", "category": "主食", "fat": 1, "protein": 5}}},
    }
    result = summarize_dining_features(meals, LABELS)
    assert [group["share"] for group in result["composition"]] == [50, 25, 25, 0]
    assert result["contributors"]["fat"] == {"name": "鱼", "share": 100, "meal_count": 1}
    assert result["contributors"]["protein"] == {"name": "鱼", "share": 80, "meal_count": 2}


def add_recognition(student, *, hour=12, low=False, status=MatchStatusEnum.matched, image=None, dish=None):
    stamp = datetime(2026, 10, 19, hour)
    if dish is None:
        dish = Dish(name=f"菜{Dish.query.count()}", category="荤菜", price=5, food_groups=["禽肉"])
        db.session.add(dish)
    if image is None:
        image = CapturedImage(capture_date=DAY, captured_at=stamp, channel_id="1", image_path="/tmp/unused.jpg")
        db.session.add(image)
    db.session.flush()
    match = MatchResult(student_id=student.id, image_id=image.id, captured_at=stamp, match_date=DAY, status=status)
    db.session.add(match)
    db.session.add(DishRecognition(image_id=image.id, dish_id=dish.id, dish_name_raw=dish.name, confidence=.99, is_low_confidence=low))
    db.session.flush()
    return image, dish


def test_bulk_queries_filter_sources_dates_locations_and_duplicate_images(dining_db):
    student = dining_db
    image, dish = add_recognition(student)
    add_recognition(student, image=image, dish=dish)  # duplicate match and recognition
    add_recognition(student, low=True)
    add_recognition(student, status=MatchStatusEnum.time_matched_only)
    for channel, amount, hour in [("canteen", -5, 12), ("canteen", -2, 13), ("shop", -3, 7), ("canteen", 5, 7)]:
        db.session.add(ConsumptionRecord(student_id=student.id, transaction_time=datetime(2026, 10, 19, hour),
                                         amount=amount, channel_id=channel, transaction_id=f"{channel}-{amount}-{hour}"))
    db.session.commit()
    queries = []
    student_id = student.id

    def count_sql(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)
    event.listen(db.engine, "before_cursor_execute", count_sql)
    try:
        result = period_dining_features([(1, student_id, DAY, DAY), (2, student_id, DAY, DAY), (3, student_id, DAY + timedelta(days=1), DAY + timedelta(days=1))],
                                        {"APP_TIMEZONE": "Asia/Shanghai", "CONSUMPTION_ENABLED_TRANSACTION_LOCATION_IDS": ["canteen"]})
    finally:
        event.remove(db.engine, "before_cursor_execute", count_sql)
    assert len(queries) <= 5
    assert result[1] == result[2]
    assert result[1]["items"][0]["evidence"]["meal_counts"] == {"lunch": 1}
    assert result[1]["items"][1]["evidence"]["selection_count"] == 1
    assert result[3]["items"][0]["status"] == "暂无数据"


def test_new_report_snapshots_and_legacy_enrichment_do_not_use_estimates(dining_db):
    add_recognition(dining_db)
    db.session.commit()
    content = NutritionService().generate_personal_report(dining_db.id, DAY, DAY)
    assert content["dining_features"]["items"][1]["evidence"]["unique_dishes"] == 1
    report = Report(report_type=ReportTypeEnum.personal_weekly, target_id=str(dining_db.id), period_start=DAY, period_end=DAY,
                    content={"meal_statistics": {"records": [{"date": DAY.isoformat(), "slot": "breakfast", "source": "estimated"}]}})
    db.session.add(report)
    db.session.commit()
    original = deepcopy(report.content)
    enriched = serialize_personal_reports([report])[0]["content"]
    assert enriched["dining_features"]["items"][0]["evidence"]["meal_counts"] == {"lunch": 1}
    assert report.content == original and "dining_features" not in report.content
    report.content = content
    db.session.commit()
    dish = Dish.query.first()
    dish.food_groups = ["畜肉"]
    db.session.commit()
    assert serialize_personal_reports([report])[0]["content"]["dining_features"] == content["dining_features"]


def test_observed_nutrition_slots_have_no_invented_time_or_dishes(dining_db):
    db.session.add(NutritionLog(student_id=dining_db.id, log_date=DAY, meal_count=1,
                                nutrient_totals={"_quality_version": 3, "_meal_slots": ["breakfast"]}, dish_ids=[999]))
    db.session.commit()
    items = period_dining_features([(1, dining_db.id, DAY, DAY)])[1]["items"]
    assert items[0]["evidence"] == {"meal_counts": {"breakfast": 1}, "times": {}}
    assert all(item["status"] == "暂无数据" for item in items[1:])


def test_image_assigned_to_multiple_students_is_not_used_for_food_choices(dining_db):
    image, _ = add_recognition(dining_db)
    other = Student(student_no="S2", name="另一学生")
    db.session.add(other)
    db.session.flush()
    db.session.add(MatchResult(student_id=other.id, image_id=image.id, captured_at=image.captured_at,
                               match_date=DAY, status=MatchStatusEnum.confirmed))
    db.session.commit()
    result = period_dining_features([(1, dining_db.id, DAY, DAY)])[1]
    assert result["items"][1]["status"] == "暂无数据"
