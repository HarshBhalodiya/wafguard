"""
Evidence-backed recommendations (Phase 5, brief P4).

    recommendation = knowledge-base action  (app/knowledge/engineering_kb.yaml)
                   + historical intervention statistics (Mongo `interventions`)

For a lot, the root-cause engine's top candidates select actions from the KB.
Each action is then scored against the intervention history: every recorded
case where an engineer diagnosed the same cause and took the same action,
strictly BEFORE this lot started (never forward in time). Reported per action:

  * n_similar_cases, n_recovered and recovery_text ("9/12"),
  * avg_yield_change and median_yield_after over those cases,
  * small_sample_warning when n < min_cases_for_confidence (KB, default 10),
  * grounded_in: the root-cause evidence ids that put this cause on the list,
  * case_ids: the intervention records the numbers were computed from.

Ranking: candidate score × Laplace-smoothed recovery rate ((recovered+1)/(n+2)),
so an action with 1/1 recoveries does not outrank one with 30/40.

No LLM is involved anywhere: action text comes only from the KB file, and every
number is a count over stored records. The intervention history is SYNTHETIC
(app/simulator/interventions.py) and every response says so.
"""
from datetime import datetime, timezone
from statistics import mean, median

from app.database.repositories.equipment_repository import EquipmentRepository
from app.database.repositories.lot_repository import LotRepository
from app.database.repositories.rootcause_repository import RootCauseRepository
from app.knowledge import actions_for, load_engineering_kb
from app.services.rootcause_service import compute_root_cause
from app.utils.exceptions import NotFoundError

SYNTHETIC_LABEL = (
    "SYNTHETIC intervention history — recovery statistics come from simulated engineer "
    "actions, not from a real fab. A past recovery after an action is an association, "
    "not proof that the action caused it."
)
MAX_CANDIDATES = 3
MAX_CASE_IDS = 10


def _parse_time(value) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def prior_interventions(db, before) -> list[dict]:
    """Every intervention recorded strictly before `before` (any tool)."""
    cutoff = _parse_time(before)
    return [
        doc
        for doc in db.interventions.find({}, {"_id": 0})
        if doc.get("timestamp") is not None and _parse_time(doc["timestamp"]) < cutoff
    ]


def action_statistics(cases: list[dict], min_cases: int) -> dict:
    """Outcome counts over a set of intervention records."""
    n = len(cases)
    n_recovered = sum(1 for c in cases if c.get("recovered"))
    changes = [float(c["yield_change"]) for c in cases if c.get("yield_change") is not None]
    afters = [float(c["yield_after"]) for c in cases if c.get("yield_after") is not None]
    if n == 0:
        warning = "No recorded case of this action for this cause — no outcome evidence."
    elif n < min_cases:
        warning = f"Small sample: only {n} recorded case(s) (< {min_cases}); treat the rate as weak evidence."
    else:
        warning = None
    return {
        "n_similar_cases": n,
        "n_recovered": n_recovered,
        "recovery_rate": round(n_recovered / n, 4) if n else None,
        "recovery_text": f"{n_recovered}/{n}" if n else "0/0",
        "avg_yield_change": round(mean(changes), 2) if changes else None,
        "median_yield_after": round(median(afters), 2) if afters else None,
        "small_sample_warning": warning,
        "smoothed_recovery_rate": round((n_recovered + 1) / (n + 2), 4),
    }


def _peers(db, equipment_id: str) -> str:
    equipment_repo = EquipmentRepository(db)
    doc = equipment_repo.get_by_equipment_id(equipment_id)
    if not doc:
        return "none identified"
    ids = [p["equipment_id"] for p in equipment_repo.peers(doc["tool_family"], equipment_id)[:2]]
    return ", ".join(ids) if ids else "none identified"


def recommend_for_lot(db, lot_id: str, force_recompute: bool = False) -> dict:
    """Ranked, evidence-backed actions for one lot."""
    lot = LotRepository(db).get_by_lot_id(lot_id)
    if not lot:
        raise NotFoundError(f"Lot {lot_id} was not found.")

    root_cause = None if force_recompute else RootCauseRepository(db).latest_for_lot(lot_id)
    if not root_cause or not all(e.get("evidence_id") for c in root_cause.get("candidates", []) for e in c.get("evidence", [])):
        root_cause = compute_root_cause(db, lot_id)

    candidates = [c for c in root_cause["candidates"] if c["confidence"] != "LOW"][:MAX_CANDIDATES]
    if not candidates:
        candidates = root_cause["candidates"][:1]

    kb = load_engineering_kb()
    min_cases = int(kb.get("min_cases_for_confidence", 10))
    history = prior_interventions(db, lot["process_start"])
    peers = _peers(db, lot["equipment"])

    actions: list[dict] = []
    skipped: list[dict] = []
    for candidate in candidates:
        cause = candidate["candidate"]
        grounded_in = [e["evidence_id"] for e in candidate.get("evidence", []) if e.get("evidence_id")]
        kb_actions = actions_for(cause)
        if not grounded_in or not kb_actions:
            skipped.append(
                {
                    "cause": cause,
                    "reason": "no supporting evidence item" if not grounded_in else "no action for this cause in the knowledge base",
                }
            )
            continue
        for action in kb_actions:
            cases = [c for c in history if c.get("diagnosed_cause") == cause and c.get("action_id") == action["id"]]
            cases.sort(key=lambda c: _parse_time(c["timestamp"]), reverse=True)
            stats = action_statistics(cases, min_cases)
            actions.append(
                {
                    "action_id": action["id"],
                    "text": action["text"].format(equipment=lot["equipment"], peers=peers),
                    "kind": action.get("kind"),
                    "cause": cause,
                    "candidate_score": candidate["score"],
                    "candidate_confidence": candidate["confidence"],
                    "grounded_in": grounded_in,
                    **stats,
                    "ranking_score": round(float(candidate["score"]) * stats["smoothed_recovery_rate"], 4),
                    "case_ids": [c["intervention_id"] for c in cases[:MAX_CASE_IDS]],
                }
            )

    actions.sort(key=lambda a: a["ranking_score"], reverse=True)
    return {
        "lot_id": lot_id,
        "equipment": lot["equipment"],
        "label": SYNTHETIC_LABEL,
        "recommendation_type": "Evidence-backed engineering action (knowledge base + intervention history)",
        "method": (
            "Actions come only from app/knowledge/engineering_kb.yaml. Statistics count recorded interventions "
            "with the same diagnosed cause and action, strictly before this lot started. "
            "Ranking = root-cause candidate score × (recovered + 1) / (cases + 2)."
        ),
        "kb_version": kb.get("version"),
        "min_cases_for_confidence": min_cases,
        "n_prior_interventions": len(history),
        "history_note": None if history else "No intervention history recorded before this lot — no outcome evidence.",
        "actions": actions,
        "skipped_candidates": skipped,
    }
