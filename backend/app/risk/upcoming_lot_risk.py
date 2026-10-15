"""
Upcoming-lot risk engine (brief PHASE 4 / P3).

Scores a lot **before it runs**. The brief's hard requirement is that only
information existing *before* the planned start may be used, so this module
is built around one rule: every lookup carries `process_start < planned_start`.

Inputs (all pre-run by construction):
  * the tool's most recent completed run strictly before `planned_start`, whose
    in-run sensor aggregates stand in for the planned run's unknown sensors
    (the proxy — labelled as such in the response),
  * the planned recipe: its process step drives the model's `process_step`
    input, and the recipe's own prior outcomes are reported as a factor,
  * the tool's state **as of `planned_start`**: every history feature the model
    reads (previous yields, prior low-yield rate, days since maintenance,
    failure count, tool age) is recomputed from rows strictly before the
    planned start — never copied from the proxy lot, whose own history ends
    one run earlier,
  * the equipment document's state (health, status, anomaly flag),
  * historical lots similar to that last known state: the candidate pool AND
    the feature scaler are restricted to lots strictly before `planned_start`.

Deliberately NOT used: the planned lot's own telemetry (it does not exist yet),
its outcome, and any row at or after `planned_start`. `tests/test_upcoming_lot_risk.py`
proves this by mutating every lot (and every maintenance event) at or after the
planned start and asserting the score does not move.

The estimate is model-based, not a guarantee — the response carries a `label`
saying so, and the proxy note spells out what the model was actually fed.
"""
from datetime import datetime, timezone

import numpy as np
from sklearn.preprocessing import StandardScaler

from app.config import LOW_YIELD_THRESHOLD
from app.database.repositories.equipment_repository import EquipmentRepository
from app.database.repositories.lot_repository import LotRepository
from app.database.repositories.prediction_repository import PredictionRepository
from app.services import yield_service
from app.services.similarity_service import SIMILARITY_FEATURE_COLUMNS, _latest_predictions_per_lot
from app.utils.exceptions import NotFoundError, ValidationFailedError

PROXY_NOTE = (
    "The planned run has no sensors yet, so the model is fed the tool's most recent "
    "completed run strictly before the planned start (last known state) plus the tool's "
    "state at that moment. This estimates tool-state risk — it is not a measurement of "
    "the un-run lot."
)
LABEL = "Pre-run model estimate — not a guarantee, and not a measurement of the un-run lot."
DEFAULT_PRIOR_YIELD_WINDOW = 10
DEFAULT_SIMILAR_LOTS = 5

# The scalar components of risk_service.compute_risk's breakdown. The breakdown dict
# also carries nested `weights` / `weighted_contributions` maps, which are not factors.
SCALAR_RISK_COMPONENTS = (
    "model_probability",
    "parameter_deviation",
    "equipment_risk",
    "historical_defect_risk",
)


def parse_time(value) -> datetime:
    """Accept a datetime (incl. pandas Timestamp) or an ISO string; return a tz-aware datetime.

    Naive values are treated as UTC: pymongo hands BSON dates back naive, while the
    generator writes tz-aware ISO strings, and comparing the two raises TypeError."""
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def dataset_now(db) -> datetime:
    """"Now" for this dataset: the latest run time it contains.

    Used as the default `planned_start` so a caller can ask "what is about to run?"
    without inventing a clock that does not match the seeded data.
    """
    latest = LotRepository(db).find_many({}, sort=[("process_start", -1)], limit=1)
    if not latest:
        raise ValidationFailedError("No lots in the database — nothing to plan against.")
    return parse_time(latest[0]["process_start"])


def _all_prior_lots(db, equipment_id: str, planned_start: datetime) -> list[dict]:
    """Every lot on this tool strictly BEFORE the planned start, newest first.

    The comparison is done in Python rather than as a Mongo `$lt` because the stored
    type is not guaranteed: the seeders have written `process_start` both as a BSON
    date and as an ISO string, and a mismatched `$lt` silently matches nothing — which
    would look like "this tool never ran before" instead of a bug. Parsing both sides
    with `parse_time` removes the whole class of failure.
    """
    cutoff = parse_time(planned_start)
    priors = [
        lot
        for lot in LotRepository(db).find_many({"equipment": equipment_id})
        if lot.get("process_start") is not None and parse_time(lot["process_start"]) < cutoff
    ]
    priors.sort(key=lambda lot: parse_time(lot["process_start"]), reverse=True)
    return priors


def _prior_lots(db, equipment_id: str, planned_start: datetime, limit: int = DEFAULT_PRIOR_YIELD_WINDOW) -> list[dict]:
    """The newest `limit` prior lots on the tool (see `_all_prior_lots`)."""
    return _all_prior_lots(db, equipment_id, planned_start)[:limit]


def _prior_maintenance(db, equipment_id: str, planned_start: datetime) -> list[dict]:
    """Maintenance events on this tool strictly before the planned start, newest first."""
    cutoff = parse_time(planned_start)
    events = [
        doc
        for doc in db.maintenance.find({"equipment_id": equipment_id}, {"_id": 0})
        if doc.get("timestamp") is not None and parse_time(doc["timestamp"]) < cutoff
    ]
    events.sort(key=lambda doc: parse_time(doc["timestamp"]), reverse=True)
    return events


def _yield_of(lot: dict):
    value = lot.get("yield")
    return None if value is None else float(value)


def state_as_of(db, equipment_id: str, planned_start, prior_lots: list[dict], chamber_id: str | None = None) -> dict:
    """The model's history features for a run on `equipment_id` at `planned_start`.

    Same definitions as training (app/ml/preprocessing.py, app/features/tabular.py),
    evaluated at the planned start instead of at the proxy lot's start, so the
    previous-lot yield IS the proxy lot's yield (training's shift(1)). Every input
    row is strictly before `planned_start`; `prior_lots` must be newest first.
    """
    planned_start = parse_time(planned_start)
    yields = [y for y in (_yield_of(lot) for lot in prior_lots) if y is not None]
    maintenance = _prior_maintenance(db, equipment_id, planned_start)

    if maintenance:
        days_since_pm = (planned_start - parse_time(maintenance[0]["timestamp"])).total_seconds() / 86400.0
    else:
        days_since_pm = 999.0  # training's "never serviced (in our window)" sentinel

    same_chamber = [lot for lot in prior_lots if chamber_id and lot.get("chamber_id") == chamber_id]
    chamber_previous = _yield_of(same_chamber[0]) if same_chamber else None
    first_start = parse_time(prior_lots[-1]["process_start"]) if prior_lots else planned_start

    features = {
        "previous_lot_yield": yields[0] if yields else None,
        "previous_5_lot_yield": float(np.mean(yields[:5])) if yields else None,
        "equipment_prev10_low_yield_rate": (
            float(np.mean([y < LOW_YIELD_THRESHOLD for y in yields[:10]])) if yields else 0.0
        ),
        "chamber_previous_yield": chamber_previous,
        "maintenance_days_since_last": round(days_since_pm, 4),
        "equipment_failure_count": sum(1 for m in maintenance if m.get("event_type") == "unscheduled_repair"),
        "equipment_age_days": round((planned_start - first_start).total_seconds() / 86400.0, 4),
    }
    return {
        "features": features,
        "n_prior_maintenance_events": len(maintenance),
        "last_maintenance": (
            {"timestamp": str(maintenance[0]["timestamp"]), "event_type": maintenance[0].get("event_type")}
            if maintenance
            else None
        ),
    }


def _resolve_step(db, recipe_id: str | None, process_step: str | None, fallback_step: str | None) -> tuple[str | None, str]:
    """Which process step the planned run executes, and where that came from.

    A recipe determines its step (recipes collection). An explicit step that
    contradicts the recipe is refused rather than silently picking one."""
    if recipe_id:
        recipe = db.recipes.find_one({"recipe_id": recipe_id}, {"_id": 0})
        if recipe is None:
            raise NotFoundError(f"Recipe {recipe_id} was not found.")
        recipe_step = recipe.get("process_step")
        if process_step and recipe_step and process_step != recipe_step:
            raise ValidationFailedError(f"Recipe {recipe_id} runs process step {recipe_step}, not {process_step}.")
        return recipe_step or process_step or fallback_step, "planned recipe"
    if process_step:
        return process_step, "request"
    return fallback_step, "last run on the tool (no recipe/step supplied)"


def recipe_history(db, recipe_id: str | None, planned_start) -> dict | None:
    """Outcomes of every earlier lot that ran this recipe (any tool), strictly prior."""
    if not recipe_id:
        return None
    cutoff = parse_time(planned_start)
    yields = [
        float(lot["yield"])
        for lot in LotRepository(db).find_many({"recipe_id": recipe_id})
        if lot.get("yield") is not None
        and lot.get("process_start") is not None
        and parse_time(lot["process_start"]) < cutoff
    ]
    n_low = sum(1 for y in yields if y < LOW_YIELD_THRESHOLD)
    return {
        "recipe_id": recipe_id,
        "n_prior_lots": len(yields),
        "n_low_yield": n_low,
        "low_yield_rate": round(n_low / len(yields), 4) if yields else None,
        "mean_yield": round(float(np.mean(yields)), 2) if yields else None,
    }


def _prior_similar_lots(
    db,
    source_snapshot: dict,
    process_step: str | None,
    planned_start: datetime,
    k: int = DEFAULT_SIMILAR_LOTS,
    predictions: list[dict] | None = None,
) -> tuple[list[dict], str | None]:
    """Historical lots closest to the tool's last known state, using priors ONLY.

    Both the candidate pool and the StandardScaler are fit on lots strictly before
    `planned_start` (same step), so a future lot can neither be cited nor shift the
    distance scale. Returns (matches, note); `note` explains an honest degradation.
    """
    cutoff = parse_time(planned_start)
    lots_by_id = {
        lot["lot_id"]: lot
        for lot in LotRepository(db).find_many({"process_step": process_step} if process_step else {})
        if lot.get("process_start") is not None and parse_time(lot["process_start"]) < cutoff
    }
    predictions = _latest_predictions_per_lot(db) if predictions is None else predictions
    pool = [p for p in predictions if p["lot_id"] in lots_by_id and p.get("features_snapshot")]
    if len(pool) < 2:
        return [], "fewer than 2 strictly-prior lots on this process step — no similarity search"

    rows = np.asarray(
        [[float(p["features_snapshot"].get(col) or 0.0) for col in SIMILARITY_FEATURE_COLUMNS] for p in pool]
    )
    query = np.asarray([[float(source_snapshot.get(col) or 0.0) for col in SIMILARITY_FEATURE_COLUMNS]])
    scaler = StandardScaler().fit(rows)
    distances = np.linalg.norm(scaler.transform(rows) - scaler.transform(query), axis=1)

    keep: list[dict] = []
    for idx in np.argsort(distances, kind="stable")[:k]:
        lot = lots_by_id[pool[idx]["lot_id"]]
        lot_yield = _yield_of(lot)
        keep.append(
            {
                "lot_id": lot["lot_id"],
                "similarity_score": round(float(1.0 / (1.0 + distances[idx])), 4),
                "space": "parameter",
                "yield": lot_yield,
                "is_low_yield": lot_yield is not None and lot_yield < LOW_YIELD_THRESHOLD,
                "equipment": lot.get("equipment"),
                "process_start": str(lot["process_start"]),
            }
        )
    return keep, None


def _top_factors(feature_snapshot: dict, breakdown: dict, top_n: int = 3, recipe_stats: dict | None = None) -> list[dict]:
    """Ranked drivers of the estimate: the risk blend's own components (ranked by their
    weighted contribution), plus the model's SHAP attributions, plus the planned
    recipe's prior outcomes. SHAP failing must not fail the estimate, and the
    breakdown's nested `weights`/`weighted_contributions` entries are not factors."""
    weighted = breakdown.get("weighted_contributions") or {}
    weights = breakdown.get("weights") or {}
    components = [
        {
            "factor": name,
            "value": round(float(breakdown[name]), 4),
            "weight": weights.get(name),
            "contribution": round(float(weighted.get(name, 0.0)), 4),
            "source": "risk blend",
        }
        for name in SCALAR_RISK_COMPONENTS
        if isinstance(breakdown.get(name), (int, float))
    ]
    components.sort(key=lambda item: -abs(item["contribution"]))
    factors = components[:top_n]
    try:
        for contribution in yield_service.explain_lot(feature_snapshot, top_n=top_n):
            factors.append(
                {
                    "factor": contribution.feature,
                    "value": round(float(contribution.shap_value), 4),
                    "source": "model attribution (SHAP)",
                }
            )
    except Exception:  # pragma: no cover - attribution is best-effort, never load-bearing
        factors.append({"factor": "shap_attribution", "value": None, "source": "unavailable"})
    if recipe_stats and recipe_stats.get("n_prior_lots"):
        factors.append(
            {
                "factor": f"recipe {recipe_stats['recipe_id']} prior low-yield rate",
                "value": recipe_stats["low_yield_rate"],
                "n": recipe_stats["n_prior_lots"],
                "source": "recipe history (strictly prior lots; reported, not a model input)",
            }
        )
    return factors


def score_planned_run(
    db,
    equipment_id: str,
    planned_start,
    product: str | None = None,
    recipe_id: str | None = None,
    process_step: str | None = None,
    predictions: list[dict] | None = None,
) -> dict:
    """Pre-run risk for one planned run on `equipment_id` at `planned_start`.

    `predictions` (latest prediction per lot) may be passed in by a caller that scores
    many tools at once, so the prediction table is read once instead of once per tool."""
    planned_start = parse_time(planned_start)

    equipment_doc = EquipmentRepository(db).get_by_equipment_id(equipment_id)
    if not equipment_doc:
        raise NotFoundError(f"Equipment {equipment_id} was not found.")

    all_priors = _all_prior_lots(db, equipment_id, planned_start)
    prior_lots = all_priors[:DEFAULT_PRIOR_YIELD_WINDOW]
    if not prior_lots:
        raise ValidationFailedError(
            f"No completed run on {equipment_id} before {planned_start.isoformat()} — a pre-run estimate "
            "needs at least one prior run on the tool to know its last state."
        )

    source_lot = prior_lots[0]
    prediction = PredictionRepository(db).latest_for_lot(source_lot["lot_id"])
    if not prediction:
        raise ValidationFailedError(
            f"Prior run {source_lot['lot_id']} has no feature snapshot, so no pre-run estimate can be built. "
            "Run scripts/train_yield_model.py to backfill predictions."
        )

    step, step_source = _resolve_step(db, recipe_id, process_step, source_lot.get("process_step"))
    state = state_as_of(db, equipment_id, planned_start, all_priors, chamber_id=source_lot.get("chamber_id"))

    # Proxy = the last run's in-run sensor aggregates; everything history-based is
    # replaced by its as-of-planned-start value, and the step is the planned one.
    source_snapshot = dict(prediction["features_snapshot"])
    feature_snapshot = dict(source_snapshot)
    feature_snapshot.pop("model_probability", None)
    for name, value in state["features"].items():
        if value is not None:
            feature_snapshot[name] = value
    feature_snapshot["equipment"] = equipment_id
    if step:
        feature_snapshot["process_step"] = step

    prior_yields = [lot["yield"] for lot in prior_lots if lot.get("yield") is not None]
    scored = yield_service.predict_lot(feature_snapshot, equipment_doc, prior_yields)
    recipe_stats = recipe_history(db, recipe_id, planned_start)
    similar_lots, similar_note = _prior_similar_lots(db, source_snapshot, step, planned_start, predictions=predictions)

    classifier_metadata = yield_service.get_bundle(yield_service.MODEL_B_CLASSIFIER).get("metadata") or {}
    calibration = (classifier_metadata.get("metrics") or {}).get("calibration") or {}

    return {
        "equipment_id": equipment_id,
        "process_step": step,
        "process_step_source": step_source,
        "product": product or source_lot.get("product"),
        "recipe_id": recipe_id or source_lot.get("recipe_id"),
        "planned_start": planned_start.isoformat(),
        "label": LABEL,
        "proxy_note": PROXY_NOTE,
        "predicted_yield": scored["predicted_yield"],
        # Calibrated P(low yield) straight from the classifier (isotonic on the
        # validation split) — distinct from the blended 0-1 risk score below.
        "probability_low_yield": scored["model_probability"],
        "probability_calibration": calibration.get("method"),
        "risk_probability": scored["risk_probability"],
        "risk_level": scored["risk_level"],
        "risk_breakdown": scored["risk_breakdown"],
        "top_factors": _top_factors(feature_snapshot, scored["risk_breakdown"], recipe_stats=recipe_stats),
        "equipment_state": {
            **state["features"],
            "health_score": equipment_doc.get("health_score"),
            "status": equipment_doc.get("status"),
            "anomaly_status": equipment_doc.get("anomaly_status"),
            "n_prior_maintenance_events": state["n_prior_maintenance_events"],
            "last_maintenance": state["last_maintenance"],
        },
        "recipe_history": recipe_stats,
        "similar_lots": similar_lots,
        "similar_lots_note": similar_note,
        "model_version": scored["model_version"],
        "low_yield_threshold": LOW_YIELD_THRESHOLD,
        "sources": {
            "state_source_lot_id": source_lot["lot_id"],
            "state_source_process_start": str(source_lot["process_start"]),
            "n_prior_lots_on_tool": len(all_priors),
            "prior_yield_window": len(prior_yields),
            "as_of": planned_start.isoformat(),
            "cutoff": "every input row has process_start < planned_start",
            "history_features": "recomputed as of planned_start (not copied from the proxy lot)",
        },
    }


def upcoming_lot_risks(db, limit: int = 20, planned_start=None) -> dict:
    """Risk-sorted "what is about to run" list, one hypothetical next run per tool.

    The synthetic dataset contains no scheduled lots, so the queue is derived rather
    than read: each tool's next run is planned at `planned_start` (default: the latest
    run time in the dataset, i.e. "now" for this dataset) and scored from that tool's
    state at that moment. The response says so — it is not a production queue.
    """
    planned_start = dataset_now(db) if planned_start is None else parse_time(planned_start)

    entries: list[dict] = []
    skipped: list[dict] = []
    predictions = _latest_predictions_per_lot(db)  # read once for every tool below
    for equipment_doc in EquipmentRepository(db).all():
        equipment_id = equipment_doc["equipment_id"]
        try:
            entries.append(score_planned_run(db, equipment_id, planned_start, predictions=predictions))
        except (ValidationFailedError, NotFoundError) as exc:
            # A tool with no prior run cannot be estimated — reported, not faked.
            skipped.append({"equipment_id": equipment_id, "reason": str(exc)})

    entries.sort(key=lambda entry: entry["risk_probability"], reverse=True)
    for entry in entries:
        entry["main_reason"] = _main_reason(entry)
    return {
        "planned_start": planned_start.isoformat(),
        "label": LABEL,
        "queue_note": (
            "Derived queue: this dataset has no scheduled lots, so one hypothetical next run per tool was "
            "scored from that tool's state at `planned_start`. It is not a production dispatch list."
        ),
        "n_tools_scored": len(entries),
        "n_tools_skipped": len(skipped),
        "skipped": skipped,
        "lots": entries[: max(1, limit)],
    }


def _main_reason(entry: dict) -> str:
    """One-line reason for the queue table: the largest weighted risk component,
    plus the top SHAP feature when one is available."""
    blend = [f for f in entry["top_factors"] if f.get("source") == "risk blend"]
    shap = [f for f in entry["top_factors"] if f.get("source") == "model attribution (SHAP)" and f.get("value")]
    parts = []
    if blend:
        parts.append(f"{blend[0]['factor'].replace('_', ' ')} ({blend[0]['value']:.2f})")
    if shap:
        top = max(shap, key=lambda f: abs(f["value"]))
        parts.append(f"top model driver: {top['factor']}")
    return "; ".join(parts) or "no dominant factor"
