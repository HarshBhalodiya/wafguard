"""
Case-based statistics (Phase 5, P5-1).

The Phase-0 audit flagged "9/12 similar cases recovered" as the canonical
fake statistic — no such number existed anywhere. This module is the real
counterpart: for a lot, it measures what actually happened to its most
similar *historical* neighbors (same process step, processed strictly
BEFORE this lot — never forward in time) and reports raw counts:

  * how many ended low-yield (< LOW_YIELD_THRESHOLD),
  * how many ended healthy,
  * how many ended healthy DESPITE elevated predicted risk.

There is deliberately **no "recovered after fix" claim**: this dataset
records no intervention/fix events, so a recovery rate could only be
invented. Every number here is a count of measured lot outcomes.
"""
from statistics import median
from typing import Optional

import numpy as np
from sklearn.preprocessing import StandardScaler

from app.config import LOW_YIELD_THRESHOLD
from app.database.repositories.lot_repository import LotRepository
from app.services.similarity_service import SIMILARITY_FEATURE_COLUMNS, _latest_predictions_per_lot
from app.utils.exceptions import NotFoundError

ELEVATED_RISK_PROBABILITY = 0.5  # "elevated" = predicted low-yield probability at/above this


def compute_case_stats(db, lot_id: str, k: int = 12) -> dict:
    """Measured outcomes of the k most similar backward-looking neighbor lots."""
    lot = LotRepository(db).get_by_lot_id(lot_id)
    if not lot:
        raise NotFoundError(f"Lot {lot_id} was not found.")

    before_ts = str(lot["process_start"])
    prior_lots = LotRepository(db).find_many(
        {"process_step": lot["process_step"], "process_start": {"$lt": before_ts}}
    )
    prior_by_id = {l["lot_id"]: l for l in prior_lots}

    predictions = _latest_predictions_per_lot(db)
    query_pred = next((p for p in predictions if p["lot_id"] == lot_id and p.get("features_snapshot")), None)
    pool = [
        p
        for p in predictions
        if p["lot_id"] in prior_by_id and p.get("features_snapshot")
    ]

    empty = {
        "n_similar": 0,
        "low_yield": 0,
        "healthy": 0,
        "healthy_despite_elevated_risk": 0,
        "median_yield": None,
        "neighbors": [],
        "window": "same process step, processed strictly before this lot",
        "source": "lot history (measured outcomes; backward-looking only)",
        "note": "Not enough comparable history to measure outcomes yet.",
    }
    if query_pred is None or len(pool) < 3:
        return empty

    rows, ids = [], []
    for p in pool:
        snap = p["features_snapshot"]
        rows.append([snap.get(col, 0.0) for col in SIMILARITY_FEATURE_COLUMNS])
        ids.append(p["lot_id"])
    query_row = [[query_pred["features_snapshot"].get(col, 0.0) for col in SIMILARITY_FEATURE_COLUMNS]]

    X = np.vstack([np.asarray(rows, dtype=float), np.asarray(query_row, dtype=float)])
    X_scaled = StandardScaler().fit_transform(X)
    query_vec, pool_vec = X_scaled[-1], X_scaled[:-1]

    distances = np.linalg.norm(pool_vec - query_vec, axis=1)
    order = np.argsort(distances)[: min(k, len(ids))]

    neighbors, yields = [], []
    low = healthy = healthy_despite_risk = 0
    for idx in order:
        p = pool[idx]
        final_yield = prior_by_id[ids[idx]].get("yield")
        risk_prob = p.get("risk_probability")
        is_low = final_yield is None or final_yield < LOW_YIELD_THRESHOLD
        elevated = (risk_prob or 0.0) >= ELEVATED_RISK_PROBABILITY
        low += int(is_low)
        healthy += int(not is_low)
        healthy_despite_risk += int((not is_low) and elevated)
        if final_yield is not None:
            yields.append(float(final_yield))
        neighbors.append(
            {
                "lot_id": ids[idx],
                "yield": final_yield,
                "risk_level": p.get("risk_level"),
                "distance": round(float(distances[idx]), 4),
            }
        )

    n = len(neighbors)
    return {
        "n_similar": n,
        "low_yield": low,
        "healthy": healthy,
        "healthy_despite_elevated_risk": healthy_despite_risk,
        "median_yield": round(median(yields), 2) if yields else None,
        "neighbors": neighbors,
        "window": "same process step, processed strictly before this lot",
        "source": "lot history (measured outcomes; backward-looking only)",
        "note": (
            f"Outcomes of the {n} most similar historical lots: {low} ended below the "
            f"{LOW_YIELD_THRESHOLD:.0f}% yield threshold, {healthy} at/above it "
            f"({healthy_despite_risk} of those despite elevated predicted risk). "
            "Counts of measured outcomes — not evidence that any intervention worked."
        ),
    }
