from collections import defaultdict
from datetime import date, datetime, time, timedelta

from app.services.report_meal_completion import complete_attendance, estimate_nutrients, nutrient_donor_index

DAY = date(2026, 10, 19)
SLOTS = {"breakfast", "lunch", "dinner"}


def evidence(*, skip_breakfast=False, missing_slot="breakfast", peer_outage=True):
    events = defaultdict(set)
    classes = {sid: 1 for sid in range(1, 8)}
    for offset in range(1, 29):
        day = DAY - timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        for sid in classes:
            for slot in SLOTS:
                if sid == 1 and skip_breakfast and slot == "breakfast":
                    continue
                events[sid, day, slot].add("consumption")
    for sid in classes:
        for slot in SLOTS:
            if slot == missing_slot and (sid == 1 or peer_outage):
                continue
            events[sid, DAY, slot].add("consumption")
    return events, classes


def complete(events, classes, start=DAY, end=DAY):
    return complete_attendance(events, classes, [1], start, end, SLOTS)[1]


def test_independent_sources_are_merged_and_each_meal_is_counted_once():
    events = {(1, DAY, "lunch"): {"consumption", "recognition", "nutrition"}, (1, DAY, "breakfast"): {"nutrition"}}
    result = complete(events, {1: 1})
    assert result["meal_count"] == result["observed_count"] == 2
    assert result["breakfast_count"] == 1 and result["estimated_count"] == 0


def test_personal_habit_and_class_collection_outage_recover_one_meal():
    events, classes = evidence()
    result = complete(events, classes)
    assert result["meal_count"] == 3 and result["breakfast_count"] == 1
    assert result["observed_count"] == 2 and result["estimated_count"] == 1
    restored = next(row for row in result["records"] if row["source"] == "estimated")
    assert restored["slot"] == "breakfast" and restored["confidence"] >= .8


def test_normal_peer_records_do_not_turn_a_personal_skip_into_a_meal():
    events, classes = evidence(peer_outage=False)
    result = complete(events, classes)
    assert result["meal_count"] == 2 and result["estimated_count"] == 0


def test_regular_breakfast_omission_is_preserved_even_during_a_class_outage():
    events, classes = evidence(skip_breakfast=True)
    result = complete(events, classes)
    assert result["meal_count"] == 2 and result["breakfast_count"] == 0
    assert result["estimated_count"] == 0


def test_regular_breakfast_omission_with_healthy_peers_is_identified_separately():
    events, classes = evidence(skip_breakfast=True, peer_outage=False)
    result = complete(events, classes)
    assert len(result["likely_skipped"]) == 1
    assert result["likely_skipped"][0]["slot"] == "breakfast"
    assert result["meal_count"] == 2


def test_entirely_absent_day_or_unknown_activity_is_not_filled():
    events, classes = evidence()
    events = {key: value for key, value in events.items() if key[0] != 1 or key[1] != DAY}
    events[1, DAY, "unknown"] = {"consumption"}
    result = complete(events, classes)
    assert result["meal_count"] == result["estimated_count"] == 0
    assert result["unknown_count"] == 1


def test_weekday_habits_are_not_applied_to_weekends_or_without_enough_history():
    events, classes = evidence()
    saturday = DAY + timedelta(days=5)
    events[1, saturday, "lunch"] = {"consumption"}
    assert complete(events, classes, saturday, saturday)["estimated_count"] == 0
    recent = {key: value for key, value in events.items() if key[1] >= DAY - timedelta(days=3)}
    assert complete(recent, classes)["estimated_count"] == 0


def test_future_records_and_estimated_meals_do_not_train_the_history():
    events, classes = evidence()
    events = {key: value for key, value in events.items() if key[1] >= DAY}
    for offset in range(1, 30):
        for sid in classes:
            for slot in SLOTS:
                events[sid, DAY + timedelta(days=offset), slot] = {"consumption"}
    assert complete(events, classes)["estimated_count"] == 0


def test_other_classes_and_custom_snack_slots_do_not_invent_meals():
    events, classes = evidence()
    classes = {sid: sid for sid in classes}
    assert complete(events, classes)["estimated_count"] == 0
    events, classes = evidence(missing_slot="dinner")
    result = complete_attendance(events, classes, [1], DAY, DAY, {"lunch", "late_night"})[1]
    assert result["estimated_count"] == 0


def test_current_unfinished_meals_and_future_days_are_not_imputed():
    events, classes = evidence(missing_slot="dinner")
    result = complete_attendance(events, classes, [1], DAY, DAY, SLOTS,
                                 as_of=datetime.combine(DAY, time(12)), slot_ends={"dinner": time(20)})[1]
    assert result["estimated_count"] == 0
    result = complete_attendance(events, classes, [1], DAY, DAY, SLOTS,
                                 as_of=datetime.combine(DAY - timedelta(days=1), time(23)))[1]
    assert result["estimated_count"] == 0


def test_explicit_menu_without_breakfast_does_not_become_a_collection_outage():
    events, classes = evidence()
    result = complete_attendance(events, classes, [1], DAY, DAY, SLOTS, serving_slots={DAY: {"lunch", "dinner"}})[1]
    assert result["meal_count"] == 2 and result["estimated_count"] == 0


def test_nutrient_completion_prefers_personal_slot_history_and_ignores_future_outliers():
    meals = {(1, DAY - timedelta(days=i), "lunch"): {"protein": value, "calories": 400}
             for i, value in ((1, 10), (4, 12), (5, 11), (6, 999))}
    meals[1, DAY + timedelta(days=1), "lunch"] = {"protein": 9999}
    meals[1, DAY - timedelta(days=1), "breakfast"] = {"protein": 1000}
    values, sources = estimate_nutrients(1, DAY, "lunch", nutrient_donor_index(meals, {1: 1}), {1: 1})
    assert values["protein"] == 12
    assert sources["protein"] == "student_history"


def test_nutrient_completion_uses_same_class_same_meal_and_keeps_real_zero():
    classes = {1: 1, 2: 1, 3: 1, 4: 1, 5: 2}
    meals = {(sid, DAY, "lunch"): {"protein": 0, "calories": 400} for sid in (2, 3, 4)}
    meals[5, DAY, "lunch"] = {"protein": 9999}
    values, sources = estimate_nutrients(1, DAY, "lunch", nutrient_donor_index(meals, classes), classes)
    assert values["protein"] == 0 and values["calories"] == 400
    assert sources["protein"] == "class_peers"
    values, sources = estimate_nutrients(1, DAY, "dinner", nutrient_donor_index(meals, classes), classes)
    assert values == sources == {}


def test_nutrient_completion_requires_multiple_independent_peers_or_personal_days():
    classes = {1: 1, 2: 1}
    meals = {(2, DAY - timedelta(days=i), "lunch"): {"protein": 10} for i in range(1, 7)}
    meals[1, DAY - timedelta(days=1), "lunch"] = {"protein": 20}
    values, _ = estimate_nutrients(1, DAY, "lunch", nutrient_donor_index(meals, classes), classes)
    assert values == {}
