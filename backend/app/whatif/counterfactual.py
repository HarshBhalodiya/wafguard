"""
Counterfactual engine (Phase 6, brief P5).

    current lot -> requested change -> new prediction -> yield difference -> uncertainty

Kept alongside — not replacing — the original simulator (app/services/
simulation_service.py, POST /api/what-if). Differences:

  * Changes are validated against app/whatif/limits.json: a value outside the
    recipe window or the physical limit is BLOCKED with a 422, never predicted.
  * Every number is a range: the bootstrap ensemble's P10/P50/P90 for yield
    before, after and for the per-member difference, plus the split-conformal
    80% interval (coverage measured on the test split, reported with it).
  * The response says, in the brief's words, that this is a model-based
    counterfactual and not proof.

How a sensor change is applied: the lot's measured run is shifted by
delta = new_level - observed mean, i.e. `{p}_mean` and `{p}_max` both move by
delta; the run's shape (std, drift, slope, excursions, time outside spec) is
held at its observed values and listed in `held_fixed`. That is a modelling
choice, stated in the response, not a physical claim about how the tool would
behave at the new setpoint.
"""
import json
from functools import lru_cache
from pathlib import Path

import numpy as np

from app.config import get_settings
from app.database.repositories.equipment_repository import EquipmentRepository
from app.database.repositories.lot_repository import LotRepository
from app.database.repositories.prediction_repository import PredictionRepository
from app.ml.preprocessing import single_row_to_model_matrix
from app.services import yield_service
from app.utils.exceptions import ModelUnavailableError, NotFoundError, ValidationFailedError
from app.whatif.ensemble import INTERVAL_QUANTILES, load_ensemble, member_predictions

LABEL = "Model-based counterfactual. Not proof that this change will produce this result."
LIMITS_FILE = Path(__file__).resolve().parent / "limits.json"
SHAPE_SUFFIXES = ("std", "drift", "slope", "excursions", "time_outside_pct")


@lru_cache(maxsize=1)
def load_limits() -> dict:
    with open(LIMITS_FILE, encoding="utf-8") as f:
        return json.load(f)


def validate_changes(changes: dict) -> None:
    """Raise ValidationFailedError for unknown parameters or impossible values."""
    if not changes:
        raise ValidationFailedError("No change requested — supply at least one parameter.")
    limits = load_limits()
    params = limits["parameters"]
    problems = []
    for name, value in changes.items():
        if name in limits.get("not_overridable", {}):
            problems.append(f"{name}: {limits['not_overridable'][name]}")
            continue
        spec = params.get(name)
        if spec is None:
            problems.append(f"{name}: unknown parameter (allowed: {sorted(params)})")
            continue
        if value is None or not np.isfinite(value):
            problems.append(f"{name}: value must be a finite number")
            continue
        p_lo, p_hi = spec["physical"]
        r_lo, r_hi = spec["recipe_window"]
        if not p_lo <= value <= p_hi:
            problems.append(f"{name}={value} {spec['unit']} is outside the physical limit [{p_lo}, {p_hi}]")
        elif not r_lo <= value <= r_hi:
            problems.append(f"{name}={value} {spec['unit']} is outside the recipe window [{r_lo}, {r_hi}]")
    if problems:
        raise ValidationFailedError("Change blocked: " + "; ".join(problems))


def apply_changes(snapshot: dict, changes: dict) -> tuple[dict, list[str]]:
    """Return (new snapshot, held-fixed feature names). Pure function."""
    params = load_limits()["parameters"]
    after = dict(snapshot)
    held_fixed: list[str] = []
    for name, value in changes.items():
        spec = params[name]
        if spec["kind"] == "sensor_level":
            observed = float(after.get(f"{name}_mean") or 0.0)
            delta = float(value) - observed
            after[f"{name}_mean"] = float(value)
            if f"{name}_max" in after and after[f"{name}_max"] is not None:
                after[f"{name}_max"] = float(after[f"{name}_max"]) + delta
            held_fixed += [f"{name}_{s}" for s in SHAPE_SUFFIXES if f"{name}_{s}" in after]
        else:
            after[spec["feature"]] = float(value)
    return after, held_fixed


def _quantiles(values: np.ndarray) -> dict:
    lo, hi = INTERVAL_QUANTILES
    return {
        "p10": round(float(np.quantile(values, lo)), 2),
        "p50": round(float(np.median(values)), 2),
        "p90": round(float(np.quantile(values, hi)), 2),
    }


def _extrapolation_warnings(bundle, snapshot_after: dict, changes: dict) -> list[str]:
    params = load_limits()["parameters"]
    warnings = []
    for name in changes:
        feature = params[name]["feature"]
        rng = bundle.feature_ranges.get(feature)
        value = snapshot_after.get(feature)
        if rng and value is not None and not rng[0] <= float(value) <= rng[1]:
            warnings.append(
                f"{feature}={value:.4g} is outside the range seen in training [{rng[0]:.4g}, {rng[1]:.4g}] — "
                "the model is extrapolating; treat the range as unreliable."
            )
    return warnings


def run_counterfactual(db, lot_id: str, changes: dict) -> dict:
    validate_changes(changes)

    lot = LotRepository(db).get_by_lot_id(lot_id)
    if not lot:
        raise NotFoundError(f"Lot {lot_id} was not found.")
    prediction = PredictionRepository(db).latest_for_lot(lot_id)
    if not prediction:
        raise NotFoundError(f"No prediction found for lot {lot_id} yet — nothing to compare against.")

    settings = get_settings()
    bundle = load_ensemble(settings.resolve_path(settings.model_dir))
    if bundle is None:
        raise ModelUnavailableError(
            "The what-if uncertainty ensemble has not been trained yet. Run "
            "'python scripts/train_whatif_ensemble.py' from backend/ first."
        )

    before = dict(prediction["features_snapshot"])
    before.pop("model_probability", None)
    after, held_fixed = apply_changes(before, changes)

    X_before = single_row_to_model_matrix(before, bundle.feature_columns)
    X_after = single_row_to_model_matrix(after, bundle.feature_columns)
    members_before = member_predictions(bundle, X_before)[:, 0]
    members_after = member_predictions(bundle, X_after)[:, 0]
    deltas = members_after - members_before

    q_lo, q_hi = bundle.residual_quantiles
    median_before, median_after = float(np.median(members_before)), float(np.median(members_after))

    equipment_doc = EquipmentRepository(db).get_by_equipment_id(lot["equipment"])
    recent = yield_service.get_recent_equipment_yields(db, lot["equipment"], exclude_lot_id=lot_id)
    prod_before = yield_service.predict_lot(before, equipment_doc, recent)
    prod_after = yield_service.predict_lot(after, equipment_doc, recent)

    agree = float(np.mean(np.sign(deltas) == np.sign(np.median(deltas)))) if np.median(deltas) != 0 else None
    params = load_limits()["parameters"]
    return {
        "lot_id": lot_id,
        "label": LABEL,
        "changes": [
            {
                "parameter": name,
                "unit": params[name]["unit"],
                "observed": before.get(params[name]["feature"]),
                "requested": float(value),
                "recipe_window": params[name]["recipe_window"],
                "physical_limit": params[name]["physical"],
            }
            for name, value in changes.items()
        ],
        "held_fixed": held_fixed,
        "yield_before": {
            **_quantiles(members_before),
            "interval_80": [round(median_before + q_lo, 2), round(median_before + q_hi, 2)],
        },
        "yield_after": {
            **_quantiles(members_after),
            "interval_80": [round(median_after + q_lo, 2), round(median_after + q_hi, 2)],
        },
        "yield_difference": {
            **_quantiles(deltas),
            "members_agreeing_on_direction": None if agree is None else round(agree, 3),
        },
        "probability_low_yield": {
            "before": prod_before["model_probability"],
            "after": prod_after["model_probability"],
            "source": "production calibrated classifier (point estimate)",
        },
        "risk_level": {"before": prod_before["risk_level"], "after": prod_after["risk_level"]},
        "warnings": _extrapolation_warnings(bundle, after, changes),
        "uncertainty_method": {
            "ensemble": f"{len(bundle.members)} bootstrap XGBoost regressors (P10-P90 = model uncertainty)",
            "interval_80": "split-conformal: ensemble median + validation-residual quantiles",
            "measured_test_coverage_of_interval_80": bundle.metrics["conformal_interval"]["test_coverage"],
            "ensemble_version": bundle.version,
        },
        "persisted": False,
    }
