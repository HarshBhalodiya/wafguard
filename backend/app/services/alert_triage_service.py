"""
Alert triage (Phase 8, P8-1): the open -> acknowledged -> resolved state
machine plus the append-only audit trail behind it.

This is the workflow Phase 4 deliberately left open (its `acknowledged_by` /
`resolution` columns existed but nothing ever wrote them — reports/phase4_report.md):

  * `status_of(alert)` derives the lifecycle status for BOTH new rows (which
    get an explicit `status` on their first transition) and legacy rows raised
    before Phase 8 (which are read as `open` — no destructive backfill of the
    shared Atlas cluster; the derived default does the work instead).
  * `acknowledge()` / `resolve()` validate the transition, stamp the actor and
    time on the alert, and append exactly one event to `alert_events`. The
    trail is append-only: events are never updated or deleted.
  * `resolved` is terminal, and an alert must be acknowledged before it can be
    resolved — the trail should answer "who picked this up" as well as
    "who closed it".

Honesty (D-030): the dataset records no interventions, so no triage note may
claim an outcome. Notes carrying recovery-claim wording are rejected instead
of being stored as a statistic the data cannot support.

Dispatch is untouched by design: triage is a separate workflow from delivery —
the P4-2 never-raises contract in `alert_dispatch_service` is not modified,
and acknowledging/resolving never re-dispatches.
"""
from typing import Optional

from app.database.repositories.alert_repository import AlertEventRepository, AlertRepository
from app.utils.exceptions import ValidationFailedError
from app.utils.time import utc_now

STATUS_OPEN = "open"
STATUS_ACKNOWLEDGED = "acknowledged"
STATUS_RESOLVED = "resolved"
STATUSES = (STATUS_OPEN, STATUS_ACKNOWLEDGED, STATUS_RESOLVED)

# Legal moves. `resolved` is terminal; closing an alert requires acknowledging
# it first (pick it up, then close it).
LEGAL_TRANSITIONS: dict[str, set[str]] = {
    STATUS_OPEN: {STATUS_ACKNOWLEDGED},
    STATUS_ACKNOWLEDGED: {STATUS_RESOLVED},
    STATUS_RESOLVED: set(),
}

# D-030: recovery-claim markers a triage note may not contain. The generator
# injects faults but records no interventions, so "the lot recovered after X"
# is exactly the kind of invented outcome this project refuses to store.
_RECOVERY_CLAIM_MARKERS = ("recover", "cured", "restored yield", "yield restored", "fixed the yield")


def status_of(alert: dict) -> str:
    """Current lifecycle status of an alert row.

    Explicit `status` wins (rows that have been triaged). Legacy rows — raised
    before Phase 8, or never touched since — are derived from the Phase 4
    fields so they all honestly read as `open` without a data migration.
    """
    explicit = alert.get("status")
    if explicit in LEGAL_TRANSITIONS:
        return explicit
    if alert.get("resolution"):
        return STATUS_RESOLVED
    if alert.get("acknowledged_by"):
        return STATUS_ACKNOWLEDGED
    return STATUS_OPEN


def assert_note_is_honest(note: Optional[str]) -> None:
    """Reject triage notes that claim a lot outcome (D-030)."""
    if not note:
        return
    lowered = note.lower()
    for marker in _RECOVERY_CLAIM_MARKERS:
        if marker in lowered:
            raise ValidationFailedError(
                "Triage notes must describe what was checked or decided — outcome claims "
                f"like '{marker}…' are not permitted: this system records no interventions, "
                "so a recovery cannot be observed (decision log D-030)."
            )


def timeline(db, alert_id) -> list[dict]:
    """Append-only triage history for one alert, oldest first. Empty for alerts
    that have never been triaged — the trail starts with the first action."""
    return AlertEventRepository(db).for_alert(str(alert_id))


def acknowledge(db, alert: dict, *, username: str, role: str, note: Optional[str] = None) -> tuple[dict, dict]:
    """open -> acknowledged. Also validates note honesty: an acknowledgement
    note that claims the lot 'recovered' would be just as unfounded."""
    return _transition(db, alert, STATUS_ACKNOWLEDGED, username, role, note)


def resolve(db, alert: dict, *, username: str, role: str, note: Optional[str] = None) -> tuple[dict, dict]:
    """acknowledged -> resolved (with an optional free-text resolution)."""
    return _transition(db, alert, STATUS_RESOLVED, username, role, note)


def _transition(
    db,
    alert: dict,
    to_status: str,
    username: str,
    role: str,
    note: Optional[str] = None,
) -> tuple[dict, dict]:
    """Validate and apply one lifecycle transition; append its audit event."""
    current = status_of(alert)
    if to_status == current:
        raise ValidationFailedError(
            f"Alert is already '{current}' — a duplicate {to_status} would pollute the audit trail."
        )
    if to_status not in LEGAL_TRANSITIONS[current]:
        allowed = ", ".join(sorted(LEGAL_TRANSITIONS[current])) or "none (resolved is terminal)"
        raise ValidationFailedError(f"Illegal transition {current} -> {to_status}. Allowed from '{current}': {allowed}.")

    assert_note_is_honest(note)

    now = utc_now()
    fields: dict = {"status": to_status}
    if to_status == STATUS_ACKNOWLEDGED:
        fields.update(acknowledged_by=username, acknowledged_at=now)
    else:
        fields.update(resolved_by=username, resolved_at=now)
        if note:
            fields["resolution"] = note

    alert_id = str(alert["_id"])
    AlertRepository(db).update_one({"_id": alert["_id"]}, fields)
    alert.update(fields)

    event = {
        "alert_id": alert_id,
        "from_status": current,
        "to_status": to_status,
        "actor": {"username": username, "role": role},
        "at": now,
        "note": note,
    }
    AlertEventRepository(db).insert_one(dict(event))  # copy: keeps our dict free of the injected _id
    return alert, event
