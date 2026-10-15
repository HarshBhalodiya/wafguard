"""
Lot-level SPC control charts (Phase 5, P5-2).

Honest scope: these are **lot-level** charts over each run's telemetry mean
(`{parameter}_mean` from the prediction feature snapshot), not per-reading
SPC on the raw 48-sample series. That granularity is what the risk engine
and the UI work at, and pretending per-reading SPC from lot means would be
exactly the kind of overclaim this project refuses.

Rules (classic Western-Electric style, documented constants):
  * R1 — a point beyond 3σ from the centerline,
  * R2 — 2 of 3 consecutive points beyond 2σ on the same side,
  * R3 — 7 consecutive points trending in the same direction.

The centerline/σ use the tool's OWN prior history only (strictly earlier
lots than the point being judged where possible — here: the chart is built
from the tool's history up to `limit` most-recent lots, and each point's
z-score is against the running mean of the points before it, so no point
is ever judged against its own future).
"""
from typing import Optional

import numpy as np

from app.utils.exceptions import NotFoundError

CONTROL_CHART_PARAMETERS = ("temperature", "pressure", "rf_power", "gas_flow")

MIN_POINTS_FOR_SIGMA = 8          # below this, sigma is unreliable — chart reports points but flags nothing
RULE_R1_Z = 3.0
RULE_R2_Z = 2.0
RULE_R2_WINDOW = 3
RULE_R3_RUN = 7


def _running_z(values: list[float]) -> list[Optional[float]]:
    """z-score of each point against the mean/σ of the points BEFORE it
    (minimum MIN_POINTS_FOR_SIGMA prior points). Strictly backward-looking."""
    zs: list[Optional[float]] = []
    for i, value in enumerate(values):
        prior = values[:i]
        if len(prior) < MIN_POINTS_FOR_SIGMA:
            zs.append(None)
            continue
        sigma = float(np.std(prior, ddof=1))
        mean = float(np.mean(prior))
        if sigma <= 1e-12:
            # Perfectly flat history: a matching point is at the centerline; a
            # DEVIATING point is the sharpest possible breach (sentinel z keeps
            # the value JSON-serializable, unlike inf).
            zs.append(0.0 if abs(value - mean) <= 1e-9 else (99.0 if value > mean else -99.0))
            continue
        zs.append((value - mean) / sigma)
    return zs


def _detect_breaches(points: list[dict]) -> list[dict]:
    breaches: list[dict] = []
    for i, point in enumerate(points):
        z = point["z"]
        if z is None:
            continue
        if abs(z) > RULE_R1_Z:
            breaches.append({"index": i, "lot_id": point["lot_id"], "rule": "R1", "detail": f"beyond 3σ (z={z:+.2f})"})

    for i in range(len(points) - (RULE_R2_WINDOW - 1)):
        window = points[i : i + RULE_R2_WINDOW]
        zs = [p["z"] for p in window]
        if any(z is None for z in zs):
            continue
        above = sum(1 for z in zs if z > RULE_R2_Z)
        below = sum(1 for z in zs if z < -RULE_R2_Z)
        if above >= 2 or below >= 2:
            side = "above" if above >= 2 else "below"
            breaches.append(
                {
                    "index": i + RULE_R2_WINDOW - 1,
                    "lot_id": window[-1]["lot_id"],
                    "rule": "R2",
                    "detail": f"2 of 3 consecutive points beyond 2σ {side} the centerline",
                }
            )

    for i in range(len(points) - (RULE_R3_RUN - 1)):
        window = points[i : i + RULE_R3_RUN]
        deltas = [b["value"] - a["value"] for a, b in zip(window, window[1:])]
        if all(d > 0 for d in deltas) or all(d < 0 for d in deltas):
            direction = "upward" if deltas[0] > 0 else "downward"
            breaches.append(
                {
                    "index": i + RULE_R3_RUN - 1,
                    "lot_id": window[-1]["lot_id"],
                    "rule": "R3",
                    "detail": f"7-point {direction} trend",
                }
            )

    seen = set()
    unique = []
    for breach in breaches:
        key = (breach["index"], breach["rule"])
        if key not in seen:
            seen.add(key)
            unique.append(breach)
    return unique


def compute_control_chart(db, equipment_id: str, parameter: str, limit: int = 100) -> dict:
    if parameter not in CONTROL_CHART_PARAMETERS:
        raise NotFoundError(f"Unknown control-chart parameter '{parameter}'. Valid: {', '.join(CONTROL_CHART_PARAMETERS)}.")

    column = f"{parameter}_mean"
    cursor = (
        db.predictions.aggregate(
            [
                {"$match": {f"features_snapshot.{column}": {"$exists": True}}},
                {"$sort": {"timestamp": 1}},
                {"$group": {"_id": "$lot_id", "doc": {"$last": "$$ROOT"}}},
            ]
        )
    )
    docs = [d for d in cursor if d["doc"].get("features_snapshot", {}).get("equipment") == equipment_id]
    docs = docs[-limit:]
    if not docs:
        raise NotFoundError(f"No scored telemetry for equipment {equipment_id} — run scripts/train_yield_model.py first.")

    values = [float(d["doc"]["features_snapshot"][column]) for d in docs]
    points = [
        {
            "lot_id": d["doc"]["lot_id"],
            "ts": d["doc"].get("timestamp"),
            "value": round(value, 4),
            "z": (round(z, 3) if z is not None else None),
        }
        for d, value, z in zip(docs, values, _running_z(values))
    ]
    breaches = _detect_breaches(points)

    return {
        "equipment_id": equipment_id,
        "parameter": parameter,
        "granularity": "lot-level telemetry means (not per-reading SPC)",
        "n_history": len(points),
        "sigma_reliable": len(points) >= MIN_POINTS_FOR_SIGMA,
        "centerline": round(float(np.mean(values)), 4),
        "sigma": round(float(np.std(values, ddof=1)), 4) if len(values) > 1 else 0.0,
        "ucl": round(float(np.mean(values) + RULE_R1_Z * np.std(values, ddof=1)), 4) if len(values) > 1 else None,
        "lcl": round(float(np.mean(values) - RULE_R1_Z * np.std(values, ddof=1)), 4) if len(values) > 1 else None,
        "rules": ["R1: beyond 3σ", "R2: 2 of 3 beyond 2σ same side", "R3: 7-point trend"],
        "points": points,
        "breaches": breaches,
        "source": "prediction feature snapshots (per-run telemetry means, strictly earlier lots for each point's z)",
    }
