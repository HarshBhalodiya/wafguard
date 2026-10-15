"""
Phase 8 triage tests (P8-1/P8-2/P8-5): the open -> acknowledged -> resolved
state machine, the append-only audit trail, the RBAC matrix on the triage
actions, the status filter (including legacy derived-open rows), the D-030
no-recovery-claim guard, and the "triage never dispatches" contract. Uses the
shared mongomock `db` fixture and the same `get_db` override pattern as
test_rbac.py.
"""
import json

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.main import app
from app.services import alert_dispatch_service, alert_triage_service
from app.utils.exceptions import ValidationFailedError

# username -> (password, role) for the five seeded demo users
USERS = {
    "admin": ("wafguard-admin", "admin"),
    "engineer": ("wafguard-engineer", "process_engineer"),
    "yieldeng": ("wafguard-yield", "yield_engineer"),
    "equipeng": ("wafguard-equip", "equipment_engineer"),
    "viewer": ("wafguard-viewer", "viewer"),
}
TRIAGE_ROLES = ["admin", "engineer", "yieldeng", "equipeng"]


@pytest.fixture(scope="module")
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _auth(client, username: str) -> dict:
    password, _ = USERS[username]
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def _new_alert(db, lot_id: str = "LOT_TRIAGE") -> dict:
    """A fresh alert row through the real raise_alert path (Phase 4 contract:
    acknowledged_by/resolution seeded None, one log delivery)."""
    return alert_dispatch_service.raise_alert(
        db,
        lot_id=lot_id,
        severity="HIGH",
        risk_level="HIGH",
        risk_probability=0.9,
        reason="triage fixture",
        source="phase8-test",
    )


# ---------------------------------------------------------------------------
# P8-1: status derivation (new rows AND legacy pre-Phase-8 rows)
# ---------------------------------------------------------------------------

def test_status_of_new_and_legacy_rows(db):
    alert = _new_alert(db)
    assert "status" not in alert  # fresh rows carry no explicit status yet…
    assert alert_triage_service.status_of(alert) == "open"  # …and still read as open

    # legacy row from before Phase 8: no status field, Phase-4 fields present
    assert alert_triage_service.status_of({"acknowledged_by": None, "resolution": None}) == "open"
    # legacy derivation also honors the Phase-4 fields if something wrote them
    assert alert_triage_service.status_of({"acknowledged_by": "eng", "resolution": None}) == "acknowledged"
    assert alert_triage_service.status_of({"acknowledged_by": "eng", "resolution": "checked"}) == "resolved"
    # explicit status wins over derivation
    assert alert_triage_service.status_of({"status": "acknowledged", "acknowledged_by": None}) == "acknowledged"


def test_acknowledge_then_resolve_stamps_actor_and_appends_events(db):
    alert = _new_alert(db, "LOT_TRIAGE_FIELDS")

    acked, ack_event = alert_triage_service.acknowledge(
        db, alert, username="engineer", role="process_engineer", note="checking ETCH_007 control chart"
    )
    assert acked["status"] == "acknowledged"
    assert acked["acknowledged_by"] == "engineer" and acked["acknowledged_at"] is not None

    resolved, res_event = alert_triage_service.resolve(
        db, acked, username="admin", role="admin", note="Reviewed the lot-level chart; no action required."
    )
    assert resolved["status"] == "resolved"
    assert resolved["resolved_by"] == "admin" and resolved["resolved_at"] is not None
    assert resolved["resolution"] == "Reviewed the lot-level chart; no action required."

    # persisted, not just in-memory
    stored = db.alerts.find_one({"_id": alert["_id"]})
    assert stored["status"] == "resolved"

    # audit trail: exactly one event per transition, oldest first, actor+ts+note
    events = alert_triage_service.timeline(db, alert["_id"])
    assert [e["to_status"] for e in events] == ["acknowledged", "resolved"]
    assert [e["from_status"] for e in events] == ["open", "acknowledged"]
    for event in events:
        assert event["actor"]["username"] and event["actor"]["role"]
        assert event["at"] is not None
    assert events[0]["note"] == "checking ETCH_007 control chart"
    assert events[1]["actor"] == {"username": "admin", "role": "admin"}


def test_illegal_transitions_rejected_and_leave_no_events(db):
    alert = _new_alert(db, "LOT_TRIAGE_ILLEGAL")

    # open -> resolved is illegal: the tool must be picked up before it is closed
    with pytest.raises(ValidationFailedError):
        alert_triage_service.resolve(db, alert, username="eng", role="process_engineer")

    acked, _ = alert_triage_service.acknowledge(db, alert, username="eng", role="process_engineer")
    # duplicate acknowledge rejected (no double entries in the trail)
    with pytest.raises(ValidationFailedError):
        alert_triage_service.acknowledge(db, acked, username="eng", role="process_engineer")

    done, _ = alert_triage_service.resolve(db, acked, username="eng", role="process_engineer")
    # resolved is terminal
    with pytest.raises(ValidationFailedError):
        alert_triage_service.acknowledge(db, done, username="eng", role="process_engineer")
    with pytest.raises(ValidationFailedError):
        alert_triage_service.resolve(db, done, username="eng", role="process_engineer")

    # rejected attempts append nothing — the trail holds only real transitions
    assert len(alert_triage_service.timeline(db, alert["_id"])) == 2


def test_triage_notes_cannot_claim_recovery(db):
    """D-030: the generator records no interventions, so a note claiming the lot
    'recovered' would be an invented outcome — rejected at the boundary."""
    alert = _new_alert(db, "LOT_TRIAGE_HONESTY")
    acked, _ = alert_triage_service.acknowledge(db, alert, username="eng", role="process_engineer")
    with pytest.raises(ValidationFailedError) as excinfo:
        alert_triage_service.resolve(db, acked, username="eng", role="process_engineer", note="Lot recovered after PM.")
    assert "D-030" in str(excinfo.value)
    # the rejected note was not half-stored
    assert alert_triage_service.timeline(db, alert["_id"])[-1]["to_status"] == "acknowledged"


# ---------------------------------------------------------------------------
# P8-2: API flow + RBAC matrix
# ---------------------------------------------------------------------------

def test_api_acknowledge_resolve_timeline_flow(client, db):
    alert = _new_alert(db, "LOT_TRIAGE_API")
    alert_id = str(alert["_id"])
    headers = _auth(client, "engineer")

    # list: fresh alert is open (derived) and addressable by string alert_id
    listing = client.get("/api/alerts?status=open&limit=100", headers=headers)
    assert listing.status_code == 200, listing.text
    by_id = {a["alert_id"]: a for a in listing.json()["data"]["alerts"]}
    assert by_id[alert_id]["status"] == "open"

    ack = client.post(f"/api/alerts/{alert_id}/acknowledge", json={"note": "on it"}, headers=headers)
    assert ack.status_code == 200, ack.text
    assert ack.json()["data"]["alert"]["status"] == "acknowledged"
    assert ack.json()["data"]["event"]["to_status"] == "acknowledged"

    # the status filter moves with the lifecycle
    open_ids = {a["alert_id"] for a in client.get("/api/alerts?status=open&limit=100", headers=headers).json()["data"]["alerts"]}
    acked_ids = {a["alert_id"] for a in client.get("/api/alerts?status=acknowledged&limit=100", headers=headers).json()["data"]["alerts"]}
    assert alert_id not in open_ids and alert_id in acked_ids

    res = client.post(f"/api/alerts/{alert_id}/resolve", json={"note": "Checked the lot-level chart; no action."}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["data"]["alert"]["status"] == "resolved"
    assert res.json()["data"]["alert"]["resolution"] == "Checked the lot-level chart; no action."

    # timeline: two events, oldest first, with actors
    timeline = client.get(f"/api/alerts/{alert_id}/timeline", headers=headers)
    assert timeline.status_code == 200, timeline.text
    payload = timeline.json()["data"]
    assert payload["status"] == "resolved"
    assert [e["to_status"] for e in payload["events"]] == ["acknowledged", "resolved"]
    assert payload["events"][0]["actor"]["username"] == "engineer"

    # duplicates are rejected, not swallowed
    assert client.post(f"/api/alerts/{alert_id}/acknowledge", json={}, headers=headers).status_code == 422
    assert client.post(f"/api/alerts/{alert_id}/resolve", json={}, headers=headers).status_code == 422

    # nothing in the payload claims a recovery (D-030)
    assert "recovered" not in json.dumps(res.json()).lower()


def test_api_recovery_claim_note_rejected(client, db):
    alert = _new_alert(db, "LOT_TRIAGE_API_HONESTY")
    alert_id = str(alert["_id"])
    headers = _auth(client, "admin")
    assert client.post(f"/api/alerts/{alert_id}/acknowledge", json={}, headers=headers).status_code == 200
    rejected = client.post(f"/api/alerts/{alert_id}/resolve", json={"note": "recovered after fix"}, headers=headers)
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "D-030" in rejected.json()["error"]["message"]


def test_triage_rbac_matrix(client, db):
    alert_id = str(_new_alert(db, "LOT_TRIAGE_RBAC")["_id"])
    ack_url, res_url, tl_url = (
        f"/api/alerts/{alert_id}/acknowledge",
        f"/api/alerts/{alert_id}/resolve",
        f"/api/alerts/{alert_id}/timeline",
    )

    # anonymous: 401 everywhere (router-level auth)
    for url in (ack_url, res_url):
        assert client.post(url, json={}).status_code == 401
    assert client.get(tl_url).status_code == 401

    # viewer: read-only — mutations 403, timeline readable
    viewer = _auth(client, "viewer")
    assert client.post(ack_url, json={}, headers=viewer).status_code == 403
    assert client.post(res_url, json={}, headers=viewer).status_code == 403
    assert client.get(tl_url, headers=viewer).status_code == 200

    # the untouched alert is still open after the viewer's rejected attempts
    assert client.get(tl_url, headers=viewer).json()["data"]["status"] == "open"


@pytest.mark.parametrize("username", TRIAGE_ROLES)
def test_engineer_roles_can_acknowledge_and_resolve(client, db, username):
    alert_id = str(_new_alert(db, f"LOT_TRIAGE_{username}")["_id"])
    headers = _auth(client, username)

    ack = client.post(f"/api/alerts/{alert_id}/acknowledge", json={}, headers=headers)
    assert ack.status_code == 200, ack.text
    assert ack.json()["data"]["alert"]["acknowledged_by"] == username

    res = client.post(f"/api/alerts/{alert_id}/resolve", json={"note": "Investigated; documented findings."}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["data"]["alert"]["resolved_by"] == username

    events = client.get(f"/api/alerts/{alert_id}/timeline", headers=headers).json()["data"]["events"]
    assert [e["actor"]["username"] for e in events] == [username, username]


def test_status_filter_and_validation(client, db):
    headers = _auth(client, "engineer")
    # legacy backfill alerts (no status field) count as open — no backfill needed
    open_alerts = client.get("/api/alerts?status=open&limit=100", headers=headers).json()["data"]["alerts"]
    assert open_alerts and all(a["status"] == "open" for a in open_alerts)
    # unknown status is a typed 422, not a silent empty list
    bad = client.get("/api/alerts?status=bogus", headers=headers)
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "VALIDATION_ERROR"
    # severity filter still works alongside status
    combined = client.get("/api/alerts?status=open&severity=HIGH&limit=100", headers=headers)
    assert combined.status_code == 200
    assert all(a["severity"] == "HIGH" for a in combined.json()["data"]["alerts"])


def test_timeline_unknown_alert_is_404(client):
    headers = _auth(client, "engineer")
    assert client.get("/api/alerts/64f000000000000000000000/timeline", headers=headers).status_code == 404
    assert client.get("/api/alerts/not-an-objectid/timeline", headers=headers).status_code == 404
    assert client.post("/api/alerts/not-an-objectid/acknowledge", json={}, headers=headers).status_code == 404


def test_triage_never_dispatches(client, db):
    """The P4-2 dispatcher contract is untouched: acknowledging/resolving writes
    no delivery records and never re-dispatches."""
    alert = _new_alert(db, "LOT_TRIAGE_NODISPATCH")
    alert_id = str(alert["_id"])
    assert alert["acknowledged_by"] is None and alert["resolution"] is None  # raise_alert contract unchanged
    before = db.alert_deliveries.count_documents({"alert_id": alert_id})

    headers = _auth(client, "engineer")
    assert client.post(f"/api/alerts/{alert_id}/acknowledge", json={}, headers=headers).status_code == 200
    assert client.post(f"/api/alerts/{alert_id}/resolve", json={"note": "Done."}, headers=headers).status_code == 200

    assert db.alert_deliveries.count_documents({"alert_id": alert_id}) == before
