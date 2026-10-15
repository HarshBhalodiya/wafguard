"""
Alert dispatch (Phase 4, P4-2/P4-3).

Turns an alert row into something that actually reaches someone. Channels
are pluggable and every attempt is recorded in `alert_deliveries` so the
question "did anything receive this?" is always answerable:

  * `log`    — structured log record + delivery row. No network. Always on
               (the demo-safe default: alerting is demonstrable with zero
               external credentials, like the copilot).
  * `webhook`— generic JSON POST to a configured URL. This deliberately is
               *not* a vendor-specific "Slack integration": any receiver
               that accepts JSON works (Slack incoming webhooks, Teams
               bridges, PagerDuty proxies, a local file sink...).
  * `smtp`   — email, when a host is configured.

Contract: dispatching NEVER raises. A dead channel records a failed
delivery and moves on — an alerting outage must not break training,
backfill, or the API (the same degrade-don't-propagate rule the copilot
follows).
"""
import time
from typing import Optional

import requests

from app.config import get_settings
from app.utils.logging import get_logger

logger = get_logger(__name__)

WEBHOOK_ATTEMPTS = 3           # 1 try + 2 retries
WEBHOOK_RETRY_BACKOFF_S = 0.2  # linear backoff base; grows per attempt

CHANNEL_TYPES = ("log", "webhook", "smtp")


# --------------------------------------------------------------------------- channels
def ensure_default_channels(db) -> list[dict]:
    """Seed the channel set from env settings exactly once (idempotent).

    The `log` channel always exists. `webhook`/`smtp` are created when their
    env settings are present. Admin-created channels (via the API) are kept.
    """
    existing = {c["name"] for c in db.alert_channels.find({})}
    defaults = [
        {"name": "log", "type": "log", "target": "application log + delivery record", "enabled": True},
    ]
    settings = get_settings()
    if settings.alert_webhook_url:
        defaults.append({"name": "webhook", "type": "webhook", "target": settings.alert_webhook_url, "enabled": True})
    if settings.alert_smtp_host:
        defaults.append(
            {
                "name": "email",
                "type": "smtp",
                "target": settings.alert_smtp_to or settings.alert_smtp_from,
                "enabled": True,
            }
        )
    for channel in defaults:
        if channel["name"] not in existing:
            db.alert_channels.update_one({"name": channel["name"]}, {"$setOnInsert": channel}, upsert=True)
    return list(db.alert_channels.find({}))


def _record_delivery(db, alert_id: str, channel: str, status: str, attempts: int, error: Optional[str] = None) -> dict:
    from app.utils.time import utc_now

    doc = {
        "alert_id": alert_id,
        "channel": channel,
        "status": status,  # sent | logged | failed | skipped
        "attempts": attempts,
        "error": error,
        "created_at": utc_now(),
    }
    db.alert_deliveries.insert_one(dict(doc))
    return doc


# --------------------------------------------------------------------------- channels
def _send_log(alert: dict) -> None:
    logger.info(
        "ALERT[%s] lot=%s risk=%s prob=%.0f%% src=%s — %s",
        alert.get("severity"),
        alert.get("lot_id"),
        alert.get("risk_level"),
        (alert.get("risk_probability") or 0) * 100,
        alert.get("source"),
        alert.get("reason", ""),
    )


def _send_webhook(target: str, alert: dict, timeout: float) -> None:
    payload = {
        "alert_id": str(alert.get("_id") or alert.get("alert_id")),
        "lot_id": alert.get("lot_id"),
        "severity": alert.get("severity"),
        "risk_level": alert.get("risk_level"),
        "risk_probability": alert.get("risk_probability"),
        "reason": alert.get("reason"),
        "source": alert.get("source"),
        "created_at": alert.get("created_at"),
        "app": "WafGuard AI",
    }
    last_error = None
    for attempt in range(1, WEBHOOK_ATTEMPTS + 1):
        try:
            response = requests.post(target, json=payload, timeout=timeout)
            response.raise_for_status()
            return
        except requests.RequestException as exc:  # network/HTTP failure → retry, then record
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < WEBHOOK_ATTEMPTS:
                time.sleep(WEBHOOK_RETRY_BACKOFF_S * attempt)
    raise RuntimeError(last_error or "webhook delivery failed")


def _send_smtp(alert: dict) -> None:
    import smtplib
    from email.message import EmailMessage

    settings = get_settings()
    message = EmailMessage()
    message["Subject"] = f"[WafGuard {alert.get('severity')}] lot {alert.get('lot_id')} at risk"
    message["From"] = settings.alert_smtp_from
    message["To"] = settings.alert_smtp_to
    message.set_content(
        f"Lot {alert.get('lot_id')} — risk {alert.get('risk_level')} "
        f"({(alert.get('risk_probability') or 0) * 100:.0f}%).\n"
        f"Reason: {alert.get('reason', '')}\nSource: {alert.get('source', '')}\n"
    )
    with smtplib.SMTP(settings.alert_smtp_host, settings.alert_smtp_port, timeout=10) as server:
        if settings.alert_smtp_username:
            server.starttls()
            server.login(settings.alert_smtp_username, settings.alert_smtp_password)
        server.send_message(message)


# --------------------------------------------------------------------------- dispatch
def dispatch_alert(db, alert: dict, channels: Optional[list[dict]] = None) -> list[dict]:
    """Deliver one alert through every enabled channel. Never raises.

    Returns the list of delivery records written (one per enabled channel).
    """
    settings = get_settings()
    if not settings.alert_dispatch_enabled:
        return []
    alert_id = str(alert.get("_id") or alert.get("alert_id") or alert.get("lot_id"))
    channels = channels if channels is not None else ensure_default_channels(db)

    deliveries: list[dict] = []
    for channel in channels:
        if not channel.get("enabled", True):
            continue
        ctype = channel.get("type")
        try:
            if ctype == "log":
                _send_log(alert)
                deliveries.append(_record_delivery(db, alert_id, channel["name"], "logged", 1))
            elif ctype == "webhook":
                _send_webhook(channel["target"], alert, settings.alert_webhook_timeout_seconds)
                deliveries.append(_record_delivery(db, alert_id, channel["name"], "sent", WEBHOOK_ATTEMPTS))
            elif ctype == "smtp":
                _send_smtp(alert)
                deliveries.append(_record_delivery(db, alert_id, channel["name"], "sent", 1))
            else:
                deliveries.append(_record_delivery(db, alert_id, channel["name"], "skipped", 0, f"unknown channel type {ctype!r}"))
        except Exception as exc:  # noqa: BLE001 — a dead channel must never propagate
            logger.exception("Alert delivery failed on channel %s", channel["name"])
            deliveries.append(_record_delivery(db, alert_id, channel["name"], "failed", WEBHOOK_ATTEMPTS if ctype == "webhook" else 1, f"{type(exc).__name__}: {exc}"))
    return deliveries


def raise_alert(
    db,
    lot_id: str,
    severity: str,
    reason: str,
    source: str,
    risk_level: Optional[str] = None,
    risk_probability: Optional[float] = None,
) -> dict:
    """Create one alert row and dispatch it. Used by the train backfill and the
    admin scan endpoint so both paths behave identically."""
    from app.database.repositories.alert_repository import AlertRepository
    from app.utils.time import utc_now

    repo = AlertRepository(db)
    alert = repo.insert(
        {
            "lot_id": lot_id,
            "severity": severity,
            "risk_level": risk_level,
            "risk_probability": risk_probability,
            "reason": reason,
            "message": reason,  # UI renders `message` (AlertPanel.jsx)
            "source": source,
            "acknowledged_by": None,
            "resolution": None,
            "created_at": utc_now(),
        }
    )
    dispatch_alert(db, alert)
    return alert
