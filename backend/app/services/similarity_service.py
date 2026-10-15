"""
Historical similarity search (spec section 21), upgraded in Phase 3 (P3-5).

Two retrieval paths coexist:

  * `find_similar_lots` — the original parameter-space kNN over standardized
    process-parameter feature vectors (7 scalars). Behavior unchanged; the
    old /api/similar/{lot_id} endpoint keeps serving it.

  * `find_similar_multi` — the new multi-space search exposing THREE spaces,
    each match tagged with the space it came from:
      - "parameter": the original scaler+kNN path (fit per request over 7
        scalars — measured 4.2 ms steady at 5,000 lots, so the per-request
        refit stays; an ANN index would add a dependency for a sub-5ms call),
      - "temporal":  cosine search over the Phase 2 temporal embeddings
        (models/artifacts/temporal_embeddings.parquet, 64-d, one vector per
        lot; matrices are L2-normalized and cached by artifact stamp —
        measured 1.4 ms steady at 5,000 lots, Phase 5 measured this and
        decided brute force beats an ANN index at this scale),
      - "vision":    cosine search over the Phase 2 wafer-map embeddings
        (models/artifacts/wafer_embeddings.npz, 64-d — covers only the lots
        in the vision split; coverage is reported, never implied; measured
        0.12 ms steady).

All spaces restrict the comparison pool to the same process step (comparing
an ETCH lot's RF power or embedding to a LITHO lot's isn't meaningful), and
report `coverage` so a caller can see how much of the fab each space can
actually search. Missing artifacts degrade to `available: false` with a
reason — never to fake matches.
"""
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

from app.config import get_settings
from app.database.repositories.lot_repository import LotRepository
from app.database.repositories.prediction_repository import PredictionRepository
from app.utils.exceptions import NotFoundError
from app.utils.logging import get_logger

logger = get_logger(__name__)

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

EMBEDDING_SPACES = ("parameter", "temporal", "vision")
TEMPORAL_EMBEDDING_FILE = "temporal_embeddings.parquet"
VISION_EMBEDDING_FILE = "wafer_embeddings.npz"


def _latest_predictions_per_lot(db) -> list[dict]:
    pipeline = [
        {"$sort": {"timestamp": -1}},
        {"$group": {"_id": "$lot_id", "doc": {"$first": "$$ROOT"}}},
        {"$replaceRoot": {"newRoot": "$doc"}},
    ]
    return list(db.predictions.aggregate(pipeline))


def _match_fields(candidate_lot: dict, score: float, space: str) -> dict:
    return {
        "lot_id": candidate_lot.get("lot_id"),
        "similarity_score": round(float(score), 4),
        "yield": candidate_lot.get("yield"),
        "equipment": candidate_lot.get("equipment"),
        "process_step": candidate_lot.get("process_step"),
        "risk_level": candidate_lot.get("risk_level"),
        "space": space,
    }


def _parameter_neighbors(db, lot: dict, k: int) -> list[dict]:
    """Original scaler+kNN path (parameter space). Returns k matches, never the query itself."""
    predictions = _latest_predictions_per_lot(db)
    predictions = [
        p
        for p in predictions
        if p.get("features_snapshot") and p["features_snapshot"].get("process_step") == lot.get("process_step")
    ]
    if len(predictions) < 2:
        raise NotFoundError("Not enough predicted lots on this process step yet to compute similarity. Run scripts/train_yield_model.py first.")

    rows, lot_ids = [], []
    for p in predictions:
        snap = p["features_snapshot"]
        rows.append([snap.get(col, 0.0) for col in SIMILARITY_FEATURE_COLUMNS])
        lot_ids.append(p["lot_id"])

    if lot["lot_id"] not in lot_ids:
        raise NotFoundError(f"Lot {lot['lot_id']} has no prediction yet — nothing to compare it against.")

    X = np.array(rows, dtype=float)
    X_scaled = StandardScaler().fit_transform(X)

    target_idx = lot_ids.index(lot["lot_id"])
    n_neighbors = min(k + 1, len(X_scaled))
    nn = NearestNeighbors(n_neighbors=n_neighbors).fit(X_scaled)
    distances, indices = nn.kneighbors(X_scaled[target_idx : target_idx + 1])

    lots_by_id = {l["lot_id"]: l for l in LotRepository(db).find_many({"lot_id": {"$in": lot_ids}})}

    similar = []
    for dist, idx in zip(distances[0], indices[0]):
        candidate_lot_id = lot_ids[idx]
        if candidate_lot_id == lot["lot_id"]:
            continue
        candidate_lot = lots_by_id.get(candidate_lot_id, {})
        similar.append(_match_fields(candidate_lot, 1 / (1 + dist), "parameter"))
        if len(similar) >= k:
            break
    return similar


def find_similar_lots(db, lot_id: str, k: int = 5) -> dict:
    lot_repo = LotRepository(db)
    lot = lot_repo.get_by_lot_id(lot_id)
    if not lot:
        raise NotFoundError(f"Lot {lot_id} was not found.")

    # Restrict the comparison pool to the same process step: comparing an
    # ETCH lot's RF power to a LITHO lot's isn't meaningful even when the raw
    # numbers happen to land close together.
    similar = _parameter_neighbors(db, lot, k)

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


# --------------------------------------------------------------------------------------
# Embedding-backed spaces (P3-5)
# --------------------------------------------------------------------------------------
def _artifact_stamp(path: Path) -> Optional[tuple]:
    try:
        stat = path.stat()
        return (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None


@lru_cache(maxsize=4)
def _load_temporal_embeddings_cached(stamp: tuple) -> tuple[list[str], np.ndarray]:
    path = Path(stamp[0])
    df = pd.read_parquet(path)
    emb_cols = [c for c in df.columns if c.startswith("emb_")]
    ids = df["lot_id"].astype(str).tolist()
    matrix = df[emb_cols].to_numpy(dtype=float)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.where(norms > 1e-12, norms, 1.0)
    return ids, matrix


@lru_cache(maxsize=4)
def _load_vision_embeddings_cached(stamp: tuple) -> tuple[list[str], np.ndarray]:
    path = Path(stamp[0])
    data = np.load(path, allow_pickle=True)
    ids = [str(x) for x in data["lot_ids"].tolist()]
    matrix = data["embeddings"].astype(float)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.where(norms > 1e-12, norms, 1.0)
    return ids, matrix


def _load_embeddings(kind: str) -> Optional[tuple[list[str], np.ndarray]]:
    """L2-normalized (lot_ids, matrix) for an embedding space, or None when the artifact
    hasn't been built. Cached by (path, mtime, size) so retraining picks up fresh vectors."""
    settings = get_settings()
    filename = TEMPORAL_EMBEDDING_FILE if kind == "temporal" else VISION_EMBEDDING_FILE
    path = settings.resolve_path(settings.model_dir) / filename
    stamp = _artifact_stamp(path)
    if stamp is None:
        return None
    loader = _load_temporal_embeddings_cached if kind == "temporal" else _load_vision_embeddings_cached
    return loader(stamp)


def _cosine_matches(
    query_id: str,
    ids: list[str],
    matrix: np.ndarray,
    pool_ids: set[str],
    k: int,
    space: str,
    lots_by_id: dict[str, dict],
) -> tuple[list[dict], Optional[str]]:
    """Top-k cosine matches inside `pool_ids` (same process step), excluding the query.
    Score = (1 + cosine) / 2 mapped into the 0..1 similarity contract; raw cosine kept."""
    query_positions = [i for i, lid in enumerate(ids) if lid == query_id]
    if not query_positions:
        return [], "query lot has no embedding in this space"
    pool_positions = [i for i, lid in enumerate(ids) if lid in pool_ids and lid != query_id]
    if not pool_positions:
        return [], "no same-step lots have embeddings in this space"

    sims = matrix[pool_positions] @ matrix[query_positions[0]]
    order = np.argsort(-sims)[:k]
    matches = []
    for idx in order:
        lot_id = ids[pool_positions[idx]]
        cosine = float(sims[idx])
        match = _match_fields(lots_by_id.get(lot_id, {"lot_id": lot_id}), (1.0 + cosine) / 2.0, space)
        match["cosine"] = round(cosine, 4)
        matches.append(match)
    return matches, None


def find_similar_multi(db, lot_id: str, k: int = 5, spaces: tuple[str, ...] = EMBEDDING_SPACES) -> dict:
    """Multi-space similarity (new API path, P3-5). Every space reports `available`,
    `coverage`, and either `matches` (each tagged with its `space`) or a `reason`."""
    lot = LotRepository(db).get_by_lot_id(lot_id)
    if not lot:
        raise NotFoundError(f"Lot {lot_id} was not found.")
    unknown = [s for s in spaces if s not in EMBEDDING_SPACES]
    if unknown:
        raise NotFoundError(f"Unknown similarity space(s): {', '.join(unknown)}. Valid: {', '.join(EMBEDDING_SPACES)}.")

    step = lot.get("process_step")
    same_step_lots = LotRepository(db).find_many({"process_step": step})
    lots_by_id = {l["lot_id"]: l for l in same_step_lots}
    pool_ids = set(lots_by_id)
    pool_ids.discard(lot_id)
    n_same_step = len(pool_ids)

    result_spaces: dict[str, dict] = {}

    if "parameter" in spaces:
        try:
            matches = _parameter_neighbors(db, lot, k)
            result_spaces["parameter"] = {
                "available": True,
                "description": "Standardized process-parameter kNN (7 scalars), fit over same-step lots.",
                "coverage": {"pool_lots": len(matches), "same_step_lots_total": n_same_step},
                "matches": matches,
            }
        except NotFoundError as exc:
            result_spaces["parameter"] = {"available": False, "reason": str(exc), "matches": []}

    for space in ("temporal", "vision"):
        if space not in spaces:
            continue
        embeddings = _load_embeddings(space)
        if embeddings is None:
            result_spaces[space] = {
                "available": False,
                "reason": f"artifact {TEMPORAL_EMBEDDING_FILE if space == 'temporal' else VISION_EMBEDDING_FILE} not built yet",
                "matches": [],
            }
            continue
        ids, matrix = embeddings
        matches, reason = _cosine_matches(lot_id, ids, matrix, pool_ids, k, space, lots_by_id)
        covered_same_step = sum(1 for lid in ids if lid in pool_ids)
        available = reason is None and bool(matches)
        entry = {
            "available": available,
            "description": (
                "Cosine search over the Phase 2 temporal sequence embeddings (64-d)."
                if space == "temporal"
                else "Cosine search over stored wafer-map CNN embeddings (64-d)."
            ),
            "coverage": {
                "lots_with_embedding": len(ids),
                "same_step_pool": covered_same_step,
                "same_step_lots_total": n_same_step,
                "coverage_note": (
                    f"{covered_same_step} of {n_same_step} same-step lots have stored {space} embeddings."
                ),
            },
            "matches": matches,
        }
        if reason:
            entry["reason"] = reason
        result_spaces[space] = entry

    return {"lot_id": lot_id, "k": k, "spaces": result_spaces}
