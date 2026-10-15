"""
Ingest data validation (Phase 7, brief P6).

Runs on ingest (scripts/seed_database.py, POST /api/data/import,
scripts/run_data_quality.py) and produces ONE quality report per lot:

    {lot_id, status: PASS | WARN | FAIL, n_fail, n_warn, issues: [...], ...}

Checks (each issue names its check, parameter, severity and the measured value):

  lot-level
    duplicate_lot_id        the same lot_id appears more than once            FAIL
    bad_process_window      process_end <= process_start                     FAIL
    impossible_yield        yield outside [0, 100]                           FAIL
  telemetry, per channel
    missing_sensor          an expected channel has no rows / no values      FAIL
    missing_values          NaN fraction > MISSING_WARN_FRACTION             WARN
    sensor_outage           >= OUTAGE_MIN_SAMPLES consecutive NaNs           WARN
    stuck_values            >= STUCK_MIN_SAMPLES identical consecutive       WARN
    impossible_values       outside center +/- IMPOSSIBLE_HALF_WIDTHS        FAIL
    unit_mismatch           median off-scale but a known unit conversion     FAIL
                            brings it into the physical range
    duplicate_samples       repeated (lot, channel, ts_index)                FAIL
    timestamp_order         timestamps not strictly increasing               FAIL
    timestamp_outside_run   samples outside [process_start, process_end]     WARN
    irregular_sampling      gaps != the configured sample interval           WARN

Thresholds are named constants below; channel windows come from the simulator's
physics table (the single definition of "normal" in this codebase).
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app.simulator.config import get_simulator_config
from app.simulator.process_physics import CHANNEL_SPECS

VALIDATOR_VERSION = "1.0.0"
MISSING_WARN_FRACTION = 0.10
OUTAGE_MIN_SAMPLES = 3
STUCK_MIN_SAMPLES = 5
IMPOSSIBLE_HALF_WIDTHS = 6.0      # beyond center +/- 6 half-widths a reading cannot be real
PHYSICAL_HALF_WIDTHS = 3.0        # the generator's physical clip range
TIMESTAMP_SLACK_SECONDS = 60

# (label, function) pairs: if a channel's median is off-scale but f(median) lands
# inside the physical range, the likely cause is a unit mismatch.
UNIT_CONVERSIONS: dict[str, list[tuple[str, callable]]] = {
    "temperature": [("Kelvin -> degC", lambda v: v - 273.15), ("degF -> degC", lambda v: (v - 32.0) * 5.0 / 9.0)],
    "pressure": [("mTorr -> Torr", lambda v: v / 1000.0), ("Pa -> Torr", lambda v: v / 133.322)],
    "rf_power": [("kW -> W", lambda v: v * 1000.0)],
    "gas_flow": [("slm -> sccm", lambda v: v * 1000.0)],
}


def _bounds(channel: str, half_widths: float) -> tuple[float, float] | None:
    spec = CHANNEL_SPECS.get(channel)
    if spec is None:
        return None
    return spec.center - half_widths * spec.half_width, spec.center + half_widths * spec.half_width


def _issue(lot_id, check, severity, detail, parameter=None, value=None) -> dict:
    return {
        "lot_id": lot_id,
        "check": check,
        "severity": severity,
        "parameter": parameter,
        "value": None if value is None else (round(float(value), 4) if isinstance(value, (int, float, np.floating, np.integer)) else value),
        "detail": detail,
    }


def _longest_runs(flags: pd.Series, keys: pd.Series) -> pd.Series:
    """Longest run of consecutive True per key (rows must be sorted by key, then order)."""
    run_break = (flags != flags.shift()) | (keys != keys.shift())
    run_len = flags.groupby(run_break.cumsum()).transform("size").where(flags, 0)
    return run_len.groupby(keys).max()


def validate_tables(
    lots: pd.DataFrame,
    telemetry: pd.DataFrame | None,
    expected_channels: list[str] | None = None,
    sample_interval_seconds: int | None = None,
) -> pd.DataFrame:
    """Validate a batch. Returns one row per lot_id with the full report.

    `lots` needs lot_id (+ process_start/process_end/yield when present);
    `telemetry` needs lot_id, parameter, ts_index, value (+ timestamp when present).
    """
    cfg = get_simulator_config()
    expected_channels = list(expected_channels or cfg.telemetry.channels)
    interval = sample_interval_seconds or cfg.telemetry.sample_interval_seconds
    issues: list[dict] = []

    lots = lots.copy()
    lot_ids = list(dict.fromkeys(lots["lot_id"].astype(str)))

    # ---------------- lot-level
    for lot_id in lots.loc[lots["lot_id"].duplicated(keep="first"), "lot_id"].astype(str).unique():
        issues.append(_issue(lot_id, "duplicate_lot_id", "FAIL", "lot_id appears more than once in the batch"))
    if {"process_start", "process_end"} <= set(lots.columns):
        starts = pd.to_datetime(lots["process_start"], utc=True, errors="coerce", format="ISO8601")
        ends = pd.to_datetime(lots["process_end"], utc=True, errors="coerce", format="ISO8601")
        lots["_start"], lots["_end"] = starts, ends
        for row in lots[(ends <= starts) | starts.isna() | ends.isna()].itertuples():
            issues.append(_issue(str(row.lot_id), "bad_process_window", "FAIL", "process_end is missing or not after process_start"))
    if "yield" in lots.columns:
        bad = lots[(lots["yield"] < 0) | (lots["yield"] > 100)]
        for _, row in bad.iterrows():
            issues.append(_issue(str(row["lot_id"]), "impossible_yield", "FAIL", "yield outside [0, 100]", value=row["yield"]))

    # ---------------- telemetry
    if telemetry is not None and len(telemetry):
        tel = telemetry[telemetry["lot_id"].astype(str).isin(lot_ids)].copy()
        tel["lot_id"] = tel["lot_id"].astype(str)
        tel = tel.sort_values(["lot_id", "parameter", "ts_index"], kind="stable").reset_index(drop=True)
        tel["value"] = pd.to_numeric(tel["value"], errors="coerce")
        tel["_key"] = tel["lot_id"] + "|" + tel["parameter"].astype(str)
        issues.extend(_telemetry_issues(tel, lots, lot_ids, expected_channels, interval))

    return _reports(lot_ids, issues)


def _split(key: str) -> tuple[str, str]:
    lot_id, channel = key.split("|", 1)
    return lot_id, channel


def _telemetry_issues(tel: pd.DataFrame, lots: pd.DataFrame, lot_ids: list[str], expected_channels: list[str], interval: int) -> list[dict]:
    """All per-channel checks, vectorized over (lot, channel) keys."""
    issues: list[dict] = []
    keys = tel["_key"]
    isna = tel["value"].isna()

    # missing sensors
    sizes = keys.value_counts()
    non_null = keys[~isna].value_counts()
    for lot_id in lot_ids:
        for channel in expected_channels:
            key = f"{lot_id}|{channel}"
            if key not in sizes.index:
                issues.append(_issue(lot_id, "missing_sensor", "FAIL", "expected channel has no rows", parameter=channel))
            elif key not in non_null.index:
                issues.append(_issue(lot_id, "missing_sensor", "FAIL", "expected channel has no values (all NaN)", parameter=channel))

    # missing values / outages / stuck
    nan_frac = isna.groupby(keys).mean()
    for key, frac in nan_frac[(nan_frac > MISSING_WARN_FRACTION) & (nan_frac < 1.0)].items():
        lot_id, channel = _split(key)
        issues.append(_issue(lot_id, "missing_values", "WARN", f"{frac:.0%} of samples are NaN", channel, frac))
    outage = _longest_runs(isna, keys)
    outage = outage[(outage >= OUTAGE_MIN_SAMPLES) & (outage < sizes.reindex(outage.index))]
    for key, length in outage.items():
        lot_id, channel = _split(key)
        issues.append(_issue(lot_id, "sensor_outage", "WARN", f"{int(length)} consecutive missing samples", channel, length))
    repeats = tel["value"].notna() & (tel["value"] == tel["value"].shift()) & (keys == keys.shift())
    stuck = _longest_runs(repeats, keys) + 1  # n repeats = n + 1 identical samples
    for key, length in stuck[stuck >= STUCK_MIN_SAMPLES].items():
        lot_id, channel = _split(key)
        issues.append(_issue(lot_id, "stuck_values", "WARN", f"{int(length)} identical consecutive samples", channel, length))

    # unit mismatch (median off-scale, a known conversion fixes it) and impossible values
    medians = tel.groupby("_key")["value"].median()
    unit_flagged: set[str] = set()
    for key, med in medians.items():
        lot_id, channel = _split(key)
        hard, phys = _bounds(channel, IMPOSSIBLE_HALF_WIDTHS), _bounds(channel, PHYSICAL_HALF_WIDTHS)
        if hard is None or pd.isna(med) or hard[0] <= med <= hard[1]:
            continue
        fix = next((label for label, f in UNIT_CONVERSIONS.get(channel, []) if phys[0] <= f(med) <= phys[1]), None)
        if fix:
            unit_flagged.add(key)
            issues.append(_issue(lot_id, "unit_mismatch", "FAIL", f"median {med:.4g} is off-scale; looks like {fix}", channel, med))
    channel_lo = tel["parameter"].map({c: _bounds(c, IMPOSSIBLE_HALF_WIDTHS)[0] for c in CHANNEL_SPECS})
    channel_hi = tel["parameter"].map({c: _bounds(c, IMPOSSIBLE_HALF_WIDTHS)[1] for c in CHANNEL_SPECS})
    outside = tel[((tel["value"] < channel_lo) | (tel["value"] > channel_hi)) & ~keys.isin(unit_flagged)]
    if len(outside):
        agg = outside.groupby("_key")["value"].agg(["size", lambda v: v.abs().max()])
        for key, (count, worst) in zip(agg.index, agg.to_numpy()):
            lot_id, channel = _split(key)
            issues.append(_issue(lot_id, "impossible_values", "FAIL", f"{int(count)} sample(s) beyond physical plausibility", channel, worst))

    # duplicates
    dup_mask = tel.duplicated(["lot_id", "parameter", "ts_index"], keep=False)
    for key, count in keys[dup_mask].value_counts().items():
        lot_id, channel = _split(key)
        issues.append(_issue(lot_id, "duplicate_samples", "FAIL", f"{int(count)} rows share a ts_index", channel, count))

    # timestamps
    if "timestamp" in tel.columns:
        dedup = tel[~tel.duplicated(["lot_id", "parameter", "ts_index"], keep="first")].copy()
        dedup["_ts"] = pd.to_datetime(dedup["timestamp"], utc=True, errors="coerce", format="ISO8601")
        gap = dedup.groupby("_key")["_ts"].diff().dt.total_seconds()
        bad_order = (gap <= 0) | dedup["_ts"].isna()
        irregular = gap.notna() & (gap > 0) & ((gap - interval).abs() > 1e-6)
        order_keys = set(dedup.loc[bad_order, "_key"])
        for key in sorted(order_keys):
            lot_id, channel = _split(key)
            issues.append(_issue(lot_id, "timestamp_order", "FAIL", "timestamps missing or not strictly increasing", channel))
        irregular_counts = dedup.loc[irregular & ~dedup["_key"].isin(order_keys), "_key"].value_counts()
        for key, count in irregular_counts.items():
            lot_id, channel = _split(key)
            issues.append(_issue(lot_id, "irregular_sampling", "WARN", f"{int(count)} gap(s) differ from {interval}s", channel, count))
        if "_start" in lots.columns:
            window = lots.drop_duplicates("lot_id").assign(lot_id=lambda d: d["lot_id"].astype(str)).set_index("lot_id")[["_start", "_end"]]
            joined = dedup.join(window, on="lot_id")
            slack = pd.Timedelta(seconds=TIMESTAMP_SLACK_SECONDS)
            late = joined[(joined["_ts"] < joined["_start"] - slack) | (joined["_ts"] > joined["_end"] + slack)]
            for key, count in late["_key"].value_counts().items():
                lot_id, channel = _split(key)
                issues.append(_issue(lot_id, "timestamp_outside_run", "WARN", f"{int(count)} sample(s) outside the run window", channel, count))
    return issues


def _reports(lot_ids: list[str], issues: list[dict]) -> pd.DataFrame:
    by_lot: dict[str, list[dict]] = {lot_id: [] for lot_id in lot_ids}
    for issue in issues:
        by_lot.setdefault(issue["lot_id"], []).append({k: v for k, v in issue.items() if k != "lot_id"})
    generated_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for lot_id, lot_issues in by_lot.items():
        n_fail = sum(1 for i in lot_issues if i["severity"] == "FAIL")
        n_warn = sum(1 for i in lot_issues if i["severity"] == "WARN")
        rows.append(
            {
                "lot_id": lot_id,
                "status": "FAIL" if n_fail else ("WARN" if n_warn else "PASS"),
                "n_fail": n_fail,
                "n_warn": n_warn,
                "issues": lot_issues,
                "validator_version": VALIDATOR_VERSION,
                "generated_at": generated_at,
            }
        )
    return pd.DataFrame(rows, columns=["lot_id", "status", "n_fail", "n_warn", "issues", "validator_version", "generated_at"])


def summarize(reports: pd.DataFrame) -> dict:
    """Batch-level counts: status distribution and issues per check."""
    per_check: dict[str, int] = {}
    for issues in reports["issues"]:
        for issue in issues:
            per_check[issue["check"]] = per_check.get(issue["check"], 0) + 1
    return {
        "n_lots": int(len(reports)),
        "status_counts": {k: int(v) for k, v in reports["status"].value_counts().to_dict().items()},
        "issues_per_check": dict(sorted(per_check.items(), key=lambda kv: -kv[1])),
        "validator_version": VALIDATOR_VERSION,
    }


def store_reports(db, reports: pd.DataFrame) -> int:
    """Upsert one report document per lot into `data_quality_reports`."""
    n = 0
    for record in reports.to_dict("records"):
        db.data_quality_reports.replace_one({"lot_id": record["lot_id"]}, record, upsert=True)
        n += 1
    return n
