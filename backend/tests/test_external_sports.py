"""Contract, persistence and failure-path tests for external sport callbacks."""

import hashlib
import importlib.util
import io
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import time
import threading
from unittest import mock
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from flask import Flask
import pytest
import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db
from app.api.external_sports import bp
from app.models import SportFile, SportRecord, Student


PREFIX = "/api/v1/external/sports"
SECRET = "test-sport-secret"
START_TIME = 1789430400123
JPEG = b"\xff\xd8\xff\xe0" + b"test jpeg content" + b"\xff\xd9"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"test video content"


@pytest.fixture
def app(tmp_path):
    application = Flask(__name__)
    application.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SPORTS_PUSH_CHECK_STRING=SECRET,
        SPORTS_PUSH_FILE_STORAGE_PATH=str(tmp_path / "sports"),
    )
    db.init_app(application)
    application.register_blueprint(bp, url_prefix=PREFIX)
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


def headers(timestamp=None, secret=SECRET):
    timestamp = str(int(time.time() * 1000)) if timestamp is None else str(timestamp)
    return {"timestamp": timestamp, "sign": hashlib.md5((secret + timestamp).encode()).hexdigest()}


def payload(**result_fields):
    return {
        "version": "v1.1", "school_id": "school-1", "product_type": 1, "sport_type": 1, "mode": 1,
        "sport_result": [{"person_id": "000123", "start_time": START_TIME, "score": 120, **result_fields}],
    }


def post(app, body=None, **kwargs):
    return app.test_client().post(PREFIX + "/results", json=payload() if body is None else body, headers=headers(), **kwargs)


def upload(app, content=JPEG, file_id="face-1", file_type="1", **fields):
    return app.test_client().post(PREFIX + "/files", headers=headers(), data={
        "version": "v1.0", "file_id": file_id, "file_type": file_type,
        "file": (io.BytesIO(content), "../../untrusted-name.jpg"), **fields,
    })


def test_result_preserves_metrics_and_resolves_student_number(app):
    db.session.add(Student(student_no="000123", name="测试学生"))
    db.session.commit()
    body = payload(all_time=60, interrupt_count=2, sub_count_list=[{"sub_time": 30, "sub_count": 65}],
                   video_file_id="video-1", face_file_id="face-1", future_metric={"value": 1})
    response = post(app, body)
    assert response.status_code == 200
    assert response.json == {"code": 0, "message": "成功"}
    record = SportRecord.query.one()
    assert record.student.student_no == "000123"
    assert record.raw_result == body["sport_result"][0]
    assert record.start_time == START_TIME
    assert record.all_time == 60
    assert record.score_unit == "个"
    assert record.video_file_id == "video-1"
    assert record.face_file_id == "face-1"


def test_unknown_student_resolves_after_roster_import(app):
    assert post(app).status_code == 200
    assert SportRecord.query.one().student is None
    assert Student.query.count() == 0
    db.session.add(Student(student_no="000123", name="稍后导入"))
    db.session.commit()
    assert SportRecord.query.one().student.name == "稍后导入"


def test_duplicate_and_corrected_results_update_one_event(app):
    body = payload()
    body["sport_result"] *= 2
    assert post(app, body).status_code == 200
    assert post(app, body).status_code == 200
    assert SportRecord.query.count() == 1
    assert post(app, payload(score=125, all_time=60)).status_code == 200
    assert SportRecord.query.one().score == 125
    assert SportRecord.query.one().raw_result["all_time"] == 60


@pytest.mark.parametrize("field,value", [("school_id", "school-2"), ("product_type", 2), ("sport_type", 2), ("mode", 2)])
def test_event_identity_includes_envelope(app, field, value):
    assert post(app).status_code == 200
    body = payload()
    body[field] = value
    assert post(app, body).status_code == 200
    assert SportRecord.query.count() == 2


def test_anonymous_results_preserved_without_merging_different_people(app):
    # Legacy/invalid empty roster numbers must never capture anonymous results.
    db.session.add(Student(student_no="", name="历史空学号"))
    db.session.commit()
    body = payload(person_id="")
    body["sport_result"].append({"person_id": "", "start_time": START_TIME, "score": 121})
    assert post(app, body).status_code == 200
    assert post(app, body).status_code == 200
    assert SportRecord.query.count() == 2
    assert all(record.student is None for record in SportRecord.query.all())


@pytest.mark.parametrize("sport_type,score,unit", [(8, -5.2, "cm"), (10, 20.5, "kg/m²"), (19, 1200, "m"), (24, 42, None)])
def test_negative_scores_bmi_and_future_sports(app, sport_type, score, unit):
    body = payload(score=score)
    body["sport_type"] = sport_type
    assert post(app, body).status_code == 200
    assert SportRecord.query.one().score_unit == unit


@pytest.mark.parametrize("values", [
    {}, {"timestamp": "123", "sign": "a" * 32}, headers(secret="wrong"),
    headers(timestamp=int(time.time() * 1000) - 600000),
    headers(timestamp=int(time.time() * 1000) + 600000),
    {"timestamp": "1789430400123", "sign": "A" * 32},
])
@pytest.mark.parametrize("path", ["results", "files"])
def test_rejects_missing_invalid_or_stale_signature(app, values, path):
    response = app.test_client().post(PREFIX + "/" + path, json=payload(), headers=values)
    assert response.status_code == 401
    assert response.json["code"] == 401
    assert SportRecord.query.count() == 0
    assert SportFile.query.count() == 0


def test_disabled_secret_and_configurable_timestamp_window(app):
    app.config["SPORTS_PUSH_CHECK_STRING"] = ""
    assert post(app).status_code == 503
    app.config.update(SPORTS_PUSH_CHECK_STRING=SECRET, SPORTS_PUSH_TIMESTAMP_TOLERANCE_SECONDS=0)
    response = app.test_client().post(PREFIX + "/results", json=payload(), headers=headers(START_TIME))
    assert response.status_code == 200


def test_school_allowlist(app):
    app.config["SPORTS_PUSH_ALLOWED_SCHOOL_IDS"] = ["another-school"]
    assert post(app).status_code == 403
    assert SportRecord.query.count() == 0


@pytest.mark.parametrize("field,value", [
    ("version", "v1.0"), ("school_id", ""), ("school_id", 1),
    ("product_type", True), ("product_type", 4), ("sport_type", 0),
    ("sport_type", 2147483648), ("mode", 3), ("mode", "1"),
    ("sport_result", []), ("sport_result", {}), ("sport_result", [None]),
])
def test_invalid_envelopes(app, field, value):
    body = payload()
    body[field] = value
    assert post(app, body).status_code == 400
    assert SportRecord.query.count() == 0


@pytest.mark.parametrize("fields", [
    {"person_id": 123}, {"person_id": " 000123"}, {"person_id": "x" * 65}, {"person_id": "\x00"},
    {"start_time": 1789430400}, {"start_time": START_TIME + 0.5},
    {"score": None}, {"score": "120"}, {"score": True}, {"score": float("nan")},
    {"score": float("inf")}, {"score": 10 ** 400}, {"all_time": -1}, {"interrupt_count": 1.5},
    {"sub_count_list": {}}, {"sub_count_list": [None]}, {"sub_count_list": [{"sub_time": 30}]},
    {"sub_count_list": [{"sub_time": 30, "sub_count": -1}]}, {"face_file_id": 123},
    {"future_metric": float("nan")},
])
def test_invalid_results_reject_entire_batch(app, fields):
    body = payload()
    body["sport_result"].append({**body["sport_result"][0], **fields})
    assert post(app, body).status_code == 400
    assert SportRecord.query.count() == 0


@pytest.mark.parametrize("field", ["person_id", "start_time", "score"])
def test_missing_required_result_fields(app, field):
    body = payload()
    del body["sport_result"][0][field]
    assert post(app, body).status_code == 400


def test_invalid_json_and_limits_have_protocol_responses(app):
    client = app.test_client()
    for data in ("{", "null", "[]"):
        response = client.post(PREFIX + "/results", data=data, content_type="application/json", headers=headers())
        assert response.status_code == 400
        assert response.json["code"] == 400
    response = client.post(PREFIX + "/results", data="{}", content_type="text/plain", headers=headers())
    assert response.status_code == 415
    app.config["SPORTS_PUSH_MAX_BATCH_SIZE"] = 1
    body = payload()
    body["sport_result"] *= 2
    assert post(app, body).status_code == 400
    app.config["SPORTS_PUSH_MAX_REQUEST_BYTES"] = 10
    assert post(app).status_code == 413


def test_database_failure_rolls_back_whole_batch_and_retry_succeeds(app):
    body = payload()
    body["sport_result"].append({"person_id": "000124", "start_time": START_TIME, "score": 60})
    execute = db.session.execute
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("internal database connection details")
        return execute(*args, **kwargs)

    with mock.patch.object(db.session, "execute", side_effect=fail_second):
        response = post(app, body)
    assert response.status_code == 500
    assert "internal" not in response.json["message"]
    assert SportRecord.query.count() == 0
    assert post(app, body).status_code == 200
    assert SportRecord.query.count() == 2


@pytest.mark.parametrize("content,file_type", [(JPEG, "1"), (MP4, "2")])
def test_media_upload_retry_and_safe_storage(app, content, file_type):
    assert upload(app, content, file_id="../../vendor-id", file_type=file_type).status_code == 200
    assert upload(app, content, file_id="../../vendor-id", file_type=file_type).status_code == 200
    stored = SportFile.query.one()
    path = Path(stored.storage_path)
    assert path.parent == Path(app.config["SPORTS_PUSH_FILE_STORAGE_PATH"])
    assert path.read_bytes() == content
    assert stored.size_bytes == len(content)
    assert not list(path.parent.glob(".upload-*"))
    assert len(list(path.parent.iterdir())) == 1


def test_media_and_results_can_arrive_in_either_order(app):
    assert upload(app).status_code == 200
    assert post(app, payload(face_file_id="face-1", video_file_id="video-1")).status_code == 200
    assert upload(app, MP4, file_id="video-1", file_type="2").status_code == 200
    record = SportRecord.query.one()
    assert db.session.get(SportFile, record.face_file_id).file_type == 1
    assert db.session.get(SportFile, record.video_file_id).file_type == 2


def test_same_file_id_cannot_replace_existing_content(app):
    assert upload(app).status_code == 200
    assert upload(app, JPEG + b"different").status_code == 409
    stored = SportFile.query.one()
    assert Path(stored.storage_path).read_bytes() == JPEG
    assert len(list(Path(stored.storage_path).parent.iterdir())) == 1


@pytest.mark.parametrize("content,file_type,fields", [
    (b"", "1", {}), (b"not a jpeg", "1", {}), (JPEG, "2", {}), (MP4, "1", {}),
    (JPEG, "3", {}), (JPEG, "1", {"version": "v1.1"}), (JPEG, "1", {"file_id": ""}),
])
def test_invalid_media(app, content, file_type, fields):
    response = upload(app, content, file_type=file_type, **fields)
    assert response.status_code == 400
    assert SportFile.query.count() == 0
    assert not list(Path(app.config["SPORTS_PUSH_FILE_STORAGE_PATH"]).glob("*"))


def test_missing_file_wrong_content_type_and_file_limit(app):
    client = app.test_client()
    response = client.post(PREFIX + "/files", json={}, headers=headers())
    assert response.status_code == 415
    response = client.post(PREFIX + "/files", content_type="multipart/form-data", headers=headers(),
                           data={"version": "v1.0", "file_id": "missing", "file_type": "1"})
    assert response.status_code == 400
    app.config["SPORTS_PUSH_MAX_FILE_BYTES"] = 5
    assert upload(app).status_code == 413
    assert upload(app, JPEG + b"x" * 70000).status_code == 413
    assert SportFile.query.count() == 0
    assert not list(Path(app.config["SPORTS_PUSH_FILE_STORAGE_PATH"]).glob("*"))


def test_file_disk_and_commit_failures_return_retryable_error(app):
    with mock.patch("app.services.sport_ingestion_service.os.link", side_effect=OSError("disk failure")):
        assert upload(app).status_code == 500
    assert SportFile.query.count() == 0
    assert not list(Path(app.config["SPORTS_PUSH_FILE_STORAGE_PATH"]).glob("*"))
    with mock.patch.object(db.session, "commit", side_effect=RuntimeError("database failure")):
        assert upload(app).status_code == 500
    assert SportFile.query.count() == 0
    assert upload(app).status_code == 200
    assert SportFile.query.count() == 1


def test_migration_upgrade_downgrade_and_model_parity():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/20260915_0027_add_sport_ingestion.py"
    spec = importlib.util.spec_from_file_location("sport_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
            inspector = sa.inspect(connection)
            for model in (SportRecord, SportFile):
                table = model.__table__
                columns = {column["name"]: column for column in inspector.get_columns(table.name)}
                assert set(columns) == set(table.columns.keys())
                for column in table.columns:
                    assert columns[column.name]["nullable"] == column.nullable
                    assert str(columns[column.name]["type"]) == str(column.type)
                assert {index["name"] for index in inspector.get_indexes(table.name)} == {index.name for index in table.indexes}
            migration.downgrade()
            assert not sa.inspect(connection).has_table("sport_records")
            assert not sa.inspect(connection).has_table("sport_files")
            migration.upgrade()


@pytest.fixture
def postgres_app(tmp_path):
    """Opt-in PG coverage always uses a fresh schema, removed after the test."""
    database_url = os.environ.get("SPORTS_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set SPORTS_TEST_DATABASE_URL for PostgreSQL integration tests")
    schema = "sports_test_" + uuid4().hex
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
    application = Flask(__name__)
    application.config.update(
        TESTING=True, SQLALCHEMY_DATABASE_URI=database_url,
        SQLALCHEMY_ENGINE_OPTIONS={"connect_args": {"options": f"-csearch_path={schema} -clock_timeout=5000"}},
        SQLALCHEMY_TRACK_MODIFICATIONS=False, SPORTS_PUSH_CHECK_STRING=SECRET,
        SPORTS_PUSH_FILE_STORAGE_PATH=str(tmp_path / "sports"),
    )
    db.init_app(application)
    application.register_blueprint(bp, url_prefix=PREFIX)
    try:
        with application.app_context():
            SportRecord.__table__.create(db.engine)
            SportFile.__table__.create(db.engine)
        yield application
    finally:
        with application.app_context():
            db.session.remove()
            db.engine.dispose()
        with engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


@pytest.mark.parametrize("kind", ["results", "files"])
def test_postgres_concurrent_retries(postgres_app, kind):
    barrier = threading.Barrier(4)

    def push(index):
        barrier.wait(timeout=10)
        if kind == "files":
            return upload(postgres_app).status_code
        body = payload()
        body["sport_result"].append({"person_id": "000124", "start_time": START_TIME, "score": 60})
        if index % 2:
            body["sport_result"].reverse()
        return post(postgres_app, body).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(push, range(4))) == [200] * 4
    with postgres_app.app_context():
        if kind == "results":
            assert SportRecord.query.count() == 2
        else:
            assert SportFile.query.count() == 1
            assert Path(SportFile.query.one().storage_path).read_bytes() == JPEG


def test_postgres_transaction_rollback(postgres_app):
    with postgres_app.app_context():
        test_database_failure_rolls_back_whole_batch_and_retry_succeeds(postgres_app)
