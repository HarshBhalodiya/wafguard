"""
Phase 9 (brief P8) — production engineering.

Pins: the brief's router names exist (and under /api/v1); the error format is
consistent and carries a request id; background jobs go API -> job -> result
with status in Mongo; models are loaded once at startup; health probes exist.
"""
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.jobs import runner
from app.main import app

BRIEF_ROUTES = [
    ("POST", "/predict"),
    ("POST", "/lot-risk"),
    ("GET", "/root-cause/{lot_id}"),
    ("GET", "/similarity/{lot_id}"),
    ("POST", "/defects/analyze"),
    ("GET", "/defects/lots/{lot_id}"),
    ("GET", "/recommendations/{lot_id}"),
    ("POST", "/whatif"),
    ("GET", "/quality/drift"),
    ("POST", "/jobs"),
    ("GET", "/jobs/{job_id}"),
    ("GET", "/jobs/{job_id}/result"),
]


def _client(db, username="admin", password="wafguard-admin"):
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    client.__enter__()
    token = client.post("/api/auth/login", json={"username": username, "password": password}).json()
    client.headers["Authorization"] = f"Bearer {token['data']['access_token']}"
    return client


@pytest.fixture()
def client(db):
    c = _client(db)
    yield c
    c.__exit__(None, None, None)
    app.dependency_overrides.clear()


def test_brief_routes_exist_versioned_and_unversioned(client):
    """A missing route answers 404 HTTP_ERROR (or 405); a present one reaches its handler
    (which may still say NOT_FOUND / VALIDATION_ERROR for the dummy ids used here)."""
    for method, path in BRIEF_ROUTES:
        concrete = path.replace("{lot_id}", "LOT_NOPE").replace("{job_id}", "job_nope")
        for prefix in ("/api", "/api/v1"):
            response = client.request(method, prefix + concrete, json={})
            code = (response.json().get("error") or {}).get("code")
            assert response.status_code != 405 and code != "HTTP_ERROR", f"{method} {prefix}{path} is not routed"


def test_openapi_documents_v1_and_the_error_envelope():
    schema = app.openapi()
    paths = schema["paths"]
    assert any(p.startswith("/api/v1/") for p in paths)
    assert not any(p.startswith("/api/lots") for p in paths), "the unversioned alias stays out of the docs"
    lots = paths["/api/v1/lots/{lot_id}"]["get"]["responses"]
    assert "404" in lots and "ErrorEnvelope" in str(lots["404"])


def test_versioned_and_alias_paths_return_the_same_data(client, incident_lot_id):
    a = client.get(f"/api/lots/{incident_lot_id}").json()
    b = client.get(f"/api/v1/lots/{incident_lot_id}").json()
    assert a["data"] == b["data"] and a["api_version"] == b["api_version"] == "1"


def test_errors_share_one_format_and_carry_the_request_id(client):
    response = client.get("/api/v1/lots/LOT_NOPE", headers={"X-Request-ID": "req-test-123"})
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False and body["data"] is None and body["api_version"] == "1"
    assert body["error"]["code"] == "NOT_FOUND" and body["error"]["request_id"] == "req-test-123"
    assert response.headers["X-Request-ID"] == "req-test-123"
    assert response.headers["X-API-Version"] == "1"

    invalid = client.post("/api/v1/whatif", json={"lot_id": 3})
    assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "VALIDATION_ERROR"
    assert invalid.json()["error"]["request_id"] == invalid.headers["X-Request-ID"]


def test_canonical_routes_match_the_legacy_ones(client, incident_lot_id):
    legacy = client.get(f"/api/lots/{incident_lot_id}/root-cause").json()["data"]
    canonical = client.get(f"/api/root-cause/{incident_lot_id}").json()["data"]
    assert [c["candidate"] for c in legacy["candidates"]] == [c["candidate"] for c in canonical["candidates"]]
    graph = client.get(f"/api/root-cause/{incident_lot_id}/graph").json()["data"]
    assert graph["nodes"] and graph["edges"]
    defects = client.get(f"/api/defects/lots/{incident_lot_id}").json()["data"]
    assert defects["n_wafers"] > 0 and "SYNTHETIC" in defects["label"]
    predicted = client.post("/api/predict", json={"lot_id": incident_lot_id}).json()["data"]
    assert predicted["risk_level"] in {"LOW", "MEDIUM", "HIGH"}


# --------------------------------------------------------------------------- jobs
def test_job_lifecycle_root_cause_batch(client, incident_lot_id):
    created = client.post("/api/jobs", json={"type": "root_cause_batch", "params": {"lot_ids": [incident_lot_id]}})
    assert created.status_code == 202, created.text
    job_id = created.json()["data"]["job_id"]
    assert created.json()["data"]["status"] == "queued"
    runner.wait(job_id, timeout=120)
    status = client.get(f"/api/jobs/{job_id}").json()["data"]
    assert status["status"] == "succeeded" and status["started_at"] and status["finished_at"]
    result = client.get(f"/api/jobs/{job_id}/result").json()["data"]["result"]
    assert result["results"][0]["lot_id"] == incident_lot_id and result["results"][0]["candidates"]


def test_sensor_batch_job_stores_quality_reports(client, db, sim_tables):
    lot_id = sim_tables["lots"]["lot_id"].iloc[3]
    rows = sim_tables["sensor_data"][sim_tables["sensor_data"]["lot_id"] == lot_id].copy()
    rows.loc[rows.index[:5], "value"] = 1e9  # impossible readings
    payload = {"type": "data_quality_batch", "params": {"telemetry": rows.astype(object).where(rows.notna(), None).to_dict("records")}}
    job_id = client.post("/api/jobs", json=payload).json()["data"]["job_id"]
    runner.wait(job_id, timeout=120)
    result = client.get(f"/api/jobs/{job_id}/result").json()["data"]["result"]
    assert result["summary"]["status_counts"].get("FAIL") == 1
    assert db.data_quality_reports.find_one({"lot_id": lot_id})["status"] == "FAIL"
    stored = client.get(f"/api/jobs/{job_id}").json()["data"]["params"]["telemetry"]
    assert stored.startswith("<") and "rows" in stored, "the raw sensor batch is not copied into the job document"


def test_job_result_states(client, db):
    db.jobs.insert_many(
        [
            {"job_id": "job_pending", "type": "root_cause_batch", "status": "running", "created_at": "2026-01-01"},
            {"job_id": "job_failed", "type": "root_cause_batch", "status": "failed", "error": "boom", "created_at": "2026-01-01"},
        ]
    )
    try:
        pending = client.get("/api/jobs/job_pending/result")
        assert pending.status_code == 409 and pending.json()["error"]["code"] == "JOB_NOT_READY"
        failed = client.get("/api/jobs/job_failed/result")
        assert failed.status_code == 409 and failed.json()["error"]["code"] == "JOB_FAILED"
        assert client.get("/api/jobs/job_nope").status_code == 404
        assert runner.recover_orphans(db) >= 1
        assert db.jobs.find_one({"job_id": "job_pending"})["status"] == "failed"
    finally:
        db.jobs.delete_many({"job_id": {"$in": ["job_pending", "job_failed"]}})


def test_job_submission_is_validated_and_role_gated(client, db):
    assert client.post("/api/jobs", json={"type": "nope", "params": {}}).status_code == 422
    assert client.post("/api/jobs", json={"type": "root_cause_batch", "params": {"lot_ids": []}}).status_code == 422
    viewer = _client(db, "viewer", "wafguard-viewer")
    try:
        assert viewer.post("/api/jobs", json={"type": "upcoming_lot_risk", "params": {}}).status_code == 403
        assert viewer.get("/api/jobs").status_code == 200
    finally:
        viewer.__exit__(None, None, None)


# --------------------------------------------------------------------------- startup + health
def test_models_are_loaded_once_at_startup(client):
    warmup = app.state.warmup
    names = {m["name"] for m in warmup["models"]}
    assert {"lot_yield_regressor", "lot_yield_classifier", "wafer_pattern_cnn", "engineering_kb"} <= names
    loaded = {m["name"]: m["loaded"] for m in warmup["models"]}
    assert loaded["lot_yield_regressor"] and loaded["lot_yield_classifier"] and loaded["engineering_kb"]
    health = client.get("/api/health").json()["data"]
    assert health["models_loaded"] == loaded and health["api_version"] == "1"


def test_liveness_and_readiness_probes(client):
    assert client.get("/api/health/live").status_code == 200
    ready = client.get("/api/health/ready")
    # The suite runs on mongomock with no real MongoDB, so the API is not "ready".
    assert ready.status_code in (200, 503)
    if ready.status_code == 503:
        assert ready.json()["error"]["code"] == "NOT_READY"
