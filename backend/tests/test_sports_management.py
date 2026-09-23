"""Sports settings must affect callbacks and browsing must stay admin-only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from test_external_sports import app, headers, payload, PREFIX
from app import db
from app.api.sports import bp
from app.models import RoleEnum, Student, User
from app.services.runtime_config import get_effective_config, persist_runtime_overrides
from app.utils.jwt_utils import generate_token


@pytest.fixture
def managed(app, tmp_path):
    app.config.update(SECRET_KEY="test-key-for-sports-management-32", JWT_ALGORITHM="HS256",
                      JWT_ACCESS_TOKEN_EXPIRES=timedelta(hours=1),
                      LOCAL_RUNTIME_CONFIG_PATH=str(tmp_path / "runtime.json"))
    app.register_blueprint(bp, url_prefix="/api/v1/sports")
    admin = User(name="管理员", role=RoleEnum.admin)
    teacher = User(name="教师", role=RoleEnum.teacher)
    db.session.add_all([admin, teacher])
    db.session.commit()
    return app.test_client(), {"Authorization": "Bearer " + generate_token(admin.id, "admin")}, {
        "Authorization": "Bearer " + generate_token(teacher.id, "teacher")}


def test_config_persists_and_changes_signature_and_school(managed, app):
    client, auth, _ = managed
    # Secrets are stored trimmed so a pasted trailing space cannot break signing.
    response = client.put("/api/v1/sports/config", headers=auth,
                          json={"school_ids": [" new-school "], "check_string": "  new-secret  "})
    assert response.status_code == 200
    config = client.get("/api/v1/sports/config", headers=auth).json["data"]
    assert config == {"school_ids": ["new-school"], "check_string_configured": True}
    # Base app config remains unchanged, as it would be in another worker.
    assert app.config["SPORTS_PUSH_CHECK_STRING"] != "new-secret"
    assert client.post(PREFIX + "/results", headers=headers(), json=payload()).status_code == 401
    assert client.post(PREFIX + "/results", headers=headers(secret="new-secret"), json=payload()).status_code == 403
    body = payload()
    body["school_id"] = "new-school"
    assert client.post(PREFIX + "/results", headers=headers(secret="new-secret"), json=body).status_code == 200
    # An empty list restores unrestricted schools.
    assert client.put("/api/v1/sports/config", headers=auth, json={"school_ids": []}).status_code == 200
    assert client.post(PREFIX + "/results", headers=headers(secret="new-secret"), json=payload()).status_code == 200


def test_clearing_secret_restores_env_configuration(managed, app):
    client, auth, _ = managed
    app.config["SPORTS_PUSH_CHECK_STRING"] = ""
    assert client.put("/api/v1/sports/config", headers=auth, json={"check_string": "temp-secret"}).status_code == 200
    assert client.post(PREFIX + "/results", headers=headers(secret="temp-secret"), json=payload()).status_code == 200
    # Empty clears the override: intake falls back to the env value (unset → 503).
    assert client.put("/api/v1/sports/config", headers=auth, json={"check_string": ""}).status_code == 200
    assert client.get("/api/v1/sports/config", headers=auth).json["data"]["check_string_configured"] is False
    assert client.post(PREFIX + "/results", headers=headers(secret="temp-secret"), json=payload()).status_code == 503


def test_string_allowlist_entries_are_normalized(managed, app):
    client, auth, _ = managed
    # A hand-edited comma string must behave like the list form, never as a
    # substring match.
    persist_runtime_overrides(app.config, {"SPORTS_PUSH_ALLOWED_SCHOOL_IDS": " school-1 , school-2 "})
    assert client.get("/api/v1/sports/config", headers=auth).json["data"]["school_ids"] == ["school-1", "school-2"]
    body = payload()
    body["school_id"] = "school-1"
    assert client.post(PREFIX + "/results", headers=headers(), json=body).status_code == 200
    unlisted = payload()
    unlisted["school_id"] = "another-school"
    assert client.post(PREFIX + "/results", headers=headers(), json=unlisted).status_code == 403
    substring = payload()
    substring["school_id"] = "hool"
    assert client.post(PREFIX + "/results", headers=headers(), json=substring).status_code == 403


def test_concurrent_persists_do_not_lose_keys(managed, app):
    _, auth, _ = managed
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda index: persist_runtime_overrides(app.config, {f"KEY_{index}": index}), range(64)))
    effective = get_effective_config(app.config)
    assert all(effective.get(f"KEY_{index}") == index for index in range(64))


@pytest.mark.parametrize("body", [
    {"school_ids": "school-1"}, {"school_ids": [12]}, {"school_ids": ["x" * 129]},
    {"check_string": None}, {"check_string": 12}, {"check_string": "x" * 1025}, [], {},
])
def test_invalid_config(managed, body):
    client, auth, _ = managed
    assert client.put("/api/v1/sports/config", headers=auth, json=body).status_code == 400


def test_browsing_filters_and_metrics(managed):
    client, auth, _ = managed
    db.session.add(Student(student_no="000123", name="测试学生"))
    db.session.commit()
    assert client.post(PREFIX + "/results", headers=headers(), json=payload(interrupt_count=2)).status_code == 200
    body = payload(person_id="", score=0)
    body["sport_type"] = 99
    assert client.post(PREFIX + "/results", headers=headers(), json=body).status_code == 200
    meta = client.get("/api/v1/sports/meta", headers=auth).json["data"]
    assert {"id": 1, "name": "跳绳"} in meta["sports"]
    assert {"id": 3, "name": "AI操场吧"} in meta["products"]
    data = client.get("/api/v1/sports/records?page_size=1", headers=auth).json["data"]
    assert data["total"] == 2 and data["total_pages"] == 2
    item = data["items"][0]
    assert item["person_id"] == ""
    assert item["score"] == 0 and item["score_unit"] is None
    assert item["sport_name"] is None  # Unknown sport types stay nameless.
    assert "raw_result" not in item  # The list stays light; the detail serves the vendor payload.
    detail = client.get(f"/api/v1/sports/records/{item['id']}", headers=auth).json["data"]
    assert detail["raw_result"]["score"] == 0
    assert detail["received_at"].endswith("+00:00")
    assert client.get("/api/v1/sports/records/999999", headers=auth).status_code == 404
    data = client.get("/api/v1/sports/records?student=测试&sport_type=1&mode=1&school_id=school-1&date_from=2026-09-15&date_to=2026-09-15", headers=auth).json["data"]
    assert data["total"] == 1
    item = data["items"][0]
    assert item["student_name"] == "测试学生"
    assert item["sport_name"] == "跳绳"
    detail = client.get(f"/api/v1/sports/records/{item['id']}", headers=auth).json["data"]
    assert detail["raw_result"]["interrupt_count"] == 2
    for query in ("student=missing", "school_id=missing", "mode=2", "date_to=2026-09-14", "date_from=2026-09-16"):
        assert client.get("/api/v1/sports/records?" + query, headers=auth).json["data"]["total"] == 0
    for query in ("sport_type=abc", "mode=3", "date_from=bad", "date_from=2026-09-16&date_to=2026-09-15"):
        assert client.get("/api/v1/sports/records?" + query, headers=auth).status_code == 400


def test_admin_only(managed):
    client, _, teacher = managed
    for path in ("config", "records", "meta"):
        assert client.get("/api/v1/sports/" + path).status_code == 401
        assert client.get("/api/v1/sports/" + path, headers=teacher).status_code == 403
    assert client.put("/api/v1/sports/config", headers=teacher, json={"school_ids": ["other"]}).status_code == 403
