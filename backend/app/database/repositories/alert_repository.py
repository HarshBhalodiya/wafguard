from typing import Any, Dict, List, Optional

from app.database.repositories.base_repository import BaseRepository
from app.utils.exceptions import ValidationFailedError

# P8-2: status filter clauses. Legacy rows (raised before Phase 8) carry no
# `status` field — these clauses express each *derived* status in Mongo so the
# filter works without a destructive backfill of the shared cluster (the same
# derived-open default as app.services.alert_triage_service.status_of).
_STATUS_CLAUSES: Dict[str, Dict[str, Any]] = {
    "open": {
        "$or": [
            {"status": "open"},
            {"status": {"$exists": False}, "acknowledged_by": None, "resolution": None},
        ]
    },
    "acknowledged": {
        "$or": [
            {"status": "acknowledged"},
            {"status": {"$exists": False}, "acknowledged_by": {"$ne": None}, "resolution": None},
        ]
    },
    "resolved": {
        "$or": [
            {"status": "resolved"},
            {"status": {"$exists": False}, "resolution": {"$ne": None}},
        ]
    },
}


def status_clause(status: str) -> Dict[str, Any]:
    if status not in _STATUS_CLAUSES:
        raise ValidationFailedError(f"Unknown alert status '{status}'. Valid: {', '.join(_STATUS_CLAUSES)}.")
    return _STATUS_CLAUSES[status]


def _with_alert_id(document: dict) -> dict:
    """Expose Mongo's ObjectId as a string `alert_id` (the addressable id the
    triage and delivery APIs share) before a document goes into a response."""
    document = dict(document)
    document["alert_id"] = str(document.pop("_id", ""))
    return document


class AlertRepository(BaseRepository):
    collection_name = "alerts"

    def recent(self, limit: int = 10, severity: Optional[str] = None, status: Optional[str] = None) -> List[dict]:
        query: Dict[str, Any] = {}
        if severity:
            query["severity"] = severity
        if status:
            query.update(status_clause(status))
        cursor = self.collection.find(query).sort("created_at", -1).limit(limit)
        return [_with_alert_id(doc) for doc in cursor]

    def for_lot(self, lot_id: str) -> List[dict]:
        return self.find_many({"lot_id": lot_id}, sort=[("created_at", -1)])

    def insert(self, document: Dict[str, Any]) -> dict:
        """Insert and return the stored document (with its generated _id) so the
        caller can dispatch/return the actual alert row."""
        self.collection.insert_one(document)
        return document


class AlertDeliveryRepository(BaseRepository):
    collection_name = "alert_deliveries"

    def for_alert(self, alert_id: str) -> List[dict]:
        return self.find_many({"alert_id": alert_id}, sort=[("created_at", -1)])

    def recent(self, limit: int = 20) -> List[dict]:
        return self.find_many({}, sort=[("created_at", -1)], limit=limit)


class AlertEventRepository(BaseRepository):
    """P8-1: append-only triage audit trail (one row per lifecycle transition)."""

    collection_name = "alert_events"

    def for_alert(self, alert_id: str) -> List[dict]:
        return self.find_many({"alert_id": str(alert_id)}, sort=[("at", 1)])


class AlertChannelRepository(BaseRepository):
    collection_name = "alert_channels"

    def by_name(self, name: str) -> Optional[dict]:
        return self.find_one({"name": name})

    def all_enabled(self) -> List[dict]:
        return self.find_many({"enabled": True})
