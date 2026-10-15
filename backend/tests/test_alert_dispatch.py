"""
Phase 4 alert-dispatch tests (P4-2/P4-8): delivery records per channel,
webhook retry-then-record, the never-raise contract, disabled channels,
and the scan's per-lot dedupe. Network is faked — no test opens a socket.
"""
import mongomock
import pytest
import requests

from app.services import alert_dispatch_service


@pytest.fixture()
def empty_db():
    return mongomock.MongoClient()["wafguard_alerts_test"]


def _fake_response(status_code=200):
    class Response:
        def raise_for_status(self):
            if status_code >= 400:
                raise requests.HTTPError(f"{status_code} error")

    return Response()


def _no_sleep(monkeypatch):
    monkeypatch.setattr(alert_dispatch_service.time, "sleep", lambda *_: None)


# --------------------------------------------------------------------------- log channel
def test_raise_alert_writes_alert_and_log_delivery(empty_db):
    alert = alert_dispatch_service.raise_alert(
        empty_db, lot_id="LOT_1", severity="HIGH", risk_level="HIGH", risk_probability=0.9,
        reason="test reason", source="unit-test",
    )
    assert alert["lot_id"] == "LOT_1"
    assert alert["message"] == "test reason"  # AlertPanel renders `message`
    assert alert["acknowledged_by"] is None

    deliveries = list(empty_db.alert_deliveries.find({}))
    assert len(deliveries) == 1
    assert deliveries[0]["channel"] == "log"
    assert deliveries[0]["status"] == "logged"


# --------------------------------------------------------------------------- webhook
def test_webhook_success_records_sent_payload(empty_db, monkeypatch):
    alert = alert_dispatch_service.raise_alert(empty_db, lot_id="LOT_2", severity="HIGH", reason="r", source="unit-test")
    seen = {}

    def fake_post(url, json=None, timeout=None):
        seen.update({"url": url, "json": json})
        return _fake_response(200)

    monkeypatch.setattr(alert_dispatch_service.requests, "post", fake_post)
    channels = [{"name": "hook", "type": "webhook", "target": "http://example.invalid/hook", "enabled": True}]
    deliveries = alert_dispatch_service.dispatch_alert(empty_db, alert, channels=channels)

    assert [d["status"] for d in deliveries] == ["sent"]
    assert seen["url"] == "http://example.invalid/hook"
    assert seen["json"]["lot_id"] == "LOT_2"
    assert seen["json"]["severity"] == "HIGH"


def test_webhook_failure_is_recorded_and_never_raises(empty_db, monkeypatch):
    _no_sleep(monkeypatch)
    alert = alert_dispatch_service.raise_alert(empty_db, lot_id="LOT_3", severity="HIGH", reason="r", source="unit-test")

    calls = {"n": 0}

    def failing_post(url, json=None, timeout=None):
        calls["n"] += 1
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(alert_dispatch_service.requests, "post", failing_post)
    channels = [{"name": "hook", "type": "webhook", "target": "http://example.invalid/hook", "enabled": True}]
    deliveries = alert_dispatch_service.dispatch_alert(empty_db, alert, channels=channels)  # must not raise

    assert [d["status"] for d in deliveries] == ["failed"]
    assert calls["n"] == 3  # 1 try + 2 retries
    assert "ConnectionError" in deliveries[0]["error"]
    assert deliveries[0]["attempts"] == 3


def test_disabled_channel_is_skipped_without_record(empty_db):
    alert = alert_dispatch_service.raise_alert(empty_db, lot_id="LOT_4", severity="HIGH", reason="r", source="unit-test")
    before = empty_db.alert_deliveries.count_documents({})
    channels = [{"name": "hook", "type": "webhook", "target": "http://example.invalid", "enabled": False}]
    deliveries = alert_dispatch_service.dispatch_alert(empty_db, alert, channels=channels)
    assert deliveries == []
    assert empty_db.alert_deliveries.count_documents({}) == before


def test_dispatch_master_switch_skips_everything(empty_db, monkeypatch):
    alert = alert_dispatch_service.raise_alert(empty_db, lot_id="LOT_5", severity="HIGH", reason="r", source="unit-test")
    before = empty_db.alert_deliveries.count_documents({})

    class Off:
        alert_dispatch_enabled = False

    monkeypatch.setattr(alert_dispatch_service, "get_settings", lambda: Off())
    deliveries = alert_dispatch_service.dispatch_alert(empty_db, alert)
    assert deliveries == []
    assert empty_db.alert_deliveries.count_documents({}) == before


def test_unknown_channel_type_is_recorded_skipped(empty_db):
    alert = alert_dispatch_service.raise_alert(empty_db, lot_id="LOT_6", severity="HIGH", reason="r", source="unit-test")
    deliveries = alert_dispatch_service.dispatch_alert(
        empty_db, alert, channels=[{"name": "mystery", "type": "pigeon", "target": "", "enabled": True}]
    )
    assert [d["status"] for d in deliveries] == ["skipped"]


# --------------------------------------------------------------------------- scan dedupe
def test_scan_creates_alert_once_per_high_risk_lot(empty_db):
    from app.database.repositories.alert_repository import AlertRepository
    from app.utils.time import utc_now

    empty_db.lots.insert_many(
        [
            {"lot_id": "H1", "risk_level": "HIGH", "risk_probability": 0.9, "process_start": utc_now()},
            {"lot_id": "H2", "risk_level": "HIGH", "risk_probability": 0.8, "process_start": utc_now()},
            {"lot_id": "L1", "risk_level": "LOW", "risk_probability": 0.1, "process_start": utc_now()},
        ]
    )
    # the route's role gate is HTTP-only; its handler logic is mirrored in _scan below
    first = _scan(empty_db)
    assert first["created"] == 2
    second = _scan(empty_db)
    assert second["created"] == 0
    assert second["skipped_already_alerted"] == 2
    assert AlertRepository(empty_db).count({"lot_id": "H1"}) == 1


def _scan(db) -> dict:
    """Mirror of the /api/alerts/scan handler logic (role gate is HTTP-only)."""
    from app.database.repositories.alert_repository import AlertRepository
    from app.database.repositories.lot_repository import LotRepository

    lots = LotRepository(db).find_many({"risk_level": "HIGH"}, sort=[("process_start", -1)])
    alert_repo = AlertRepository(db)
    created, skipped = [], 0
    for lot in lots:
        if alert_repo.for_lot(lot["lot_id"]):
            skipped += 1
            continue
        alert_dispatch_service.raise_alert(
            db, lot_id=lot["lot_id"], severity="HIGH", risk_level=lot.get("risk_level"),
            risk_probability=lot.get("risk_probability"), reason="risk HIGH", source="alerts/scan",
        )
        created.append(lot["lot_id"])
    return {"created": len(created), "lot_ids": created, "skipped_already_alerted": skipped}
