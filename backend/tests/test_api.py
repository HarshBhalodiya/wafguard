"""
Full API integration tests. Uses FastAPI's TestClient with `get_db`
overridden to the seeded mongomock database from conftest.py — so these
tests exercise the real routers, real request validation, and the real
response envelope, without needing a live MongoDB server.
"""
import io

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api.deps import get_db
from app.data.loaders.synthetic_wafer_loader import generate_synthetic_wafer_maps
from app.main import app


@pytest.fixture()
def client(db):
    """Authenticated admin client — Phase 4 RBAC requires a token on every
    route except /api/health and /api/auth/login (see test_rbac.py)."""
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        login = test_client.post("/api/auth/login", json={"username": "admin", "password": "wafguard-admin"})
        assert login.status_code == 200, login.text
        test_client.headers["Authorization"] = f"Bearer {login.json()['data']['access_token']}"
        yield test_client
    app.dependency_overrides.clear()


def _assert_envelope_ok(response, expected_status: int = 200):
    assert response.status_code == expected_status, response.text
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    assert "timestamp" in body
    return body["data"]


def _assert_envelope_error(response, expected_status: int, expected_code: str | None = None):
    assert response.status_code == expected_status, response.text
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    if expected_code:
        assert body["error"]["code"] == expected_code
    return body["error"]


def test_health(client):
    data = _assert_envelope_ok(client.get("/api/health"))
    assert "status" in data and "database" in data


def test_login_success_and_failure(client):
    data = _assert_envelope_ok(client.post("/api/auth/login", json={"username": "admin", "password": "wafguard-admin"}))
    assert data["role"] == "admin"
    assert data["access_token"]

    _assert_envelope_error(
        client.post("/api/auth/login", json={"username": "admin", "password": "wrong-password"}),
        expected_status=401,
        expected_code="UNAUTHORIZED",
    )


def test_dashboard(client, db):
    data = _assert_envelope_ok(client.get("/api/dashboard"))
    for key in ("overall_yield", "at_risk_lot_count", "high_risk_lot_count", "top_root_causes", "yield_trend", "top_risk_lots"):
        assert key in data
    assert data["total_lots"] == db.lots.count_documents({})  # scale comes from the simulator config, not a magic number


def test_list_and_get_lot(client, incident_lot_id, db):
    listed = _assert_envelope_ok(client.get("/api/lots", params={"limit": 5}))
    assert listed["total"] == db.lots.count_documents({})
    assert len(listed["items"]) == 5

    filtered = _assert_envelope_ok(client.get("/api/lots", params={"risk_level": "HIGH", "limit": 100}))
    assert all(item["risk_level"] == "HIGH" for item in filtered["items"])

    lot = _assert_envelope_ok(client.get(f"/api/lots/{incident_lot_id}"))
    assert lot["lot_id"] == incident_lot_id
    assert lot["equipment_id"] == lot["equipment"]  # schema v2 dual field

    _assert_envelope_error(client.get("/api/lots/LOT_DOES_NOT_EXIST"), expected_status=404, expected_code="NOT_FOUND")


def test_yield_predict_by_lot_id(client, incident_lot_id):
    data = _assert_envelope_ok(client.post("/api/yield/predict", json={"lot_id": incident_lot_id}))
    assert data["lot_id"] == incident_lot_id
    assert data["risk_level"] in ("LOW", "MEDIUM", "HIGH")
    assert isinstance(data["predicted_yield"], float)


def test_yield_predict_requires_lot_id_or_features(client):
    _assert_envelope_error(client.post("/api/yield/predict", json={}), expected_status=422, expected_code="VALIDATION_ERROR")


def test_root_cause_and_recommendations(client, incident_lot_id):
    rc = _assert_envelope_ok(client.get(f"/api/lots/{incident_lot_id}/root-cause"))
    assert rc["candidates"][0]["candidate"] == "RF Power"

    rec = _assert_envelope_ok(client.get(f"/api/lots/{incident_lot_id}/recommendations"))
    assert rec["recommendation_type"] == "Suggested engineering investigation"
    assert len(rec["recommendations"]) > 0


def test_wafers_list_and_map(client, incident_lot_id):
    wafers = _assert_envelope_ok(client.get("/api/wafers", params={"lot_id": incident_lot_id}))
    assert len(wafers) == 25
    wafer_id = wafers[0]["wafer_id"]
    assert "die_grid" not in wafers[0]  # schema v2: grids are materialized on demand, not stored

    wafer_map = _assert_envelope_ok(client.get(f"/api/wafers/{wafer_id}/map"))
    assert wafer_map["wafer_id"] == wafer_id
    assert "predicted_pattern" in wafer_map or "wafer_map_pattern" in wafer_map

    _assert_envelope_error(client.get("/api/wafers/WAFER_DOES_NOT_EXIST/map"), expected_status=404)


def test_defect_analyze_upload(client):
    grid = None
    for sample in generate_synthetic_wafer_maps(n_per_class=1, grid_size=48, seed=555):
        if sample.pattern_label == "Edge-Ring":
            grid = np.array(sample.die_grid)
            break
    rgb = np.zeros((*grid.shape, 3), dtype=np.uint8)
    rgb[grid == 1] = [220, 220, 220]
    rgb[grid == 2] = [30, 30, 30]
    buffer = io.BytesIO()
    Image.fromarray(rgb, mode="RGB").save(buffer, format="PNG")
    buffer.seek(0)

    response = client.post("/api/defects/analyze", files={"file": ("wafer.png", buffer, "image/png")})
    data = _assert_envelope_ok(response)
    assert data["analysis_type"] == "Wafer Map Pattern Analysis"

    bad_response = client.post("/api/defects/analyze", files={"file": ("not_an_image.txt", io.BytesIO(b"nope"), "text/plain")})
    _assert_envelope_error(bad_response, expected_status=415, expected_code="UNSUPPORTED_FILE")


def test_risk_lots(client):
    data = _assert_envelope_ok(client.get("/api/risk/lots"))
    assert all(item["risk_level"] in ("MEDIUM", "HIGH") for item in data)


def test_equipment_list_and_detail(client, db):
    equipment_list = _assert_envelope_ok(client.get("/api/equipment"))
    assert len(equipment_list) == db.equipment.count_documents({})

    some_equipment_id = equipment_list[0]["equipment_id"]
    detail = _assert_envelope_ok(client.get(f"/api/equipment/{some_equipment_id}"))
    assert detail["equipment_id"] == some_equipment_id
    assert "recent_maintenance" in detail and "peers" in detail

    _assert_envelope_error(client.get("/api/equipment/NOT_REAL"), expected_status=404)


def test_similar_lots(client, incident_lot_id):
    data = _assert_envelope_ok(client.get(f"/api/similar/{incident_lot_id}", params={"k": 3}))
    assert len(data["similar_lots"]) <= 3
    assert incident_lot_id not in [s["lot_id"] for s in data["similar_lots"]]


def test_what_if(client, incident_lot_id):
    data = _assert_envelope_ok(client.post("/api/what-if", json={"lot_id": incident_lot_id, "overrides": {"rf_power": 858}}))
    assert data["after"]["predicted_yield"] >= data["before"]["predicted_yield"]

    _assert_envelope_error(
        client.post("/api/what-if", json={"lot_id": incident_lot_id, "overrides": {"bogus_param": 1}}),
        expected_status=422,
        expected_code="VALIDATION_ERROR",
    )


def test_models_and_model_performance(client):
    models = _assert_envelope_ok(client.get("/api/models"))
    model_names = {m["model_name"] for m in models}
    assert {"lot_yield_regressor", "lot_yield_classifier", "secom_failure_classifier"} <= model_names

    perf = _assert_envelope_ok(client.get("/api/model-performance"))
    assert len(perf["models"]) == len(models)


def test_data_import_requires_auth_then_succeeds(client, db):
    test_lot_id = "LOT_TEST_IMPORT_1"
    payload = {
        "lots": [
            {
                "lot_id": test_lot_id,
                "product": "5NM_CPU",
                "process_start": "2026-01-01T00:00:00",
                "equipment": "ETCH_001",
                "process_step": "ETCH",
            }
        ]
    }
    try:
        # explicit empty header overrides the fixture's admin token — this is the
        # anonymous case (full anonymous matrix lives in test_rbac.py)
        _assert_envelope_error(
            client.post("/api/data/import", json=payload, headers={"Authorization": ""}), expected_status=401
        )

        login_data = _assert_envelope_ok(client.post("/api/auth/login", json={"username": "admin", "password": "wafguard-admin"}))
        token = login_data["access_token"]

        data = _assert_envelope_ok(
            client.post("/api/data/import", json=payload, headers={"Authorization": f"Bearer {token}"})
        )
        assert data["imported"] == 1
    finally:
        # `db` is a session-scoped fixture shared with other test modules —
        # never leave behind a lot that would throw off another test's counts.
        db.lots.delete_one({"lot_id": test_lot_id})


def test_copilot(client, incident_lot_id, db):
    data = _assert_envelope_ok(client.post(f"/api/lots/{incident_lot_id}/copilot", json={"question": "Why is this lot at risk?"}))
    assert data["source"] == "deterministic_template"
    assert data["lot_id"] == incident_lot_id
    assert data["evidence"]["equipment"] == db.lots.find_one({"lot_id": incident_lot_id})["equipment"]

    _assert_envelope_error(client.post("/api/lots/LOT_DOES_NOT_EXIST/copilot", json={}), expected_status=404)
