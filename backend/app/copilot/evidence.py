"""
Evidence retrieval for the copilot. Every fact the copilot may state is an
evidence item with a stable id, a type, a source and its values:

    pred:<lot>                 the stored prediction (risk, probability, yield)
    shap:<lot>:<feature>       a model attribution (SHAP) for that prediction
    rc:<lot>:<candidate>       a ranked root-cause candidate (+ its evidence ids)
    evt:<lot>:<n>              a timeline event (excursion, alarm, transition...)
    eq:<equipment>             equipment state at the time of the lot
    maint:<event_id>           the last maintenance event before the lot
    defect:<lot>               inspection pattern + defect density
    sim:<lot>:<other lot>      a similar historical lot and its outcome
    rec:<lot>:<action_id>      an evidence-backed recommended action
    doc:<file>#<section>       a documentation section

Each retriever is independent and failure-tolerant: a retriever that cannot
produce evidence returns nothing and records why in `missing`, so the answer
can say "not enough evidence" instead of guessing.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.config import LOW_YIELD_THRESHOLD
from app.copilot import docs_index
from app.database.repositories.equipment_repository import EquipmentRepository
from app.database.repositories.lot_repository import LotRepository
from app.database.repositories.prediction_repository import PredictionRepository
from app.database.repositories.rootcause_repository import RootCauseRepository
from app.database.repositories.timeline_repository import TimelineEventRepository
from app.utils.logging import get_logger

logger = get_logger(__name__)
EVENT_TYPES_OF_INTEREST = ("PARAMETER_EXCURSION", "EQUIPMENT_ALARM", "RECIPE_TRANSITION")


def _parse_time(value) -> datetime | None:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class EvidenceSet:
    """Ordered evidence items keyed by id, plus a record of what could not be retrieved."""

    def __init__(self) -> None:
        self.items: dict[str, dict] = {}
        self.missing: dict[str, str] = {}

    def add(self, item_id: str, type_: str, source: str, title: str, **values) -> str:
        self.items[item_id] = {"id": item_id, "type": type_, "source": source, "title": title, **values}
        return item_id

    def of_type(self, type_: str) -> list[dict]:
        return [i for i in self.items.values() if i["type"] == type_]

    def has(self, type_: str) -> bool:
        return any(i["type"] == type_ for i in self.items.values())

    def to_list(self) -> list[dict]:
        return list(self.items.values())


def retrieve_prediction(db, lot: dict, ev: EvidenceSet) -> None:
    prediction = PredictionRepository(db).latest_for_lot(lot["lot_id"])
    if not prediction:
        ev.missing["prediction"] = f"no prediction stored for {lot['lot_id']}"
        return
    snapshot = prediction.get("features_snapshot") or {}
    ev.add(
        f"pred:{lot['lot_id']}", "prediction", f"predictions ({prediction.get('model_name')} {prediction.get('model_version')})",
        f"Stored prediction for {lot['lot_id']}",
        risk_level=prediction.get("risk_level"),
        risk_score=prediction.get("risk_probability"),
        probability_low_yield=snapshot.get("model_probability"),
        predicted_yield=prediction.get("predicted_yield"),
        actual_yield=lot.get("yield") if lot.get("status") == "completed" else None,
    )


def retrieve_attributions(db, lot: dict, ev: EvidenceSet, top_n: int = 3) -> None:
    from app.services import yield_service

    prediction = PredictionRepository(db).latest_for_lot(lot["lot_id"])
    if not prediction:
        ev.missing["attribution"] = "no feature snapshot to attribute"
        return
    try:
        contributions = yield_service.explain_lot(prediction["features_snapshot"], top_n=top_n)
    except Exception as exc:  # noqa: BLE001 - a missing model must degrade to "not enough evidence"
        ev.missing["attribution"] = f"SHAP unavailable: {exc}"
        return
    for c in contributions:
        ev.add(
            f"shap:{lot['lot_id']}:{c.feature}", "attribution", "SHAP on lot_yield_classifier",
            f"Model attribution: {c.feature}",
            feature=c.feature, shap_value=round(float(c.shap_value), 4), feature_value=round(float(c.feature_value), 4),
            direction=c.direction,
        )


def retrieve_root_cause(db, lot: dict, ev: EvidenceSet, top_n: int = 3) -> None:
    from app.services import rootcause_service

    result = RootCauseRepository(db).latest_for_lot(lot["lot_id"])
    if not result:
        try:
            result = rootcause_service.compute_root_cause(db, lot["lot_id"])
        except Exception as exc:  # noqa: BLE001
            ev.missing["root_cause"] = f"root-cause engine unavailable: {exc}"
            return
    candidates = result.get("candidates") or []
    if not candidates:
        ev.missing["root_cause"] = "the engine returned no candidates"
        return
    for candidate in candidates[:top_n]:
        ev.add(
            f"rc:{lot['lot_id']}:{candidate['candidate'].lower().replace(' ', '_')}", "root_cause",
            f"multi-signal root-cause engine ({result.get('model_name', 'rootcause')})",
            f"Root-cause candidate: {candidate['candidate']}",
            candidate=candidate["candidate"], score=candidate["score"], confidence=candidate["confidence"],
            supporting_evidence=[e.get("evidence_id") for e in candidate.get("evidence", []) if e.get("evidence_id")],
            top_evidence_label=(candidate.get("evidence") or [{}])[0].get("label"),
        )


def retrieve_timeline(db, lot: dict, ev: EvidenceSet, limit: int = 4) -> None:
    events = [e for e in TimelineEventRepository(db).for_lot(lot["lot_id"]) if e.get("event_type") in EVENT_TYPES_OF_INTEREST]
    for n, event in enumerate(events[:limit]):
        payload = event.get("payload") or {}
        ev.add(
            f"evt:{lot['lot_id']}:{n}", "event", "timeline_events", f"{event['event_type']} during {lot['lot_id']}",
            event_type=event["event_type"], ts=str(event.get("ts")),
            parameter=payload.get("parameter"), value=payload.get("value"), alarm=payload.get("alarm"),
        )
    if not events:
        ev.missing["events"] = "no excursion / alarm / transition events logged for this lot"


def retrieve_equipment(db, lot: dict, ev: EvidenceSet) -> None:
    equipment = EquipmentRepository(db).get_by_equipment_id(lot["equipment"])
    if not equipment:
        ev.missing["equipment"] = f"equipment {lot['equipment']} not found"
        return
    start = _parse_time(lot.get("process_start"))
    prior = [
        m for m in db.maintenance.find({"equipment_id": lot["equipment"]}, {"_id": 0})
        if start is None or (_parse_time(m.get("timestamp")) and _parse_time(m["timestamp"]) < start)
    ]
    prior.sort(key=lambda m: _parse_time(m["timestamp"]), reverse=True)
    days_since = None
    if prior and start is not None:
        days_since = round((start - _parse_time(prior[0]["timestamp"])).total_seconds() / 86400.0, 1)
        ev.add(
            f"maint:{prior[0].get('event_id', 'last')}", "maintenance", "maintenance",
            f"Last maintenance on {lot['equipment']} before {lot['lot_id']}",
            event_type=prior[0].get("event_type"), timestamp=str(prior[0].get("timestamp")),
        )
    ev.add(
        f"eq:{lot['equipment']}", "equipment", "equipment + maintenance",
        f"Equipment state of {lot['equipment']}",
        health_score=equipment.get("health_score"), status=equipment.get("status"),
        days_since_maintenance=days_since, n_prior_maintenance=len(prior),
    )


def retrieve_defects(db, lot: dict, ev: EvidenceSet) -> None:
    if lot.get("spatial_signature") is None and lot.get("defect_density") is None:
        ev.missing["defects"] = "no inspection result stored for this lot"
        return
    ev.add(
        f"defect:{lot['lot_id']}", "defects", "lots (inspection summary, SYNTHETIC)",
        f"Inspection summary for {lot['lot_id']}",
        pattern=lot.get("spatial_signature"), defect_density=lot.get("defect_density"),
    )


def retrieve_similar(db, lot: dict, ev: EvidenceSet, k: int = 3) -> None:
    from app.services import similarity_service

    try:
        result = similarity_service.find_similar_lots(db, lot["lot_id"], k=k)
    except Exception as exc:  # noqa: BLE001
        ev.missing["similar"] = f"similarity unavailable: {exc}"
        return
    for match in result.get("similar_lots", []):
        lot_yield = match.get("yield")
        ev.add(
            f"sim:{lot['lot_id']}:{match['lot_id']}", "similar_lot", "similarity search (parameter space)",
            f"Similar historical lot {match['lot_id']}",
            lot_id=match["lot_id"], similarity=match.get("similarity_score"), lot_yield=lot_yield,
            low_yield=lot_yield is not None and lot_yield < LOW_YIELD_THRESHOLD,
        )
    if not result.get("similar_lots"):
        ev.missing["similar"] = "no comparable historical lots"


def retrieve_recommendations(db, lot: dict, ev: EvidenceSet, top_n: int = 2) -> None:
    from app.recommend import engine

    try:
        result = engine.recommend_for_lot(db, lot["lot_id"])
    except Exception as exc:  # noqa: BLE001
        ev.missing["recommendation"] = f"recommendation engine unavailable: {exc}"
        return
    for action in result.get("actions", [])[:top_n]:
        ev.add(
            f"rec:{lot['lot_id']}:{action['action_id']}", "recommendation",
            "knowledge base + intervention history (SYNTHETIC)", f"Recommended action: {action['action_id']}",
            text=action["text"], cause=action["cause"], recovery_text=action["recovery_text"],
            n_cases=action["n_similar_cases"], avg_yield_change=action["avg_yield_change"],
            small_sample_warning=action["small_sample_warning"],
        )
    if not result.get("actions"):
        ev.missing["recommendation"] = "no evidence-backed action for this lot's candidates"


def retrieve_docs(question: str, ev: EvidenceSet, k: int = 2) -> None:
    for section in docs_index.search(question, k=k):
        ev.add(
            section["id"], "doc", section["file"], section["heading"],
            excerpt=docs_index.first_sentence(section["text"]), score=section["score"],
        )


def find_lot(db, lot_id: str | None) -> dict | None:
    return LotRepository(db).get_by_lot_id(lot_id) if lot_id else None
