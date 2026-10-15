"""
Feature engineering for the synthetic lot-yield model ("Model B").

Temporal-safety rules (spec section 11), enforced here:
  - previous_lot_yield / previous_5_lot_yield / equipment_failure_count /
    maintenance_days_since_last are computed using ONLY records that
    happened strictly before the lot's own process_start.
  - The synthetic generator's internal `defect_probability` column (which
    the target `yield` is directly derived from) is never included as a
    feature — that would be leaking the label, not engineering a feature.
  - A lot's *own* in-process sensor readings (rf_power_mean, etc.) ARE
    used, which is intentional and not leakage: in-situ sensor telemetry
    is available in real time as the tool runs, strictly before the
    end-of-line yield measurement that produces the target. What's
    excluded is any *other* lot's future data and any direct encoding of
    this lot's own outcome.

`FEATURE_COLUMNS` (numeric) + `CATEGORICAL_COLUMNS` (one-hot expanded) is
the single source of truth for what Model B consumes — training and
runtime inference both call `build_lot_features_bulk` /
`build_single_lot_features` so they can never drift apart.
"""
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from app.features.tabular import (  # noqa: F401  (re-exported for callers)
    EXTENDED_NUMERIC_FEATURE_COLUMNS,
    equipment_context_features,
    sensor_shape_features,
)

SENSOR_TRACKED_PARAMS = ["temperature", "rf_power", "pressure", "gas_flow"]
DIRECT_PARAMS = ["etch_time", "voltage", "current"]
CATEGORICAL_COLUMNS = ["equipment", "process_step"]

NUMERIC_FEATURE_COLUMNS = (
    [f"{p}_mean" for p in SENSOR_TRACKED_PARAMS]
    + [f"{p}_max" for p in SENSOR_TRACKED_PARAMS]
    + [f"{p}_std" for p in SENSOR_TRACKED_PARAMS]
    + [f"{p}_drift" for p in SENSOR_TRACKED_PARAMS]
    + DIRECT_PARAMS
    + ["previous_lot_yield", "previous_5_lot_yield", "equipment_failure_count", "maintenance_days_since_last"]
    + list(EXTENDED_NUMERIC_FEATURE_COLUMNS)  # Phase 2A extensions (D-016)
)


def _sensor_aggregates(sensor_df: pd.DataFrame) -> pd.DataFrame:
    """One row per lot_id with {param}_mean/max/std/drift columns."""
    frames = []
    for param in SENSOR_TRACKED_PARAMS:
        sub = sensor_df[sensor_df.parameter == param].sort_values(["lot_id", "reading_index"])
        grouped = sub.groupby("lot_id")["value"]
        agg = grouped.agg(mean="mean", max="max", std="std").reset_index()
        drift = sub.groupby("lot_id")["value"].agg(lambda s: s.iloc[-1] - s.iloc[0]).reset_index(name="drift")
        merged = agg.merge(drift, on="lot_id")
        merged = merged.rename(columns={c: f"{param}_{c}" for c in ["mean", "max", "std", "drift"]})
        frames.append(merged.set_index("lot_id"))
    result = pd.concat(frames, axis=1).fillna(0.0)
    return result


def _equipment_history_features(lots_df: pd.DataFrame, maintenance_df: pd.DataFrame) -> pd.DataFrame:
    """previous_lot_yield / previous_5_lot_yield / equipment_failure_count / maintenance_days_since_last, per lot."""
    lots_sorted = lots_df.sort_values("process_start").copy()
    lots_sorted["process_start"] = pd.to_datetime(lots_sorted["process_start"])

    lots_sorted["previous_lot_yield"] = lots_sorted.groupby("equipment")["yield"].shift(1)

    # Per-group rolling mean of the *shifted* series, so only strictly-earlier
    # lots on the same equipment ever contribute (no leakage of the current row).
    prev5 = []
    for _, group in lots_sorted.groupby("equipment"):
        shifted = group["yield"].shift(1)
        prev5.append(shifted.rolling(5, min_periods=1).mean())
    lots_sorted["previous_5_lot_yield"] = pd.concat(prev5).reindex(lots_sorted.index)

    maintenance_df = maintenance_df.copy()
    maintenance_df["timestamp"] = pd.to_datetime(maintenance_df["timestamp"])
    failure_events = maintenance_df[maintenance_df.event_type == "unscheduled_repair"]

    failure_counts, days_since = [], []
    for _, lot in lots_sorted.iterrows():
        cutoff = lot["process_start"]
        eq = lot["equipment"]
        prior_failures = failure_events[(failure_events.equipment_id == eq) & (failure_events.timestamp < cutoff)]
        failure_counts.append(len(prior_failures))

        prior_maint = maintenance_df[(maintenance_df.equipment_id == eq) & (maintenance_df.timestamp < cutoff)]
        if len(prior_maint) == 0:
            days_since.append(999.0)  # "never serviced (in our window)" sentinel
        else:
            last = prior_maint["timestamp"].max()
            days_since.append((cutoff - last).total_seconds() / 86400)

    lots_sorted["equipment_failure_count"] = failure_counts
    lots_sorted["maintenance_days_since_last"] = days_since

    fill_values = {
        "previous_lot_yield": lots_sorted["yield"].median(),
        "previous_5_lot_yield": lots_sorted["yield"].median(),
    }
    lots_sorted = lots_sorted.fillna(fill_values)
    return lots_sorted.set_index("lot_id")[
        ["previous_lot_yield", "previous_5_lot_yield", "equipment_failure_count", "maintenance_days_since_last"]
    ]


def build_lot_features_bulk(
    lots_df: pd.DataFrame,
    process_df: pd.DataFrame,
    sensor_df: pd.DataFrame,
    maintenance_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Returns one row per lot: lot_id, process_start, equipment, process_step,
    all NUMERIC_FEATURE_COLUMNS, plus targets `yield` and `is_low_yield`.
    Sorted chronologically by process_start (callers rely on this for splits).
    """
    lots_df = lots_df.copy()
    lots_df["process_start"] = pd.to_datetime(lots_df["process_start"])

    sensor_features = _sensor_aggregates(sensor_df)
    history_features = _equipment_history_features(lots_df, maintenance_df)
    direct_params = process_df.set_index("lot_id")[DIRECT_PARAMS]
    shape_features = sensor_shape_features(sensor_df)
    context_features = equipment_context_features(lots_df)

    features = (
        lots_df.set_index("lot_id")
        .join(sensor_features)
        .join(direct_params)
        .join(history_features)
        .join(shape_features)
        .join(context_features)
    )
    features = features.reset_index().sort_values("process_start").reset_index(drop=True)

    keep_cols = ["lot_id", "process_start", "equipment", "process_step", "yield", "is_low_yield"] + NUMERIC_FEATURE_COLUMNS
    return features[keep_cols]


def to_model_matrix(features_df: pd.DataFrame, known_dummy_columns: Optional[list] = None) -> tuple[pd.DataFrame, list]:
    """One-hot encodes CATEGORICAL_COLUMNS. If `known_dummy_columns` is given (inference time),
    reindexes to exactly those columns so the matrix always matches what the model was trained on."""
    dummies = pd.get_dummies(features_df[CATEGORICAL_COLUMNS], prefix=CATEGORICAL_COLUMNS)
    numeric = features_df[NUMERIC_FEATURE_COLUMNS].astype(float)
    matrix = pd.concat([numeric, dummies], axis=1)
    if known_dummy_columns is not None:
        matrix = matrix.reindex(columns=known_dummy_columns, fill_value=0.0)
    all_columns = list(matrix.columns)
    return matrix, all_columns


def single_row_to_model_matrix(feature_snapshot: dict, feature_columns: list) -> pd.DataFrame:
    """
    Builds a 1-row DataFrame matching `feature_columns` exactly (the column
    list a trained bundle was fit on), from a plain feature dict such as
    the one stored as a lot's `feature_snapshot`. Used at inference time —
    for an existing lot, for a what-if override, or for a hypothetical
    payload — so there's exactly one code path that has to agree with
    training's column layout.
    """
    row = {}
    for col in feature_columns:
        if col in feature_snapshot:
            row[col] = float(feature_snapshot[col])
            continue
        matched = False
        for cat_col in CATEGORICAL_COLUMNS:
            prefix = f"{cat_col}_"
            if col.startswith(prefix):
                row[col] = 1.0 if feature_snapshot.get(cat_col) == col[len(prefix):] else 0.0
                matched = True
                break
        if not matched:
            row[col] = 0.0
    return pd.DataFrame([row], columns=feature_columns)


def chronological_split(features_df: pd.DataFrame, train_frac: float = 0.70, val_frac: float = 0.15):
    """Assumes features_df is already sorted by process_start ascending."""
    n = len(features_df)
    train_end = int(n * train_frac)
    val_end = int(n * (train_frac + val_frac))
    return (
        features_df.iloc[:train_end].reset_index(drop=True),
        features_df.iloc[train_end:val_end].reset_index(drop=True),
        features_df.iloc[val_end:].reset_index(drop=True),
    )
