"""
Recommendation engine (spec section 15).

Converts the top root-cause candidate(s) into numbered, entity-populated
engineering investigation steps. Deliberately framed as "suggested
engineering investigation", never as an instruction to change a recipe or
process parameter (spec section 15/22) — and it never writes anything to
equipment/process state, only to the `recommendations` collection.

Phase 3 grounding (P3-6): every rendered step is also emitted as a
`recommendation_items` entry carrying `grounded_in` — the `evidence_id`s
of the root-cause evidence items that justified it. A candidate with zero
evidence items is skipped, so orphan suggestions (suggestions no evidence
col supports) cannot be produced. The legacy `recommendations` list of
numbered strings is kept unchanged for existing UI/tests.
"""
import json
from functools import lru_cache
from pathlib import Path

from app.database.repositories.equipment_repository import EquipmentRepository
from app.database.repositories.lot_repository import LotRepository
from app.database.repositories.prediction_repository import PredictionRepository
from app.database.repositories.recommendation_repository import RecommendationRepository
from app.database.repositories.rootcause_repository import RootCauseRepository
from app.services import case_statistics
from app.services.rootcause_service import compute_root_cause
from app.utils.exceptions import NotFoundError
from app.utils.time import utc_now

# Phase 5 (P5-1): templates moved to a documented data file (authored knowledge
# base, per spec section 15). Case COUNTS are never stored there — they are
# measured at render time from lot history (app/services/case_statistics.py).
TEMPLATES_FILE = Path(__file__).resolve().parent.parent / "knowledge" / "recommendation_templates.json"


@lru_cache(maxsize=1)
def load_templates() -> dict:
    with open(TEMPLATES_FILE, encoding="utf-8") as f:
        return json.load(f)["templates"]


RECOMMENDATION_TEMPLATES = load_templates()


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
    if root_cause and not _has_evidence_ids(root_cause):
        # Cached result predates evidence ids (Phase 3) — recompute so grounding is possible.
        root_cause = None
    if not root_cause:
        root_cause = compute_root_cause(db, lot_id)

    prediction = prediction_repo.latest_for_lot(lot_id)
    equipment_doc = equipment_repo.get_by_equipment_id(lot["equipment"])
    top_candidates = [c for c in root_cause["candidates"] if c["confidence"] != "LOW"][:2] or root_cause["candidates"][:1]

    # P5-1: measured outcomes of the lot's similar historical neighbors
    # (backward-looking). Attached to every item and to the result — counts of
    # what happened, never a "recovered after fix" claim.
    case_stats = case_statistics.compute_case_stats(db, lot_id)

    steps: list[str] = []
    referenced_candidates: list[str] = []
    recommendation_items: list[dict] = []
    for candidate in top_candidates:
        name = candidate["candidate"]
        grounded_in = [e["evidence_id"] for e in candidate.get("evidence", []) if e.get("evidence_id")]
        if not grounded_in:
            # No evidence supports this candidate right now — no orphan suggestions (P3-6).
            continue
        templates = RECOMMENDATION_TEMPLATES.get(name, ["Investigate {equipment} for factors related to " + name.lower() + "."])
        referenced_candidates.append(name)
        for template in templates:
            step = template.format(
                equipment=lot["equipment"],
                peers=_peer_list(equipment_repo, equipment_doc),
            )
            if step in steps:
                continue
            steps.append(step)
            recommendation_items.append(
                {
                    "id": f"rec:{lot_id}:{len(recommendation_items)}",
                    "text": step,
                    "candidate": name,
                    "grounded_in": list(grounded_in),
                    "case_statistics": case_stats,
                }
            )

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
        "recommendation_items": recommendation_items,
        "referenced_candidates": referenced_candidates,
        "case_statistics": case_stats,
        "timestamp": utc_now(),
    }

    RecommendationRepository(db).upsert_for_lot(lot_id, result)
    return result


def _has_evidence_ids(root_cause: dict) -> bool:
    """False only for pre-Phase-3 cached results: evidence items exist but carry no
    evidence_id. A result with no evidence at all is a valid current shape (nothing
    to ground in yet), not a legacy one."""
    items = [e for c in root_cause.get("candidates", []) for e in c.get("evidence", [])]
    return all(e.get("evidence_id") for e in items)
