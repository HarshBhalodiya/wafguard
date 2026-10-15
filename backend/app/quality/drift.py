"""
Drift detection (Phase 7, brief P6): training distribution vs recent data.

Numeric features (sensors, defects): Population Stability Index over 10
quantile bins of the REFERENCE sample, plus the two-sample Kolmogorov-Smirnov
test. Categorical features (recipe, equipment, process step, defect pattern):
PSI over category frequencies (unseen categories get their own bucket).

Interpretation (industry-standard PSI bands, documented constants):
    PSI < 0.10 stable · 0.10-0.25 moderate · >= 0.25 significant
A feature is flagged `drifted` when PSI >= PSI_SIGNIFICANT, or when the KS
p-value < KS_ALPHA and PSI >= PSI_MODERATE (KS alone over-flags at n in the
thousands, where trivial differences are "significant").

The reference window is the production model's training split (first 70% of
lots by process_start); the current window is the most recent `window_days`
of data (default 30) — both selectable by the caller.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import ks_2samp

PSI_MODERATE = 0.10
PSI_SIGNIFICANT = 0.25
KS_ALPHA = 0.01
N_BINS = 10
EPS = 1e-4
MIN_SAMPLES = 30

FEATURE_GROUPS: dict[str, list[str]] = {
    "sensors": [
        "temperature_mean", "pressure_mean", "rf_power_mean", "gas_flow_mean",
        "temperature_std", "pressure_std", "rf_power_std", "gas_flow_std",
        "rf_power_slope", "pressure_slope",
    ],
    "equipment": ["maintenance_days_since_last", "equipment_failure_count", "equipment_prev10_low_yield_rate"],
    "defects": ["defect_density"],
    "categorical": ["recipe_id", "equipment", "process_step", "spatial_signature"],
}


def psi_numeric(reference: np.ndarray, current: np.ndarray, n_bins: int = N_BINS) -> float:
    reference = reference[np.isfinite(reference)]
    current = current[np.isfinite(current)]
    edges = np.unique(np.quantile(reference, np.linspace(0, 1, n_bins + 1)))
    if len(edges) < 2:  # constant reference: any change in value is total drift
        return 0.0 if np.allclose(current, reference[0]) else float("inf")
    edges[0], edges[-1] = -np.inf, np.inf
    ref_frac = np.histogram(reference, edges)[0] / len(reference)
    cur_frac = np.histogram(current, edges)[0] / len(current)
    ref_frac, cur_frac = np.clip(ref_frac, EPS, None), np.clip(cur_frac, EPS, None)
    return float(np.sum((cur_frac - ref_frac) * np.log(cur_frac / ref_frac)))


def psi_categorical(reference: pd.Series, current: pd.Series) -> float:
    categories = sorted(set(reference.astype(str)) | set(current.astype(str)))
    ref = reference.astype(str).value_counts(normalize=True).reindex(categories, fill_value=0.0).to_numpy()
    cur = current.astype(str).value_counts(normalize=True).reindex(categories, fill_value=0.0).to_numpy()
    ref, cur = np.clip(ref, EPS, None), np.clip(cur, EPS, None)
    return float(np.sum((cur - ref) * np.log(cur / ref)))


def _band(psi: float) -> str:
    if psi >= PSI_SIGNIFICANT:
        return "significant"
    if psi >= PSI_MODERATE:
        return "moderate"
    return "stable"


def compare(reference: pd.DataFrame, current: pd.DataFrame, groups: dict[str, list[str]] | None = None) -> dict:
    """Drift report between two frames that share feature columns."""
    groups = groups or FEATURE_GROUPS
    features = []
    for group, columns in groups.items():
        for column in columns:
            if column not in reference.columns or column not in current.columns:
                continue
            ref, cur = reference[column].dropna(), current[column].dropna()
            if len(ref) < MIN_SAMPLES or len(cur) < MIN_SAMPLES:
                features.append({"feature": column, "group": group, "status": "insufficient_data", "n_reference": len(ref), "n_current": len(cur)})
                continue
            if group == "categorical":
                psi, ks_stat, p_value = psi_categorical(ref, cur), None, None
            else:
                ref_v, cur_v = ref.to_numpy(dtype=float), cur.to_numpy(dtype=float)
                psi = psi_numeric(ref_v, cur_v)
                ks = ks_2samp(ref_v, cur_v)
                ks_stat, p_value = float(ks.statistic), float(ks.pvalue)
            drifted = psi >= PSI_SIGNIFICANT or (p_value is not None and p_value < KS_ALPHA and psi >= PSI_MODERATE)
            features.append(
                {
                    "feature": column,
                    "group": group,
                    "psi": round(psi, 4) if np.isfinite(psi) else None,
                    "psi_band": _band(psi),
                    "ks_statistic": None if ks_stat is None else round(ks_stat, 4),
                    "ks_p_value": None if p_value is None else float(f"{p_value:.4g}"),
                    "drifted": bool(drifted),
                    "n_reference": int(len(ref)),
                    "n_current": int(len(cur)),
                    "reference_mean": None if group == "categorical" else round(float(ref.mean()), 4),
                    "current_mean": None if group == "categorical" else round(float(cur.mean()), 4),
                }
            )
    drifted = [f["feature"] for f in features if f.get("drifted")]
    return {
        "n_features": len(features),
        "n_drifted": len(drifted),
        "drifted_features": drifted,
        "overall_status": "drift" if drifted else "stable",
        "features": sorted(features, key=lambda f: -(f.get("psi") or 0.0)),
        "method": {
            "numeric": f"PSI over {N_BINS} reference-quantile bins + two-sample KS",
            "categorical": "PSI over category frequencies",
            "thresholds": {"psi_moderate": PSI_MODERATE, "psi_significant": PSI_SIGNIFICANT, "ks_alpha": KS_ALPHA},
            "flag_rule": "PSI >= significant, or KS p < alpha with PSI >= moderate",
        },
    }


def lot_frame_from_db(db) -> pd.DataFrame:
    """One row per lot: lot fields + its latest prediction's feature snapshot."""
    lots = pd.DataFrame(
        list(
            db.lots.find(
                {},
                {"_id": 0, "lot_id": 1, "process_start": 1, "recipe_id": 1, "equipment": 1, "process_step": 1,
                 "spatial_signature": 1, "defect_density": 1},
            )
        )
    )
    if lots.empty:
        return lots
    snapshots = {}
    for doc in db.predictions.find({}, {"_id": 0, "lot_id": 1, "features_snapshot": 1, "timestamp": 1}).sort("timestamp", 1):
        snapshots[doc["lot_id"]] = doc.get("features_snapshot") or {}
    feature_cols = sorted({c for group in ("sensors", "equipment") for c in FEATURE_GROUPS[group]})
    snap_df = pd.DataFrame(
        [{"lot_id": lot_id, **{c: snap.get(c) for c in feature_cols}} for lot_id, snap in snapshots.items()],
        columns=["lot_id", *feature_cols],
    )
    frame = lots.merge(snap_df, on="lot_id", how="left")
    frame["process_start"] = pd.to_datetime(frame["process_start"], utc=True, format="ISO8601")
    return frame.sort_values("process_start").reset_index(drop=True)


def split_windows(frame: pd.DataFrame, window_days: int = 30, reference_fraction: float = 0.70) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Reference = first `reference_fraction` of lots by time (the training split);
    current = lots in the last `window_days` days of the data."""
    n_ref = int(len(frame) * reference_fraction)
    reference = frame.iloc[:n_ref]
    end = frame["process_start"].max()
    current = frame[frame["process_start"] > end - pd.Timedelta(days=window_days)]
    meta = {
        "reference": {"definition": f"first {reference_fraction:.0%} of lots by process_start (training split)",
                      "start": str(reference["process_start"].min()), "end": str(reference["process_start"].max()), "n": int(len(reference))},
        "current": {"definition": f"last {window_days} days of data",
                    "start": str(current["process_start"].min()), "end": str(current["process_start"].max()), "n": int(len(current))},
    }
    return reference, current, meta


def drift_report_from_db(db, window_days: int = 30) -> dict:
    frame = lot_frame_from_db(db)
    if frame.empty:
        return {"overall_status": "no_data", "features": [], "n_features": 0, "n_drifted": 0, "drifted_features": []}
    reference, current, meta = split_windows(frame, window_days=window_days)
    return {**compare(reference, current), "windows": meta}
