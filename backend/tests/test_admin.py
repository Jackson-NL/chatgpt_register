from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api import admin
from app.config import settings


def _test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(admin.router, prefix="/api/admin")
    return app


@pytest.fixture(autouse=True)
def enable_admin_for_tests(monkeypatch):
    monkeypatch.setattr(settings, "admin_auth_enabled", True)


def test_admin_status_reports_feature_state(monkeypatch):
    monkeypatch.setattr(settings, "admin_auth_enabled", False)

    with TestClient(_test_app()) as client:
        assert client.get("/api/admin/status").json() == {"enabled": False}
        assert client.post("/api/admin/login", json={"key": "anything"}).status_code == 404


def test_admin_requires_configured_key(monkeypatch):
    monkeypatch.setattr(settings, "admin_access_key", "")
    admin._FAILED_ATTEMPTS.clear()

    with TestClient(_test_app()) as client:
        assert client.post("/api/admin/login", json={"key": "anything"}).status_code == 503
        assert client.get("/api/admin/session").status_code == 503


def test_admin_login_issues_cookie_and_allows_protected_requests(monkeypatch):
    monkeypatch.setattr(settings, "admin_access_key", "test-admin-key")
    monkeypatch.setattr(settings, "admin_session_ttl_seconds", 120)
    admin._FAILED_ATTEMPTS.clear()

    with TestClient(_test_app()) as client:
        response = client.post("/api/admin/login", json={"key": "test-admin-key"})

        assert response.status_code == 200
        assert response.json()["authenticated"] is True
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "SameSite=lax" in response.headers["set-cookie"]
        assert client.get("/api/admin/session").status_code == 200
        overview = client.get("/api/admin/overview").json()
        assert overview["authenticated"] is True
        assert "账号运维" in overview["capabilities"]


def test_admin_rejects_wrong_key_and_rate_limits_failed_logins(monkeypatch):
    monkeypatch.setattr(settings, "admin_access_key", "test-admin-key")
    monkeypatch.setattr(settings, "admin_login_max_attempts", 2)
    admin._FAILED_ATTEMPTS.clear()

    with TestClient(_test_app()) as client:
        assert client.post("/api/admin/login", json={"key": "wrong"}).status_code == 401
        assert client.post("/api/admin/login", json={"key": "wrong"}).status_code == 401
        limited = client.post("/api/admin/login", json={"key": "test-admin-key"})

        assert limited.status_code == 429
        assert limited.headers["retry-after"] == str(settings.admin_login_window_seconds)


def test_admin_rejects_tampered_and_expired_sessions(monkeypatch):
    monkeypatch.setattr(settings, "admin_access_key", "test-admin-key")
    monkeypatch.setattr(settings, "admin_session_ttl_seconds", 120)
    admin._FAILED_ATTEMPTS.clear()

    with TestClient(_test_app()) as client:
        assert client.post("/api/admin/login", json={"key": "test-admin-key"}).status_code == 200
        token = client.cookies.get(admin.ADMIN_COOKIE_NAME)
        assert token

        client.cookies.set(admin.ADMIN_COOKIE_NAME, f"{token[:-1]}x", path="/")
        assert client.get("/api/admin/session").status_code == 401

        expired, _ = admin._issue_session("test-admin-key", now=100)
        client.cookies.set(admin.ADMIN_COOKIE_NAME, expired, path="/")
        monkeypatch.setattr(admin.time, "time", lambda: 221)
        assert client.get("/api/admin/session").status_code == 401


def test_admin_logout_clears_session(monkeypatch):
    monkeypatch.setattr(settings, "admin_access_key", "test-admin-key")
    admin._FAILED_ATTEMPTS.clear()

    with TestClient(_test_app()) as client:
        assert client.post("/api/admin/login", json={"key": "test-admin-key"}).status_code == 200
        assert client.post("/api/admin/logout", json={}).status_code == 200
        assert client.get("/api/admin/session").status_code == 401
