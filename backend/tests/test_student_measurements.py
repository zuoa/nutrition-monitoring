"""BMI callbacks and historical backfills must keep student measurements current."""

import importlib.util
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

from test_external_sports import app, payload, post, START_TIME
from app import db
from app.models import RoleEnum, SportRecord, Student, User
from app.modules.students.api.students import bp
from app.utils.jwt_utils import generate_token


SYNC_URL = "/api/v1/students/sync-measurements"


def bmi_payload(**fields):
    body = payload(score=18.8, **fields)
    body["sport_type"] = 10
    return body


def historical_result(person_id, timestamp, raw_result, sport_type=10):
    record = SportRecord(
        event_key=f"{person_id}-{timestamp}-{sport_type}", version="v1.1", school_id="school-1",
        product_type=1, sport_type=sport_type, mode=2, person_id=person_id,
        start_time=timestamp, score=18.8, raw_result=raw_result,
    )
    db.session.add(record)
    return record


@pytest.fixture
def managed(app):
    app.config.update(
        SECRET_KEY="student-measurements-test-key-32",
        JWT_ALGORITHM="HS256", JWT_ACCESS_TOKEN_EXPIRES=timedelta(hours=1),
    )
    app.register_blueprint(bp, url_prefix="/api/v1/students")
    users = {role: User(name=role.value, role=role) for role in RoleEnum}
    db.session.add_all(users.values())
    db.session.commit()
    auth = {role: {"Authorization": "Bearer " + generate_token(user.id, role.value)} for role, user in users.items()}
    return app.test_client(), auth


def test_bmi_push_updates_student_profile_and_list(managed, app):
    client, auth = managed
    student = Student(student_no="250256", name="测试学生")
    db.session.add(student)
    db.session.commit()
    assert post(app, bmi_payload(person_id="250256", height=171, weight=55, start_time=1776770359000)).status_code == 200
    detail = client.get(f"/api/v1/students/{student.id}", headers=auth[RoleEnum.admin]).json["data"]
    assert (detail["height"], detail["weight"]) == (171, 55)
    items = client.get("/api/v1/students/", headers=auth[RoleEnum.admin]).json["data"]["items"]
    assert (items[0]["height"], items[0]["weight"]) == (171, 55)


def test_out_of_order_pushes_and_corrections_use_latest_event_time(app):
    student = Student(student_no="000123", name="测试学生")
    db.session.add(student)
    db.session.commit()
    assert post(app, bmi_payload(height=171, weight=55)).status_code == 200
    assert post(app, bmi_payload(height=160, weight=50, start_time=START_TIME - 1000)).status_code == 200
    assert (student.height, student.weight) == (171, 55)
    # Corrections to the same newest event must refresh both fields.
    assert post(app, bmi_payload(height=172, weight=56)).status_code == 200
    assert (student.height, student.weight) == (172, 56)
    assert SportRecord.query.count() == 2
    assert post(app, bmi_payload(height=161, weight=51, start_time=START_TIME - 1000)).status_code == 200
    assert (student.height, student.weight) == (172, 56)


@pytest.mark.parametrize("reverse", [False, True])
def test_batch_order_does_not_affect_latest_measurements(app, reverse):
    db.session.add(Student(student_no="000123", name="测试学生"))
    db.session.commit()
    body = bmi_payload(height=170, weight=54)
    body["sport_result"].append({"person_id": "000123", "score": 18.8, "start_time": START_TIME + 1000, "height": 171, "weight": 55})
    if reverse:
        body["sport_result"].reverse()
    assert post(app, body).status_code == 200
    assert (Student.query.one().height, Student.query.one().weight) == (171, 55)


def test_partial_and_zero_values_preserve_latest_valid_value_per_field(app):
    student = Student(student_no="000123", name="测试学生")
    db.session.add(student)
    db.session.commit()
    assert post(app, bmi_payload(height=170, weight=54)).status_code == 200
    assert post(app, bmi_payload(height=171, weight=0, start_time=START_TIME + 1000)).status_code == 200
    assert (student.height, student.weight) == (171, 54)
    assert post(app, bmi_payload(weight=55, start_time=START_TIME + 2000)).status_code == 200
    assert (student.height, student.weight) == (171, 55)
    assert post(app, bmi_payload(start_time=START_TIME + 3000)).status_code == 200
    assert (student.height, student.weight) == (171, 55)


def test_non_bmi_and_unmatched_results_do_not_change_student_profiles(app):
    student = Student(student_no="000123", name="已有学生", height=171, weight=55)
    anonymous = Student(student_no="", name="旧空学号", height=160, weight=50)
    db.session.add_all([student, anonymous])
    db.session.commit()
    assert post(app, payload(height=180, weight=70)).status_code == 200
    assert post(app, bmi_payload(person_id="123", height=180, weight=70)).status_code == 200
    assert post(app, bmi_payload(person_id="", height=180, weight=70)).status_code == 200
    assert (student.height, student.weight) == (171, 55)
    assert (anonymous.height, anonymous.weight) == (160, 50)
    assert Student.query.count() == 2


def test_push_and_profile_update_roll_back_together_on_failure(app):
    student = Student(student_no="000123", name="测试学生", height=170, weight=54)
    db.session.add(student)
    db.session.commit()
    with patch.object(db.session, "commit", side_effect=RuntimeError("internal database details")):
        response = post(app, bmi_payload(height=171, weight=55))
    assert response.status_code == 500
    assert SportRecord.query.count() == 0
    assert (student.height, student.weight) == (170, 54)
    assert post(app, bmi_payload(height=171, weight=55)).status_code == 200
    assert (student.height, student.weight) == (171, 55)


def test_backfill_resolves_pre_roster_pushes_and_is_idempotent(managed, app):
    client, auth = managed
    assert post(app, bmi_payload(height=171, weight=55)).status_code == 200
    assert Student.query.count() == 0
    student = Student(student_no="000123", name="后建档学生")
    # Backfill applies to the complete roster, including students outside the
    # default enrolled list, without altering their status.
    disabled = Student(student_no="000124", name="已停用", is_active=False, is_locally_disabled=True)
    untouched = Student(student_no="000125", name="无体测记录", height=165, weight=52)
    db.session.add_all([student, disabled, untouched])
    historical_result("000124", START_TIME, {"height": 160, "weight": 50})
    historical_result("missing", START_TIME, {"height": 180, "weight": 70})
    db.session.commit()
    response = client.post(SYNC_URL, headers=auth[RoleEnum.admin])
    assert response.status_code == 200
    assert response.json["data"] == {"matched": 2, "updated": 2, "unchanged": 0}
    assert (student.height, student.weight) == (171, 55)
    assert (disabled.height, disabled.weight) == (160, 50)
    assert disabled.is_locally_disabled and not disabled.is_active
    assert (untouched.height, untouched.weight) == (165, 52)
    assert Student.query.count() == 3
    assert client.post(SYNC_URL, headers=auth[RoleEnum.admin]).json["data"] == {"matched": 2, "updated": 0, "unchanged": 2}


@pytest.mark.parametrize("invalid", [None, "175", True, 0, -1, float("inf"), 10 ** 400])
def test_backfill_skips_invalid_historical_metrics_and_non_bmi(managed, invalid):
    client, auth = managed
    student = Student(student_no="000123", name="历史学生", weight=54)
    db.session.add(student)
    historical_result("000123", START_TIME - 1000, {"height": 171})
    historical_result("000123", START_TIME, {"height": invalid, "weight": 55})
    historical_result("000123", START_TIME + 1000, {"height": 190, "weight": 80}, sport_type=1)
    db.session.commit()
    assert client.post(SYNC_URL, headers=auth[RoleEnum.admin]).json["data"]["updated"] == 1
    assert (student.height, student.weight) == (171, 55)


def test_backfill_with_no_valid_data_preserves_existing_values(managed):
    client, auth = managed
    student = Student(student_no="000123", name="历史学生", height=171, weight=55)
    db.session.add(student)
    historical_result("000123", START_TIME, {"height": 0, "weight": None})
    historical_result("000123", START_TIME + 1000, [171, 55])
    db.session.commit()
    assert client.post(SYNC_URL, headers=auth[RoleEnum.admin]).json["data"] == {"matched": 0, "updated": 0, "unchanged": 0}
    assert (student.height, student.weight) == (171, 55)


def test_backfill_failure_rolls_back_all_students(managed):
    client, auth = managed
    first = Student(student_no="000123", name="甲", height=170, weight=54)
    second = Student(student_no="000124", name="乙")
    db.session.add_all([first, second])
    historical_result("000123", START_TIME, {"height": 171, "weight": 55})
    historical_result("000124", START_TIME, {"height": 160, "weight": 50})
    db.session.commit()
    with patch.object(db.session, "commit", side_effect=RuntimeError("internal database details")):
        response = client.post(SYNC_URL, headers=auth[RoleEnum.admin])
    assert response.status_code == 500
    assert "internal" not in response.json["message"]
    assert (first.height, first.weight) == (170, 54)
    assert (second.height, second.weight) == (None, None)


def test_measurement_sync_is_admin_only_and_handles_empty_history(managed):
    client, auth = managed
    assert client.post(SYNC_URL).status_code == 401
    for role, headers in auth.items():
        if role != RoleEnum.admin:
            assert client.post(SYNC_URL, headers=headers).status_code == 403
    assert client.post(SYNC_URL, headers=auth[RoleEnum.admin]).json["data"] == {"matched": 0, "updated": 0, "unchanged": 0}


def test_measurements_migration_preserves_existing_rows_and_downgrades():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/20261103_0032_add_student_measurements.py"
    spec = importlib.util.spec_from_file_location("student_measurements_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    students = sa.Table("students", metadata, sa.Column("id", sa.Integer, primary_key=True))
    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(students.insert().values(id=1))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert connection.exec_driver_sql("SELECT height, weight FROM students WHERE id = 1").one() == (None, None)
            connection.exec_driver_sql("UPDATE students SET height = 171.5, weight = 55.2 WHERE id = 1")
            # Also tolerate schemas already initialized by create_all.
            migration.upgrade()
            assert connection.exec_driver_sql("SELECT height, weight FROM students WHERE id = 1").one() == (171.5, 55.2)
            migration.downgrade()
            migration.downgrade()
        assert {column["name"] for column in sa.inspect(connection).get_columns("students")} == {"id"}
        assert connection.execute(sa.select(students.c.id)).scalar_one() == 1
    engine.dispose()
