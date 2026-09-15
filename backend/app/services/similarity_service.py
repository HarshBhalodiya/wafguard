"""
Historical similarity search (spec section 21).

Nearest-neighbor retrieval over standardized process-parameter feature
vectors using scikit-learn. Deliberately simple for the MVP scale
(hundreds of lots, fit fresh per request) — the deep-research report
explicitly frames this as "extensible to vector databases later", so the
function boundary here is the thing worth keeping stable, not the
implementation inside it.
"""
import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

from app.database.repositories.lot_repository import LotRepository
from app.database.repositories.prediction_repository import PredictionRepository
from app.utils.exceptions import NotFoundError

# Deliberately narrower than the model's full NUMERIC_FEATURE_COLUMNS: this
# matches the spec's own similarity example (comparing RF Power/Temperature/
# Pressure directly) and keeps the distance metric meaningful. Folding in
# equipment-history features too would dilute genuine process-parameter
# closeness with dimensions that aren't about "does this lot look like that
# one", and pushes distances up simply from having more axes.
SIMILARITY_FEATURE_COLUMNS = [
    "temperature_mean",
    "pressure_mean",
    "rf_power_mean",
    "gas_flow_mean",
    "etch_time",
    "voltage",
    "current",
]


def _latest_predictions_per_lot(db) -> list[dict]:
    pipeline = [
        {"$sort": {"timestamp": -1}},
        {"$group": {"_id": "$lot_id", "doc": {"$first": "$$ROOT"}}},
        {"$replaceRoot": {"newRoot": "$doc"}},
    ]
    return list(db.predictions.aggregate(pipeline))


def find_similar_lots(db, lot_id: str, k: int = 5) -> dict:
    lot_repo = LotRepository(db)
    lot = lot_repo.get_by_lot_id(lot_id)
    if not lot:
        raise NotFoundError(f"Lot {lot_id} was not found.")

    # Restrict the comparison pool to the same process step: comparing an
    # ETCH lot's RF power to a LITHO lot's isn't meaningful even when the raw
    # numbers happen to land close together.
    predictions = _latest_predictions_per_lot(db)
    predictions = [
        p for p in predictions if p.get("features_snapshot") and p["features_snapshot"].get("process_step") == lot.get("process_step")
    ]
    if len(predictions) < 2:
        raise NotFoundError("Not enough predicted lots on this process step yet to compute similarity. Run scripts/train_yield_model.py first.")

    rows, lot_ids = [], []
    for p in predictions:
        snap = p["features_snapshot"]
        rows.append([snap.get(col, 0.0) for col in SIMILARITY_FEATURE_COLUMNS])
        lot_ids.append(p["lot_id"])

    if lot_id not in lot_ids:
        raise NotFoundError(f"Lot {lot_id} has no prediction yet — nothing to compare it against.")

    X = np.array(rows, dtype=float)
    X_scaled = StandardScaler().fit_transform(X)

    target_idx = lot_ids.index(lot_id)
    n_neighbors = min(k + 1, len(X_scaled))
    nn = NearestNeighbors(n_neighbors=n_neighbors).fit(X_scaled)
    distances, indices = nn.kneighbors(X_scaled[target_idx : target_idx + 1])

    lots_by_id = {l["lot_id"]: l for l in lot_repo.find_many({"lot_id": {"$in": lot_ids}})}

    similar = []
    for dist, idx in zip(distances[0], indices[0]):
        candidate_lot_id = lot_ids[idx]
        if candidate_lot_id == lot_id:
            continue
        candidate_lot = lots_by_id.get(candidate_lot_id, {})
        similar.append(
            {
                "lot_id": candidate_lot_id,
                "similarity_score": round(1 / (1 + dist), 4),
                "yield": candidate_lot.get("yield"),
                "equipment": candidate_lot.get("equipment"),
                "process_step": candidate_lot.get("process_step"),
                "risk_level": candidate_lot.get("risk_level"),
            }
        )
        if len(similar) >= k:
            break

    low_yield_among_similar = sum(1 for s in similar if (s["yield"] or 100) < 85)

    return {
        "lot_id": lot_id,
        "current_process_parameters": lot.get("process_parameters"),
        "similar_lots": similar,
        "insight": (
            f"{low_yield_among_similar} of the {len(similar)} most similar historical lots experienced low yield."
            if similar
            else "No comparable historical lots found."
        ),
    }
