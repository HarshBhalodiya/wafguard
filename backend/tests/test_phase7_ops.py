"""
Phase 7 test suite (P7-6): the instrumentation library, the request-timing
shim, the /api/ops/* surface, and the root-cause span/prefetch refactor.

Uses a PRIVATE MetricStore for library-contract tests (no shared global
state); the app-level tests ride on main.py's real METRIC_STORE singleton.
"""
import time

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.main import app
from app.ops.timeillustrator import MetricStore, Span

# username -> (password, role), same seeded users as test_rbac.py
USERS = {
    "admin": ("wafguard-admin", "admin"),
    "viewer": ("wafguard-viewer", "viewer"),
}


@pytest.fixture()
def client(db):
    """Same shape as test_rbac.py's client fixture (no implicit admin token —
    these tests manage authorization per-request)."""
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _auth(client, username: str) -> dict:
    password, _ = USERS[username]
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


# ---------------------------------------------------------------------------
# P7-1 library contracts (private store — no shared state)
# ---------------------------------------------------------------------------

def test_span_records_total_and_self_ms():
    store = MetricStore()
    with store.span("root") as root:
        with root.child("fast"):
            time.sleep(0.005)
        with root.child("slow") as s2:
            with s2.child("inner"):
                time.sleep(0.005)
    spans = store.recent(1)
    assert len(spans) == 1
    root_span = spans[0]
    d = root_span.to_dict()
    assert d["name"] == "root"
    assert d["total_ms"] >= 10.0
    # self excludes children
    assert d["self_ms"] < 5.0
    assert {c["name"] for c in d["children"]} == {"fast", "slow"}
    # hierarchy: slow has inner
    slow = next(c for c in d["children"] if c["name"] == "slow")
    assert any(c["name"] == "inner" for c in slow["children"])


def test_store_ring_buffer_bounded_and_order():
    store = MetricStore(maxlen=3)
    for i in range(5):
        with store.span(f"span-{i}"):
            pass
    assert len(store) == 3
    names = [s.name for s in store.recent(3)]
    assert names == ["span-2", "span-3", "span-4"]


def test_counters_increment_and_snapshot():
    store = MetricStore()
    store.incr("hits")
    store.incr("hits")
    store.incr("errors", 0.5)
    snap = store.counters_snapshot()
    assert snap["hits"] == 2.0
    assert snap["errors"] == 0.5


def test_aggregate_reports_p50_p95_and_errors_are_absent_for_active():
    store = MetricStore()
    for _ in range(4):
        with store.span("GET /x"):
            time.sleep(0.002)
    agg = store.aggregate(n=10)
    entry = next(e for e in agg if e["name"] == "GET /x")
    assert entry["count"] == 4
    assert entry["p50_ms"] > 0
    assert entry["p95_ms"] >= entry["p50_ms"]
    assert entry["active_count"] == 0


def test_active_span_visible_not_dropped():
    store = MetricStore()
    span = Span("crashed-request")
    store.record(span)  # never finished: must still appear, flagged active
    agg = store.aggregate()
    assert agg[0]["active_count"] == 1
    text = store.render_text()
    assert "crashed-request" in text and "▸" in text


def test_render_text_caps_depth():
    store = MetricStore()
    with store.span("root") as r:
        a = r.child("a")
        b = a.child("b")
        b.child("c")
    text = store.render_text(n=1, max_depth=2)
    assert "a" in text
    assert "truncated" in text  # depth-3 child collapsed


def test_json_render_cap_matches_text():
    store = MetricStore()
    with store.span("root") as r:
        a = r.child("a")
        a.child("too-deep")
    data = store.render_json(n=1, max_depth=1)
    assert data[0]["children"][0]["name"] == "a"
    assert data[0]["children"][0]["children"][0]["__truncated__"] == 1


# ---------------------------------------------------------------------------
# P7-2 middleware shim — app-level, via the real singleton store
# ---------------------------------------------------------------------------

@pytest.fixture()
def fresh_store():
    """Swap the process-owned store via registry.swap_store (the ONLY writable
    seam) so both the main.py middleware and the ops routes see it; restore
    the original at teardown, even on failure."""
    from app.ops import registry
    new_store = MetricStore()
    previous = registry.swap_store(new_store)
    yield new_store
    registry.swap_store(previous)


def test_middleware_records_request_spans(client, fresh_store):
    store = fresh_store
    before = len(store)
    client.get("/api/health")
    assert len(store) == before + 1
    span = store.recent(1)[0]
    assert span.name == "GET /api/health"
    assert span.total_ms > 0


def test_middleware_records_error_counter(client, fresh_store):
    store = fresh_store
    client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    snap = store.counters_snapshot()
    assert snap.get("requests:POST /api/auth/login:errors", 0) >= 1


def test_legacy_health_metrics_unchanged_p3_contract(client):
    # Phase 3 contract: /api/health still carries request_metrics p95 payload.
    body = client.get("/api/health").json()
    data = body["data"]
    assert "request_metrics" in data and "readiness" in data


# ---------------------------------------------------------------------------
# P7-3 ops endpoint surface — RBAC + shallow data discipline
# ---------------------------------------------------------------------------

def test_ops_metrics_requires_auth(client):
    r = client.get("/api/ops/metrics")
    assert r.status_code == 401
    r = client.get("/api/ops/health-probe")
    assert r.status_code == 401


def test_ops_rejects_viewer_role(client):
    headers = _auth(client, "viewer")
    r = client.get("/api/ops/metrics", headers=headers)
    assert r.status_code == 403
    r = client.get("/api/ops/health-probe", headers=headers)
    assert r.status_code == 403


def test_ops_metrics_admin_positive(client, fresh_store):
    store = fresh_store
    with store.span("GET /api/health"):
        pass
    headers = _auth(client, "admin")
    r = client.get("/api/ops/metrics", params={"window": 10}, headers=headers)
    assert r.status_code == 200
    data = r.json()["data"]
    # window_spans is snapshotted inside the handler, before the ops request's
    # own span is filed by the middleware — so it may lag len(store.recent())
    # by exactly this request. Assert the honest invariant, not a race.
    assert 0 < data["window_spans"] <= len(store.recent(10))
    assert any(e["name"] == "GET /api/health" for e in data["endpoints"])
    # shallow-data discipline: no DB contents / filesystem paths leak
    blob = str(data)
    assert "mongodb" not in blob.lower()
    assert "E:\\\\" not in blob
    probe = client.get("/api/ops/health-probe", headers=headers).json()["data"]
    assert probe["store"]["recorded"] >= 1
    assert "restarts" in probe["note"]  # honesty about in-process scope


# ---------------------------------------------------------------------------
# P7-5 rootcause: spans + prefetch must not change results
# ---------------------------------------------------------------------------

def test_compute_root_cause_records_spans(db, incident_lot_id, fresh_store):
    from app.ops import registry
    from app.services.rootcause_service import compute_root_cause
    from app.database.repositories.prediction_repository import PredictionRepository

    if not PredictionRepository(db).latest_for_lot(incident_lot_id):
        pytest.skip("incident lot has no prediction in this fixture set")

    new_store = fresh_store
    result = compute_root_cause(db, incident_lot_id)
    assert result["candidates"], "engine still returns ranked candidates"
    trees = new_store.render_json(n=1)
    assert trees, "span tree recorded"
    root = trees[0]
    assert root["name"] == "rootcause:compute"
    child_names = {c["name"] for c in root["children"]}
    assert "ml:shap" in child_names and "db:equip_stats" in child_names


def test_equipment_association_prefetch_matches_legacy(db, incident_lot_id):
    from app.database.repositories.lot_repository import LotRepository
    from app.services import rootcause_service as rc

    lot = LotRepository(db).get_by_lot_id(incident_lot_id)
    if not lot:
        pytest.skip("no incident lot")
    fab = rc._fab_baselines(db, str(lot["process_start"]))
    legacy_scores = []
    for candidate, (base_param, _) in rc.CANDIDATE_DEFINITIONS.items():
        s_legacy, _ = rc._equipment_association(db, lot, candidate, base_param, lot.get("features_snapshot", {}), fab)
        legacy_scores.append(round(s_legacy, 6))
    prefetched = rc._prefetch_equipment_stats(db, lot, fab)
    prefetch_scores = []
    for candidate, (base_param, _) in rc.CANDIDATE_DEFINITIONS.items():
        s_new, _ = rc._equipment_association(db, lot, candidate, base_param, lot.get("features_snapshot", {}), fab, prefetched)
        prefetch_scores.append(round(s_new, 6))
    assert legacy_scores == prefetch_scores, "prefetch must be a pure refactor of timing, not of numbers"
