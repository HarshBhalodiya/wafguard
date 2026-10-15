"""
Phase 4 RBAC tests (P4-1/P4-8): authentication on every route except
health/login, and the per-action role matrix. Uses the seeded mongomock DB
through the same `get_db` override as test_api.py.
"""
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.main import app

# username -> (password, role) for the five seeded demo users
USERS = {
    "admin": ("wafguard-admin", "admin"),
    "engineer": ("wafguard-engineer", "process_engineer"),
    "yieldeng": ("wafguard-yield", "yield_engineer"),
    "equipeng": ("wafguard-equip", "equipment_engineer"),
    "viewer": ("wafguard-viewer", "viewer"),
}


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _token(client, username: str) -> str:
    password, _ = USERS[username]
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["data"]["access_token"]


def _auth(client, username: str) -> dict:
    return {"Authorization": f"Bearer {_token(client, username)}"}


def _lot_id(client, headers) -> str:
    response = client.get("/api/lots?limit=1", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]["items"][0]["lot_id"]


# --------------------------------------------------------------------------- anonymous
def test_health_and_login_are_open(client):
    assert client.get("/api/health").status_code == 200
    assert client.post("/api/auth/login", json={"username": "admin", "password": "wafguard-admin"}).status_code == 200


def test_cors_preflight_does_not_require_auth():
    """Browsers send OPTIONS without a Bearer token; that must not 401.

    Uses a bare TestClient (no DB seed/training) because preflight is answered
    by CORSMiddleware before any route or get_current_user runs.
    """
    with TestClient(app) as cors_client:
        response = cors_client.options(
            "/api/dashboard",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
    assert response.status_code in (200, 204), response.text
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
    allow_headers = (response.headers.get("access-control-allow-headers") or "").lower()
    assert "authorization" in allow_headers


def test_everything_else_requires_auth(client):
    for method, url in [
        ("GET", "/api/lots"),
        ("GET", "/api/dashboard"),
        ("GET", "/api/alerts"),
        ("GET", "/api/alerts/channels"),
        ("POST", "/api/what-if"),
        ("POST", "/api/lots/LOT_X/copilot"),
    ]:
        response = client.request(method, url, json={"lot_id": "LOT_X", "overrides": {}, "question": "q"})
        assert response.status_code == 401, f"{method} {url} -> {response.status_code}"


def test_bad_login_rejected(client):
    assert client.post("/api/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    assert client.get("/api/lots", headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401


# --------------------------------------------------------------------------- reads: every role
@pytest.mark.parametrize("username", list(USERS))
def test_all_roles_can_read(client, username):
    headers = _auth(client, username)
    assert client.get("/api/lots?limit=1", headers=headers).status_code == 200
    assert client.get("/api/alerts", headers=headers).status_code == 200


# --------------------------------------------------------------------------- action matrix
def test_viewer_is_read_only(client):
    headers = _auth(client, "viewer")
    lot_id = _lot_id(client, headers)
    assert client.post("/api/what-if", json={"lot_id": lot_id, "overrides": {"rf_power": 860}}, headers=headers).status_code == 403
    assert client.post(f"/api/lots/{lot_id}/copilot", json={"question": "why?"}, headers=headers).status_code == 403
    assert client.post("/api/alerts/scan", headers=headers).status_code == 403
    assert client.post("/api/alerts/channels", json={"name": "x", "type": "log"}, headers=headers).status_code == 403


def test_process_engineer_actions(client):
    headers = _auth(client, "engineer")
    lot_id = _lot_id(client, headers)
    assert client.post("/api/what-if", json={"lot_id": lot_id, "overrides": {"rf_power": 860}}, headers=headers).status_code == 200
    assert client.post(f"/api/lots/{lot_id}/copilot", json={"question": "why?"}, headers=headers).status_code == 200
    # data import stays admin+process_engineer (unchanged from Phase 0); channel admin is admin-only
    assert client.post("/api/alerts/channels", json={"name": "x", "type": "log"}, headers=headers).status_code == 403


def test_equipment_engineer_can_copilot_but_not_what_if(client):
    headers = _auth(client, "equipeng")
    lot_id = _lot_id(client, headers)
    assert client.post(f"/api/lots/{lot_id}/copilot", json={"question": "why?"}, headers=headers).status_code == 200
    assert client.post("/api/what-if", json={"lot_id": lot_id, "overrides": {"rf_power": 860}}, headers=headers).status_code == 403


def test_admin_manages_alert_channels_and_scan(client):
    headers = _auth(client, "admin")
    created = client.post("/api/alerts/channels", json={"name": "qa-webhook", "type": "webhook", "target": "http://127.0.0.1:9/hook"}, headers=headers)
    assert created.status_code == 201, created.text
    # duplicate name rejected (ValidationFailedError -> 422)
    assert client.post("/api/alerts/channels", json={"name": "qa-webhook", "type": "log"}, headers=headers).status_code == 422
    # invalid type rejected
    assert client.post("/api/alerts/channels", json={"name": "qa-2", "type": "carrier-pigeon"}, headers=headers).status_code == 422
    # built-in log channel cannot be deleted (ValidationFailedError -> 422)
    assert client.delete("/api/alerts/channels/log", headers=headers).status_code == 422
    assert client.delete("/api/alerts/channels/qa-webhook", headers=headers).status_code == 200

    # scan: all current HIGH-risk lots already have alerts from the backfill → 0 created, no duplicates
    scan = client.post("/api/alerts/scan", headers=headers)
    assert scan.status_code == 200, scan.text
    assert scan.json()["data"]["created"] == 0
    assert scan.json()["data"]["skipped_already_alerted"] > 0
