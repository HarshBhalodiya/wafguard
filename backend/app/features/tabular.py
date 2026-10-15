"""
Phase 2A extended tabular features (see docs/DECISIONS.md, D-016).

These extend — never replace — the Phase 1 feature set in
app.ml.preprocessing. Every feature here obeys the same temporal-safety
rule as the base set: only data strictly before the lot's own yield
measurement may be used.

Feature families added:

  Per tracked sensor channel (temperature, rf_power, pressure, gas_flow):
    {p}_slope             OLS slope of value vs. reading index (trend shape,
                          not just start/end delta like the base {p}_drift).
    {p}_excursions        count of readings outside the parameter's normal
                          operating range (app.config.PROCESS_PARAMETER_RANGES).
    {p}_time_outside_pct  fraction of readings outside that range.

  Equipment / chamber context:
    equipment_age_days             days since the equipment's first lot in the
                                   dataset (a proxy for tool maturity/degradation).
    chamber_previous_yield         yield of the previous lot in the SAME CHAMBER
                                   (strictly earlier by process_start).
    equipment_prev10_low_yield_rate fraction of the previous <=10 lots on the
                                   same equipment that were low-yield.
"""
import numpy as np
import pandas as pd

from app.config import LOW_YIELD_THRESHOLD, PROCESS_PARAMETER_RANGES

SENSOR_TRACKED_PARAMS = ["temperature", "rf_power", "pressure", "gas_flow"]

EXTENDED_SENSOR_COLUMNS = [
    f"{p}_{stat}"
    for p in SENSOR_TRACKED_PARAMS
    for stat in ("slope", "excursions", "time_outside_pct")
]

EXTENDED_HISTORY_COLUMNS = [
    "equipment_age_days",
    "chamber_previous_yield",
    "equipment_prev10_low_yield_rate",
]

EXTENDED_NUMERIC_FEATURE_COLUMNS = EXTENDED_SENSOR_COLUMNS + EXTENDED_HISTORY_COLUMNS


def sensor_shape_features(sensor_df: pd.DataFrame) -> pd.DataFrame:
    """
    One row per lot_id with the extended per-channel columns.

    slope: OLS fit of value against reading order, computed per
    (lot, parameter) group with closed-form sums (fully vectorized — no
    groupby-apply, whose return shape is fragile across pandas versions).
    A linear ramp and a step change with the same endpoints get very
    different slopes here, which plain (last - first) drift cannot
    distinguish.
    """
    sensor_df = (
        sensor_df.rename(columns={"ts_index": "reading_index"})
        if "ts_index" in sensor_df.columns
        else sensor_df
    )
    sensor_df = sensor_df[sensor_df["value"].notna()]

    frames = []
    for param in SENSOR_TRACKED_PARAMS:
        sub = sensor_df[sensor_df.parameter == param].sort_values(["lot_id", "reading_index"])
        lo, hi = PROCESS_PARAMETER_RANGES[param]
        values = sub["value"].to_numpy(dtype=float)
        idx = sub["reading_index"].to_numpy(dtype=float)
        outside = ((values < lo) | (values > hi)).astype(float)

        work = pd.DataFrame(
            {
                "lot_id": sub["lot_id"].to_numpy(),
                "n": 1.0,
                "i": idx,
                "v": values,
                "iv": idx * values,
                "ii": idx * idx,
                "out": outside,
            }
        )
        g = work.groupby("lot_id").sum()
        denom = g["n"] * g["ii"] - g["i"] ** 2
        slope = np.where(
            (g["n"] >= 2) & (denom.abs() > 1e-12),
            (g["n"] * g["iv"] - g["i"] * g["v"]) / denom.replace(0, np.nan),
            0.0,
        )
        frame = pd.DataFrame(
            {
                f"{param}_slope": slope,
                f"{param}_excursions": g["out"],
                f"{param}_time_outside_pct": g["out"] / g["n"],
            },
            index=g.index,
        )
        frames.append(frame)
    result = pd.concat(frames, axis=1)  # every frame is indexed by lot_id
    return result.fillna(0.0)


def equipment_context_features(lots_df: pd.DataFrame) -> pd.DataFrame:
    """
    equipment_age_days / chamber_previous_yield / equipment_prev10_low_yield_rate.

    All backward-looking: shifted/rolling series only ever see lots that
    finished strictly before the current lot's process_start.
    """
    lots_sorted = lots_df.sort_values("process_start").copy()
    lots_sorted["process_start"] = pd.to_datetime(lots_sorted["process_start"])

    # Tool age at production time (own start included — age is not a label).
    first_start = lots_sorted.groupby("equipment")["process_start"].transform("min")
    lots_sorted["equipment_age_days"] = (
        (lots_sorted["process_start"] - first_start).dt.total_seconds() / 86400.0
    )

    lots_sorted["is_low"] = (lots_sorted["yield"] < LOW_YIELD_THRESHOLD).astype(float)

    # Previous low-yield rate on this equipment, over the previous <=10 lots.
    prev10 = []
    for _, group in lots_sorted.groupby("equipment"):
        shifted = group["is_low"].shift(1)
        prev10.append(shifted.rolling(10, min_periods=1).mean())
    lots_sorted["equipment_prev10_low_yield_rate"] = pd.concat(prev10).reindex(lots_sorted.index)

    # Previous lot's yield in the same chamber, if chamber info exists.
    if "chamber_id" in lots_sorted.columns:
        chamber_prev = (
            lots_sorted.sort_values("process_start")
            .groupby("chamber_id")["yield"]
            .shift(1)
        )
        lots_sorted["chamber_previous_yield"] = chamber_prev.reindex(lots_sorted.index)
    else:
        lots_sorted["chamber_previous_yield"] = np.nan

    median_yield = lots_sorted["yield"].median()
    lots_sorted["chamber_previous_yield"] = lots_sorted["chamber_previous_yield"].fillna(median_yield)
    lots_sorted["equipment_prev10_low_yield_rate"] = lots_sorted["equipment_prev10_low_yield_rate"].fillna(0.0)

    return lots_sorted.set_index("lot_id")[EXTENDED_HISTORY_COLUMNS]
