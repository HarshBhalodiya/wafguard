"""
Recommendation engine (spec section 15).

Converts the top root-cause candidate(s) into numbered, entity-populated
engineering investigation steps. Deliberately framed as "suggested
engineering investigation", never as an instruction to change a recipe or
process parameter (spec section 15/22) — and it never writes anything to
equipment/process state, only to the `recommendations` collection.
"""
from app.database.repositories.equipment_repository import EquipmentRepository
from app.database.repositories.lot_repository import LotRepository
from app.database.repositories.prediction_repository import PredictionRepository
from app.database.repositories.recommendation_repository import RecommendationRepository
from app.database.repositories.rootcause_repository import RootCauseRepository
from app.services.rootcause_service import compute_root_cause
from app.utils.exceptions import NotFoundError
from app.utils.time import utc_now

RECOMMENDATION_TEMPLATES = {
    "RF Power": [
        "Inspect the RF generator on {equipment}.",
        "Verify RF match network calibration against manufacturer spec.",
        "Compare {equipment} against peer chamber(s): {peers}.",
        "Review recent maintenance events on {equipment}.",
        "Consider routing a verification lot through {equipment} before releasing this lot.",
    ],
    "Temperature": [
        "Inspect heater/thermocouple calibration on {equipment}.",
        "Compare temperature control loop against peer chamber(s): {peers}.",
        "Review recent maintenance events on {equipment}.",
    ],
    "Pressure": [
        "Inspect chamber pressure control (throttle valve, pump) on {equipment}.",
        "Compare pressure control against peer chamber(s): {peers}.",
    ],
    "Gas Flow": [
        "Inspect mass flow controllers on {equipment}.",
        "Verify gas line integrity and calibration.",
    ],
    "Etch Time": [
        "Review recipe timing settings for the current process step on {equipment}.",
    ],
    "Voltage": [
        "Inspect power supply stability on {equipment}.",
    ],
    "Current": [
        "Inspect current draw stability on {equipment}.",
    ],
    "Equipment Yield History": [
        "Review {equipment}'s recent lot history for a recurring pattern.",
        "Escalate to equipment engineering for a full chamber health review.",
    ],
    "Equipment Failure History": [
        "Review recent unscheduled-repair events on {equipment}.",
        "Consider a preventive-maintenance cycle for {equipment}.",
    ],
    "Maintenance Overdue": [
        "Schedule an overdue preventive-maintenance/calibration check on {equipment}.",
    ],
}


def _peer_list(equipment_repo: EquipmentRepository, equipment_doc: dict | None) -> str:
    if not equipment_doc:
        return "none identified"
    peers = equipment_repo.peers(equipment_doc["tool_family"], equipment_doc["equipment_id"])
    ids = [p["equipment_id"] for p in peers[:2]]
    return ", ".join(ids) if ids else "none identified"


def generate_recommendations(db, lot_id: str, force_recompute: bool = False) -> dict:
    lot_repo = LotRepository(db)
    equipment_repo = EquipmentRepository(db)
    prediction_repo = PredictionRepository(db)
    rootcause_repo = RootCauseRepository(db)

    lot = lot_repo.get_by_lot_id(lot_id)
    if not lot:
        raise NotFoundError(f"Lot {lot_id} was not found.")

    root_cause = None if force_recompute else rootcause_repo.latest_for_lot(lot_id)
    if not root_cause:
        root_cause = compute_root_cause(db, lot_id)

    prediction = prediction_repo.latest_for_lot(lot_id)
    equipment_doc = equipment_repo.get_by_equipment_id(lot["equipment"])
    top_candidates = [c for c in root_cause["candidates"] if c["confidence"] != "LOW"][:2] or root_cause["candidates"][:1]

    steps: list[str] = []
    referenced_candidates: list[str] = []
    for candidate in top_candidates:
        name = candidate["candidate"]
        templates = RECOMMENDATION_TEMPLATES.get(name, ["Investigate {equipment} for factors related to " + name.lower() + "."])
        referenced_candidates.append(name)
        for template in templates:
            step = template.format(
                equipment=lot["equipment"],
                peers=_peer_list(equipment_repo, equipment_doc),
            )
            if step not in steps:
                steps.append(step)

    numbered_steps = [f"{i + 1}. {step}" for i, step in enumerate(steps)]

    top_evidence_label = None
    if top_candidates and top_candidates[0]["evidence"]:
        top_evidence_label = top_candidates[0]["evidence"][0]["label"]

    result = {
        "lot_id": lot_id,
        "evidence": top_evidence_label or "No single dominant factor detected — parameters are within normal ranges.",
        "prediction": (
            f"Low-yield risk is {prediction['risk_probability'] * 100:.0f}% ({prediction['risk_level']})."
            if prediction
            else "No prediction available yet."
        ),
        "recommendation_type": "Suggested engineering investigation",
        "recommendations": numbered_steps,
        "referenced_candidates": referenced_candidates,
        "timestamp": utc_now(),
    }

    RecommendationRepository(db).upsert_for_lot(lot_id, result)
    return result
