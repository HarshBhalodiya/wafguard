"""
Model monitoring (Phase 7, brief P6): daily snapshots of how the deployed
risk/yield models behave and — once yield labels arrive — how right they were.

For every calendar day (UTC, by process_start) a snapshot records:

  prediction distribution   n_predicted, mean / p90 calibrated P(low yield),
                            risk-level counts, mean predicted yield
  labelled accuracy         n_labelled, yield MAE, precision / recall of the
                            "flagged" decision at the classifier's own threshold
  false alarms              flagged but yield >= threshold
  missed low-yield lots     yield < threshold but not flagged
  calibration               ECE over the trailing CALIBRATION_WINDOW_DAYS
                            (a single day holds too few lots for a stable ECE)

"Flagged" = calibrated P(low yield) >= the decision threshold stored in the
classifier's metadata (chosen on the validation split at training time).

Snapshots are stored in Mongo `monitoring_snapshots` (one document per day,
upserted, so re-running is idempotent). This dataset is historical, so the
snapshots are back-filled day by day; in production the same function runs
once a day on the previous day's lots.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app.config import LOW_YIELD_THRESHOLD

CALIBRATION_WINDOW_DAYS = 14
ECE_BINS = 10
DEFAULT_DECISION_THRESHOLD = 0.5


def expected_calibration_error(y_true: np.ndarray, proba: np.ndarray, n_bins: int = ECE_BINS) -> float | None:
    if len(y_true) == 0:
        return None
    bins = np.minimum((proba * n_bins).astype(int), n_bins - 1)
    ece = 0.0
    for b in range(n_bins):
        mask = bins == b
        if mask.any():
            ece += mask.mean() * abs(proba[mask].mean() - y_true[mask].mean())
    return float(ece)


def monitoring_frame_from_db(db) -> pd.DataFrame:
    """One row per predicted lot: process_start, probability, predicted & actual yield, risk level."""
    lots = {
        doc["lot_id"]: doc
        for doc in db.lots.find({}, {"_id": 0, "lot_id": 1, "process_start": 1, "yield": 1, "status": 1})
    }
    rows = []
    for doc in db.predictions.find({}, {"_id": 0, "lot_id": 1, "predicted_yield": 1, "risk_level": 1, "features_snapshot": 1, "timestamp": 1}).sort("timestamp", 1):
        lot = lots.get(doc["lot_id"])
        if not lot:
            continue
        rows.append(
            {
                "lot_id": doc["lot_id"],
                "process_start": lot["process_start"],
                "probability": (doc.get("features_snapshot") or {}).get("model_probability"),
                "predicted_yield": doc.get("predicted_yield"),
                "risk_level": doc.get("risk_level"),
                "actual_yield": lot.get("yield"),
            }
        )
    frame = pd.DataFrame(rows, columns=["lot_id", "process_start", "probability", "predicted_yield", "risk_level", "actual_yield"])
    if frame.empty:
        return frame
    frame = frame.drop_duplicates("lot_id", keep="last")
    frame["process_start"] = pd.to_datetime(frame["process_start"], utc=True, format="ISO8601")
    frame["day"] = frame["process_start"].dt.strftime("%Y-%m-%d")
    return frame.sort_values("process_start").reset_index(drop=True)


def _day_snapshot(day_df: pd.DataFrame, window_df: pd.DataFrame, threshold: float, day: str) -> dict:
    proba = day_df["probability"].astype(float)
    flagged = proba >= threshold
    labelled = day_df[day_df["actual_yield"].notna()]
    is_low = labelled["actual_yield"].astype(float) < LOW_YIELD_THRESHOLD
    lab_flagged = labelled["probability"].astype(float) >= threshold
    tp = int((lab_flagged & is_low).sum())
    fp = int((lab_flagged & ~is_low).sum())
    fn = int((~lab_flagged & is_low).sum())
    win_lab = window_df[window_df["actual_yield"].notna() & window_df["probability"].notna()]
    ece = expected_calibration_error(
        (win_lab["actual_yield"].astype(float) < LOW_YIELD_THRESHOLD).to_numpy(dtype=float),
        win_lab["probability"].astype(float).to_numpy(),
    )
    return {
        "date": day,
        "n_predicted": int(len(day_df)),
        "prediction_distribution": {
            "mean_probability_low_yield": round(float(proba.mean()), 4) if len(proba) else None,
            "p90_probability_low_yield": round(float(proba.quantile(0.9)), 4) if len(proba) else None,
            "n_flagged": int(flagged.sum()),
            "risk_level_counts": {k: int(v) for k, v in day_df["risk_level"].value_counts().to_dict().items()},
            "mean_predicted_yield": round(float(day_df["predicted_yield"].astype(float).mean()), 3) if len(day_df) else None,
        },
        "labelled": {
            "n_labelled": int(len(labelled)),
            "n_low_yield": int(is_low.sum()),
            "yield_mae": round(float((labelled["predicted_yield"].astype(float) - labelled["actual_yield"].astype(float)).abs().mean()), 3)
            if len(labelled) else None,
            "precision": round(tp / (tp + fp), 4) if (tp + fp) else None,
            "recall": round(tp / (tp + fn), 4) if (tp + fn) else None,
            "false_alarms": fp,
            "missed_low_yield": fn,
        },
        "calibration": {
            "ece": None if ece is None else round(ece, 4),
            "window_days": CALIBRATION_WINDOW_DAYS,
            "n_in_window": int(len(win_lab)),
        },
        "decision_threshold": threshold,
    }


def build_daily_snapshots(frame: pd.DataFrame, threshold: float = DEFAULT_DECISION_THRESHOLD, model_version: str | None = None) -> list[dict]:
    if frame.empty:
        return []
    snapshots = []
    created_at = datetime.now(timezone.utc).isoformat()
    for day, day_df in frame.groupby("day", sort=True):
        day_end = pd.Timestamp(day, tz="UTC") + pd.Timedelta(days=1)
        window = frame[(frame["process_start"] < day_end) & (frame["process_start"] >= day_end - pd.Timedelta(days=CALIBRATION_WINDOW_DAYS))]
        snapshot = _day_snapshot(day_df, window, threshold, day)
        snapshot["model_version"] = model_version
        snapshot["created_at"] = created_at
        snapshots.append(snapshot)
    return snapshots


def store_snapshots(db, snapshots: list[dict]) -> int:
    for snapshot in snapshots:
        db.monitoring_snapshots.replace_one({"date": snapshot["date"]}, snapshot, upsert=True)
    return len(snapshots)


def summarize_snapshots(snapshots: list[dict]) -> dict:
    """Totals across the stored days (what the UI panel headlines)."""
    if not snapshots:
        return {"n_days": 0}
    fa = sum(s["labelled"]["false_alarms"] for s in snapshots)
    missed = sum(s["labelled"]["missed_low_yield"] for s in snapshots)
    n_low = sum(s["labelled"]["n_low_yield"] for s in snapshots)
    n_flagged = sum(s["prediction_distribution"]["n_flagged"] for s in snapshots)
    eces = [s["calibration"]["ece"] for s in snapshots if s["calibration"]["ece"] is not None]
    return {
        "n_days": len(snapshots),
        "first_day": snapshots[0]["date"],
        "last_day": snapshots[-1]["date"],
        "n_predicted": sum(s["n_predicted"] for s in snapshots),
        "total_false_alarms": fa,
        "total_missed_low_yield": missed,
        "false_alarm_rate_of_flags": round(fa / n_flagged, 4) if n_flagged else None,
        "miss_rate_of_low_yield": round(missed / n_low, 4) if n_low else None,
        "latest_ece": eces[-1] if eces else None,
    }
