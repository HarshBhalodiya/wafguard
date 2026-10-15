"""
Alerting API (Phase 4, P4-3 + Phase 8, P8-2).

Reads (alerts, deliveries, channels, triage timeline) are available to any
authenticated role; mutations split three ways: triage (acknowledge/resolve)
is an engineering action (engineer roles + admin — viewer stays read-only per
the D-027 action-matrix style), while channel management, scan and redeliver
remain admin-only. Every raised alert is dispatched through the configured
channels (see app.services.alert_dispatch_service); triage never dispatches.
"""
from typing import Optional

from fastapi import APIRouter, Body, Depends
from pymongo.database import Database

from app.api.deps import get_db
from app.database.repositories.alert_repository import (
    AlertChannelRepository,
    AlertDeliveryRepository,
    AlertRepository,
)
from app.database.repositories.lot_repository import LotRepository
from app.services import alert_dispatch_service, alert_triage_service
from app.utils.exceptions import NotFoundError, ValidationFailedError
from app.utils.responses import success
from app.utils.security import require_roles
from app.utils.time import utc_now

router = APIRouter()

CHANNEL_TYPES = alert_dispatch_service.CHANNEL_TYPES

# P8-2: triage is an engineering action (pick a lot up, close it out).
TRIAGE_ROLES = ("admin", "process_engineer", "yield_engineer", "equipment_engineer")


def _find_alert(db: Database, alert_id: str) -> Optional[dict]:
    """Raw alert document (with its ObjectId `_id`) addressed by the string id
    the API exposes. Shared by redeliver and the triage routes so all three
    address alerts identically."""
    from bson import ObjectId
    from bson.errors import InvalidId

    doc = db.alerts.find_one({"_id": alert_id})
    if doc:
        return doc
    try:
        return db.alerts.find_one({"_id": ObjectId(alert_id)})
    except InvalidId:
        return None


def _require_alert(db: Database, alert_id: str) -> dict:
    alert = _find_alert(db, alert_id)
    if not alert:
        raise NotFoundError(f"Alert {alert_id} was not found.")
    return alert


def _public(alert: dict) -> dict:
    """Response shape: string `alert_id` instead of Mongo's ObjectId, plus the
    derived lifecycle status (legacy rows honestly read as `open`)."""
    doc = dict(alert)
    doc["alert_id"] = str(doc.pop("_id", ""))
    doc["status"] = alert_triage_service.status_of(doc)
    return doc


@router.get("/alerts")
def list_alerts(
    limit: int = 20,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    db: Database = Depends(get_db),
):
    alerts = AlertRepository(db).recent(limit=limit, severity=severity, status=status)
    for alert in alerts:  # annotate the derived status so clients never guess
        alert["status"] = alert_triage_service.status_of(alert)
    return success({"alerts": alerts})


@router.get("/alerts/deliveries")
def list_deliveries(limit: int = 20, alert_id: Optional[str] = None, db: Database = Depends(get_db)):
    repo = AlertDeliveryRepository(db)
    deliveries = repo.for_alert(alert_id) if alert_id else repo.recent(limit=limit)
    return success({"deliveries": deliveries})


@router.get("/alerts/channels")
def list_channels(db: Database = Depends(get_db)):
    channels = AlertChannelRepository(db).find_many({})
    # never leak channel secrets in a listing
    for channel in channels:
        if channel.get("type") == "webhook" and channel.get("target"):
            target = str(channel["target"])
            channel["target"] = target.split("?")[0][-40:] if len(target) > 40 else "***"
    return success({"channels": channels})


@router.post("/alerts/channels", status_code=201)
def create_channel(
    payload: dict,
    db: Database = Depends(get_db),
    _admin: dict = Depends(require_roles("admin")),
):
    name = (payload.get("name") or "").strip()
    ctype = payload.get("type")
    target = (payload.get("target") or "").strip()
    if not name:
        raise ValidationFailedError("Channel 'name' is required.")
    if ctype not in CHANNEL_TYPES:
        raise ValidationFailedError(f"Channel 'type' must be one of {CHANNEL_TYPES}.")
    if ctype in ("webhook", "smtp") and not target and ctype == "webhook":
        raise ValidationFailedError("Webhook channels require a 'target' URL.")
    repo = AlertChannelRepository(db)
    if repo.by_name(name):
        raise ValidationFailedError(f"Channel '{name}' already exists.")
    channel = {"name": name, "type": ctype, "target": target, "enabled": bool(payload.get("enabled", True)), "created_at": utc_now()}
    repo.collection.insert_one(dict(channel))
    return success({"channel": channel})


@router.delete("/alerts/channels/{name}")
def delete_channel(
    name: str,
    db: Database = Depends(get_db),
    _admin: dict = Depends(require_roles("admin")),
):
    repo = AlertChannelRepository(db)
    channel = repo.by_name(name)
    if not channel:
        raise NotFoundError(f"Channel '{name}' was not found.")
    if name == "log":
        raise ValidationFailedError("The built-in 'log' channel cannot be removed.")
    repo.delete_many({"name": name})
    return success({"deleted": name})


@router.post("/alerts/scan")
def scan_high_risk_lots(
    db: Database = Depends(get_db),
    _admin: dict = Depends(require_roles("admin")),
):
    """Raise + dispatch alerts for currently-HIGH-risk lots that have no alert yet
    (dedupe per lot). Lets an operator trigger alerting without retraining."""
    lots = LotRepository(db).find_many({"risk_level": "HIGH"}, sort=[("process_start", -1)])
    alert_repo = AlertRepository(db)
    created, skipped = [], 0
    for lot in lots:
        if alert_repo.for_lot(lot["lot_id"]):
            skipped += 1
            continue
        alert = alert_dispatch_service.raise_alert(
            db,
            lot_id=lot["lot_id"],
            severity="HIGH",
            risk_level=lot.get("risk_level"),
            risk_probability=lot.get("risk_probability"),
            reason=f"Lot risk level is HIGH ({(lot.get('risk_probability') or 0) * 100:.0f}% predicted low-yield risk).",
            source="alerts/scan",
        )
        created.append(alert["lot_id"])
    return success({"created": len(created), "lot_ids": created, "skipped_already_alerted": skipped})


@router.post("/alerts/{alert_id}/redeliver")
def redeliver_alert(
    alert_id: str,
    db: Database = Depends(get_db),
    _admin: dict = Depends(require_roles("admin")),
):
    alert = _require_alert(db, alert_id)
    deliveries = alert_dispatch_service.dispatch_alert(db, alert)
    return success({"alert_id": alert_id, "deliveries": [d["status"] for d in deliveries]})


# --------------------------------------------------------------------------- triage (Phase 8, P8-2)
@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(
    alert_id: str,
    payload: Optional[dict] = Body(default=None),
    db: Database = Depends(get_db),
    user: dict = Depends(require_roles(*TRIAGE_ROLES)),
):
    """Take ownership of an alert: open -> acknowledged. Duplicate or illegal
    transitions are rejected (422) rather than swallowed — the audit trail
    must never contain two acknowledgements of the same alert."""
    alert = _require_alert(db, alert_id)
    alert, event = alert_triage_service.acknowledge(
        db, alert, username=user["username"], role=user["role"], note=(payload or {}).get("note")
    )
    return success({"alert": _public(alert), "event": event})


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(
    alert_id: str,
    payload: Optional[dict] = Body(default=None),
    db: Database = Depends(get_db),
    user: dict = Depends(require_roles(*TRIAGE_ROLES)),
):
    """Close an alert: acknowledged -> resolved, with an optional free-text
    resolution note. Notes claiming a lot outcome ("recovered") are rejected —
    this system records no interventions (decision log D-030)."""
    alert = _require_alert(db, alert_id)
    alert, event = alert_triage_service.resolve(
        db, alert, username=user["username"], role=user["role"], note=(payload or {}).get("note")
    )
    return success({"alert": _public(alert), "event": event})


@router.get("/alerts/{alert_id}/timeline")
def alert_timeline(alert_id: str, db: Database = Depends(get_db)):
    """Append-only triage history for one alert (who acknowledged/resolved it,
    when, and their note). Alerts that have never been triaged return an empty
    list — the trail starts with the first action, no fabricated backfill."""
    alert = _require_alert(db, alert_id)
    return success(
        {
            "alert_id": str(alert["_id"]),
            "status": alert_triage_service.status_of(alert),
            "events": alert_triage_service.timeline(db, alert["_id"]),
        }
    )
