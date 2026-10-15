"""
Risk scoring engine (spec section 12/18).

    risk_score = w1*model_probability + w2*parameter_deviation
               + w3*equipment_risk + w4*historical_defect_risk

Weights live in app.config.RISK_WEIGHTS (not hard-coded here) so the
methodology stays explicit and tunable. This module is pure scoring logic
— it takes already-fetched data in and returns numbers out; DB lookups
belong to the caller (yield_service), which keeps this easy to unit test.
"""
from typing import Optional

from app.config import LOW_YIELD_THRESHOLD, PROCESS_PARAMETER_RANGES, RISK_THRESHOLDS, RISK_WEIGHTS


def risk_level_from_score(score_0_100: float) -> str:
    if score_0_100 <= RISK_THRESHOLDS["low_max"]:
        return "LOW"
    if score_0_100 <= RISK_THRESHOLDS["medium_max"]:
        return "MEDIUM"
    return "HIGH"


def raw_parameter_deviation(name: str, value: Optional[float]) -> float:
    """0 at the band center, 1.0 exactly at the band edge, >1 beyond it. Single source of
    truth for "how abnormal is this one reading", reused by the risk score and by the
    root-cause engine's per-candidate deviation evidence."""
    if value is None or name not in PROCESS_PARAMETER_RANGES:
        return 0.0
    lo, hi = PROCESS_PARAMETER_RANGES[name]
    center, half_width = (lo + hi) / 2, (hi - lo) / 2
    return abs(value - center) / half_width


def compute_parameter_deviation_score(process_parameters: dict) -> float:
    """
    0-1: how abnormal the *worst* tracked parameter is right now. Uses the
    max (not average) because in practice one drifting parameter is
    usually what actually drives risk, not a mild wobble across all of them.

    Accepts both key styles so callers with different snapshot shapes work:
    v1-style scalars ("rf_power") and the v2 telemetry aggregates
    ("rf_power_mean" / "rf_power_max"). For each channel the worst available
    reading (mean or in-run max) drives the deviation, so a run that drifted
    out of spec by its end scores even if its lot-level mean stayed mild.
    """
    deviations = []
    for name in PROCESS_PARAMETER_RANGES:
        candidates = [
            v
            for v in (
                process_parameters.get(name),
                process_parameters.get(f"{name}_mean"),
                process_parameters.get(f"{name}_max"),
            )
            if v is not None
        ]
        if not candidates:
            continue
        deviations.append(max(raw_parameter_deviation(name, v) for v in candidates))

    if not deviations:
        return 0.0
    worst = max(deviations)
    return max(0.0, min(1.0, (worst - 0.3) / 1.7))


def compute_equipment_risk_score(equipment_doc: Optional[dict]) -> float:
    if not equipment_doc:
        return 0.0
    health_score = equipment_doc.get("health_score", 100.0)
    score = max(0.0, min(1.0, (100.0 - health_score) / 100.0))
    if equipment_doc.get("anomaly_status"):
        score = min(1.0, score + 0.15)
    return score


def compute_historical_defect_risk(recent_lot_yields: list[float], low_yield_threshold: float = LOW_YIELD_THRESHOLD) -> float:
    """Fraction of an equipment's recent lots that came in under the low-yield threshold."""
    if not recent_lot_yields:
        return 0.0
    low = sum(1 for y in recent_lot_yields if y < low_yield_threshold)
    return low / len(recent_lot_yields)


def compute_risk(
    model_probability: float,
    process_parameters: dict,
    equipment_doc: Optional[dict],
    historical_defect_risk: float,
) -> dict:
    parameter_deviation = compute_parameter_deviation_score(process_parameters)
    equipment_risk = compute_equipment_risk_score(equipment_doc)

    weighted = {
        "model_probability": RISK_WEIGHTS["model_probability"] * model_probability,
        "parameter_deviation": RISK_WEIGHTS["parameter_deviation"] * parameter_deviation,
        "equipment_risk": RISK_WEIGHTS["equipment_risk"] * equipment_risk,
        "historical_defect_risk": RISK_WEIGHTS["historical_defect_risk"] * historical_defect_risk,
    }
    score_0_100 = max(0.0, min(100.0, sum(weighted.values()) * 100))

    return {
        "risk_probability": round(score_0_100 / 100, 4),
        "risk_level": risk_level_from_score(score_0_100),
        "breakdown": {
            "model_probability": round(model_probability, 4),
            "parameter_deviation": round(parameter_deviation, 4),
            "equipment_risk": round(equipment_risk, 4),
            "historical_defect_risk": round(historical_defect_risk, 4),
            "weights": RISK_WEIGHTS,
            "weighted_contributions": {k: round(v, 4) for k, v in weighted.items()},
        },
    }
