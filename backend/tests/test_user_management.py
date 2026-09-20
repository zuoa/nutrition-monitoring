"""Account lifecycle and canteen feature-boundary regressions."""

from datetime import date, timedelta
from pathlib import Path
import sys
from unittest.mock import Mock, patch

from flask import Flask
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db
from app.api.admin import bp as admin_bp
from app.api.analysis import bp as analysis_bp
from app.api.auth import bp as auth_bp
from app.api.consumption import bp as consumption_bp
from app.api.dishes import bp as dishes_bp
from app.api.menus import bp as menus_bp
from app.api.reports import bp as reports_bp
from app.models import Department, RoleEnum, TaskLog, User
from app.utils.jwt_utils import generate_token


@pytest.fixture
def app(tmp_path):
    app = Flask(__name__)
    app.config.update(
        TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:", SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SECRET_KEY="account-tests", JWT_ALGORITHM="HS256", JWT_ACCESS_TOKEN_EXPIRES=timedelta(hours=1),
        LOCAL_RUNTIME_CONFIG_PATH=str(tmp_path / "runtime.json"),
    )
    db.init_app(app)
    for blueprint, prefix in ((admin_bp, "v1/admin"), (analysis_bp, "v1/analysis"), (auth_bp, "auth"),
                              (consumption_bp, "v1/consumption"), (dishes_bp, "v1/dishes"), (menus_bp, "v1/menus"), (reports_bp, "v1/reports")):
        app.register_blueprint(blueprint, url_prefix="/api/" + prefix)
    with app.app_context():
        db.create_all()
        db.session.add_all([
            User(id=1, name="管理员", username="admin-test", role=RoleEnum.admin),
            User(id=2, name="食堂老师", username="canteen-test", role=RoleEnum.canteen_manager),
            Department(name="食堂", dingtalk_dept_id="dept-canteen", is_active=True),
        ])
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


def headers(user_id=1, role="admin"):
    return {"Authorization": "Bearer " + generate_token(user_id, role)}


def account(**overrides):
    return {"name": "新食堂管理员", "username": "kitchen-new", "password": "New-pass123", "role": "canteen_manager", **overrides}


def test_create_edit_password_login_disable_and_restore(app):
    client = app.test_client()
    response = client.post("/api/v1/admin/users", json=account(dept_id="dept-canteen"), headers=headers())
    assert response.status_code == 201
    data = response.json["data"]
    assert data["username"] == "kitchen-new"
    assert data["dept_name"] == "食堂"
    assert data["has_password"] is True
    assert "password" not in data and "password_hash" not in data
    user = db.session.get(User, data["id"])
    assert user.password_hash != "New-pass123" and user.check_password("New-pass123")
    token = headers(user.id, "canteen_manager")
    assert client.get("/api/auth/me", headers=token).status_code == 200
    response = client.put(f"/api/v1/admin/users/{user.id}", json={"username": "kitchen-edited", "name": "新姓名", "password": "Reset-pass123"}, headers=headers())
    assert response.status_code == 200
    assert user.check_password("Reset-pass123") and not user.check_password("New-pass123")
    fake_redis = Mock()
    fake_redis.get.return_value = "ABCD"
    with patch("app.api.auth.get_redis_client", return_value=fake_redis):
        response = client.post("/api/auth/login", json={"username": "kitchen-edited", "password": "Reset-pass123", "captcha_id": "test", "captcha_code": "abcd"})
    assert response.status_code == 200
    assert response.json["data"]["user"]["username"] == "kitchen-edited"
    assert client.delete(f"/api/v1/admin/users/{user.id}", headers=headers()).status_code == 200
    assert client.get("/api/auth/me", headers=token).status_code == 401
    assert client.put(f"/api/v1/admin/users/{user.id}", json={"is_active": True}, headers=headers()).status_code == 200
    assert client.get("/api/auth/me", headers=token).status_code == 200


@pytest.mark.parametrize("data", [[], {"name": ""}, account(username=""), account(username="has spaces"), account(username="a" * 65),
                                  account(password="short"), account(role="unknown"), account(is_active="false"), account(dept_id="missing")])
def test_invalid_account_data_has_no_partial_changes(app, data):
    assert app.test_client().post("/api/v1/admin/users", json=data, headers=headers()).status_code == 400
    assert User.query.count() == 2


def test_duplicate_login_and_rejected_update_preserve_existing_account(app):
    client = app.test_client()
    assert client.post("/api/v1/admin/users", json=account(username="admin-test"), headers=headers()).status_code == 400
    assert client.put("/api/v1/admin/users/2", json={"name": "must not change", "username": "admin-test"}, headers=headers()).status_code == 400
    assert db.session.get(User, 2).name == "食堂老师"
    assert client.put("/api/v1/admin/users/2", json={"role": "teacher", "is_active": "false"}, headers=headers()).status_code == 400
    assert db.session.get(User, 2).role == RoleEnum.canteen_manager


def test_existing_password_survives_profile_only_edit(app):
    user = db.session.get(User, 2)
    user.set_password("Original-pass123")
    db.session.commit()
    old_hash = user.password_hash
    assert app.test_client().put("/api/v1/admin/users/2", json={"name": "改名"}, headers=headers()).status_code == 200
    assert user.password_hash == old_hash


def test_synced_user_can_get_login_credentials_without_changing_identity(app):
    user = User(name="同步用户", dingtalk_user_id="external-123", role=RoleEnum.teacher)
    db.session.add(user)
    db.session.commit()
    client = app.test_client()
    url = f"/api/v1/admin/users/{user.id}"
    assert client.put(url, json={"username": "teacher-login"}, headers=headers()).status_code == 400
    assert client.put(url, json={"username": "teacher-login", "password": "Teacher-pass123"}, headers=headers()).status_code == 200
    assert user.dingtalk_user_id == "external-123"
    assert user.check_password("Teacher-pass123")


@pytest.mark.parametrize("data", [{"role": "canteen_manager"}, {"is_active": False}])
def test_admin_cannot_remove_own_access(app, data):
    assert app.test_client().put("/api/v1/admin/users/1", json=data, headers=headers()).status_code == 400
    assert db.session.get(User, 1).role == RoleEnum.admin
    assert db.session.get(User, 1).is_active


def test_search_login_name_and_paginate_inactive_accounts(app):
    client = app.test_client()
    db.session.add_all([User(name="同名用户", username=f"login-{index:02}", role=RoleEnum.teacher, is_active=False) for index in range(23)])
    db.session.commit()
    response = client.get("/api/v1/admin/users?search=login-&active_only=false&status=inactive&page=2&page_size=20", headers=headers())
    data = response.json["data"]
    assert data["total"] == 23 and len(data["items"]) == 3
    assert {user["username"] for user in data["items"]} == {"login-20", "login-21", "login-22"}


@pytest.mark.parametrize("method,path", [
    ("get", "/api/v1/admin/users"), ("post", "/api/v1/admin/users"), ("put", "/api/v1/admin/users/1"), ("delete", "/api/v1/admin/users/1"),
    ("get", "/api/v1/admin/config"), ("get", "/api/v1/admin/students"), ("get", "/api/v1/analysis/tasks"),
    ("get", "/api/v1/analysis/tasks/1"), ("get", "/api/v1/analysis/images"), ("get", "/api/v1/analysis/summary"),
    ("get", "/api/v1/reports/alerts"), ("get", "/api/v1/consumption/records"),
    ("post", "/api/v1/dishes/rebuild-sample-embeddings"), ("post", "/api/v1/dishes/confusion-analysis"),
])
def test_canteen_cannot_access_other_features_even_with_forged_role_claim(app, method, path):
    response = getattr(app.test_client(), method)(path, json={}, headers=headers(2, "admin"))
    assert response.status_code == 403


def test_canteen_can_manage_dishes_menus_and_read_only_dish_tasks(app):
    client = app.test_client()
    auth = headers(2, "canteen_manager")
    assert client.get("/api/v1/dishes/metadata", headers=auth).status_code == 200
    response = client.post("/api/v1/dishes/", json={"name": "测试菜品", "category": "素菜", "price": 3.5}, headers=auth)
    assert response.status_code == 201
    dish_id = response.json["data"]["id"]
    assert client.put(f"/api/v1/dishes/{dish_id}", json={"price": 4}, headers=auth).status_code == 200
    day = date.today().isoformat()
    assert client.put(f"/api/v1/menus/{day}", json={"meal_dish_ids": {"lunch": [dish_id]}}, headers=auth).status_code == 200
    assert client.get(f"/api/v1/menus/{day}", headers=auth).json["data"]["meal_dish_ids"]["lunch"] == [dish_id]
    task = TaskLog(task_type="dish_zip_import", status="success")
    other = TaskLog(task_type="ai_recognition", status="success")
    db.session.add_all([task, other])
    db.session.commit()
    assert client.get(f"/api/v1/dishes/tasks/{task.id}", headers=auth).status_code == 200
    assert client.get(f"/api/v1/dishes/tasks/{other.id}", headers=auth).status_code == 404
    assert client.delete(f"/api/v1/dishes/{dish_id}", headers=auth).status_code == 200
