"""
Generates the synthetic manufacturing layer (spec sections 24-25, 30, 47-48).

Everything produced here is DEMO / SIMULATED DATA (see
app.data.schemas.dataset_schema) — a deterministic (seeded), causally
structured stand-in for a real fab's lot/equipment/sensor/defect history,
used because real fab data is confidential. Relationships are built in on
purpose (RF power drift -> etch variation -> defect probability up ->
yield down) rather than left to pure randomness, so the downstream ML
models have real signal to learn and SHAP has something honest to find.

Writes CSVs to SYNTHETIC_DATA_PATH:
    equipment.csv, lots.csv, process_parameters.csv, sensor_data.csv,
    wafers.csv, defects.csv, maintenance.csv
plus _generation_metadata.json documenting the seed/ranges/date used, so
"which timestamp produced which feature" stays auditable (spec section 11).

Usage:
    cd backend
    .venv\\Scripts\\activate
    python scripts/generate_synthetic_data.py
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import LOW_YIELD_THRESHOLD, PROCESS_PARAMETER_RANGES, get_settings  # noqa: E402

# ---------------------------------------------------------------------------
# Generation configuration (tune here, not scattered through the code)
# ---------------------------------------------------------------------------
SEED = 42
N_LOTS = 950
INCIDENT_LOT_INDEX = 901  # -> lot_id LOT_10901, matches the spec's worked example
END_DATE = datetime(2026, 9, 14)  # fixed, not "today", so output is fully reproducible
LOOKBACK_DAYS = 150

PRODUCTS = ["5NM_CPU", "7NM_GPU", "10NM_SOC", "14NM_MCU"]
TOOL_FAMILIES = {
    "ETCH": 10,
    "CVD": 6,
    "CMP": 6,
    "LITHO": 4,
    "IMPLANT": 2,
    "DIFFUSION": 2,
}
DRIFT_EQUIPMENT_ID = "ETCH_007"
PEER_EQUIPMENT_ID = "ETCH_003"  # spec section 157: "Compare ETCH_007 with ETCH_003"
DRIFT_WINDOW_DAYS = 18  # trailing window of the timeline during which ETCH_007 is drifting

NORMAL_RANGES = PROCESS_PARAMETER_RANGES  # single source of truth lives in app.config
RANDOM_EXCURSION_PROB = 0.16  # fraction of non-incident lots with a one-off, fab-wide parameter excursion

DEFECT_TYPES = ["Particle", "Scratch", "Crack", "Pattern Defect", "Contamination", "Edge Defect", "Unknown"]
DEFECT_TYPE_WEIGHTS = [0.30, 0.14, 0.06, 0.20, 0.16, 0.10, 0.04]

WAFERS_PER_LOT = 25

# Causal weights: how strongly each parameter's *abnormal* deviation drives defect probability.
DEFECT_DRIVER_WEIGHTS = {
    "rf_power": 0.42,
    "temperature": 0.27,
    "pressure": 0.18,
    "gas_flow": 0.08,
    "etch_time": 0.05,
}


def _rng() -> np.random.Generator:
    return np.random.default_rng(SEED)


def generate_equipment(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for family, count in TOOL_FAMILIES.items():
        for i in range(1, count + 1):
            equipment_id = f"{family}_{i:03d}"
            is_drifting = equipment_id == DRIFT_EQUIPMENT_ID
            rows.append(
                {
                    "equipment_id": equipment_id,
                    "tool_family": family,
                    "chamber": f"C{rng.integers(1, 4)}",
                    "location": f"BAY_{rng.integers(1, 9)}",
                    "health_score": 68.0 if is_drifting else round(float(rng.uniform(85, 99)), 1),
                    "status": "warning" if is_drifting else "operational",
                    "anomaly_status": "RF power instability" if is_drifting else None,
                    "is_simulated_metadata": True,
                }
            )
    return pd.DataFrame(rows)


def _sample_param(rng: np.random.Generator, name: str, abnormal: bool) -> float:
    lo, hi = NORMAL_RANGES[name]
    center, half_width = (lo + hi) / 2, (hi - lo) / 2
    if abnormal:
        # push meaningfully outside the normal band, on whichever side is more common for this kind of drift
        direction = 1 if name in ("rf_power", "temperature", "current") else rng.choice([-1, 1])
        magnitude = rng.uniform(1.3, 2.6) * half_width
        return float(np.clip(center + direction * magnitude + rng.normal(0, half_width * 0.15), lo - half_width, hi + half_width * 2.5))
    value = rng.normal(center, half_width * 0.35)
    return float(np.clip(value, lo - half_width * 0.5, hi + half_width * 0.5))


def _deviation(name: str, value: float) -> float:
    """Continuous deviation: 0 at the band center, 1.0 exactly at the band edge, >1 beyond it.

    Deliberately not a hard cutoff at the edge — real process windows have a soft
    margin, not a cliff, so a value near the edge of "normal" should already carry
    a little risk rather than none at all.
    """
    lo, hi = NORMAL_RANGES[name]
    center, half_width = (lo + hi) / 2, (hi - lo) / 2
    return abs(value - center) / half_width


def generate_lots(rng: np.random.Generator, equipment_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    etch_equipment = equipment_df[equipment_df.tool_family == "ETCH"].equipment_id.tolist()
    other_equipment = equipment_df[equipment_df.tool_family != "ETCH"]

    starts = sorted(END_DATE - timedelta(days=float(d)) for d in rng.uniform(0, LOOKBACK_DAYS, size=N_LOTS))
    drift_cutoff = END_DATE - timedelta(days=DRIFT_WINDOW_DAYS)

    lot_rows, process_rows = [], []
    for i in range(N_LOTS):
        lot_id = f"LOT_{10000 + i}"
        process_start = starts[i]
        is_incident = i == INCIDENT_LOT_INDEX

        if is_incident:
            equipment_id, process_step = DRIFT_EQUIPMENT_ID, "ETCH"
        elif rng.random() < 0.55:
            equipment_id, process_step = rng.choice(etch_equipment), "ETCH"
        else:
            row = other_equipment.sample(1, random_state=int(rng.integers(0, 1_000_000))).iloc[0]
            equipment_id, process_step = row.equipment_id, row.tool_family

        equipment_drifting_now = equipment_id == DRIFT_EQUIPMENT_ID and (process_start >= drift_cutoff or is_incident)

        if equipment_drifting_now:
            abnormal_params = {"rf_power", "temperature"} if rng.random() < 0.7 else {"rf_power"}
        elif not is_incident and rng.random() < RANDOM_EXCURSION_PROB:
            n_abnormal = 1 if rng.random() < 0.65 else 2
            all_params = list(NORMAL_RANGES)
            chosen = rng.choice(len(all_params), size=n_abnormal, replace=False)
            abnormal_params = {all_params[j] for j in chosen}
        else:
            abnormal_params = set()

        params = {name: _sample_param(rng, name, name in abnormal_params) for name in NORMAL_RANGES}
        if is_incident:
            # Hand-tuned, not procedurally sampled: this is the one deterministic
            # demo incident (spec section 48), so its evidence chain is authored
            # for narrative coherence — RF Power dominant, Temperature/Pressure
            # secondary — rather than left to the general random-excursion path.
            params["rf_power"] = float(rng.normal(906.0, 3.0))
            params["temperature"] = float(rng.normal(446.0, 1.5))
            params["pressure"] = float(rng.normal(2.95, 0.04))

        deviations = {name: _deviation(name, val) for name, val in params.items()}
        # Soft-knee: deviation has to cross ~halfway into "beyond normal" before it
        # starts contributing, then contributions saturate (via 1-exp) rather than
        # exploding, so severity stays graded instead of every excursion looking
        # equally catastrophic. An unmeasured-factors term is added *before* the
        # squashing function (not just as output noise) so the measured parameters
        # don't fully determine the outcome — a few normal-looking lots still tip
        # into low yield and a few abnormal ones still scrape by, same as a real
        # process with confounders nobody is logging. Without this, a flexible
        # model can reconstruct the generating rule almost exactly and reports a
        # suspiciously perfect test score.
        raw_risk = sum(DEFECT_DRIVER_WEIGHTS[n] * max(0.0, deviations[n] - 0.5) for n in DEFECT_DRIVER_WEIGHTS)
        raw_risk = max(0.0, raw_risk + rng.normal(0, 0.14))
        defect_probability = 0.03 + 0.95 * (1 - np.exp(-1.1 * raw_risk))
        defect_probability = float(np.clip(defect_probability + rng.normal(0, 0.035), 0.01, 0.97))

        yield_pct = 99.3 - defect_probability * 45 + rng.normal(0, 2.0)
        yield_pct = float(np.clip(yield_pct, 40.0, 99.8))

        status = "completed" if process_start < END_DATE - timedelta(days=2) and not is_incident else "in_progress"

        lot_rows.append(
            {
                "lot_id": lot_id,
                "product": rng.choice(PRODUCTS),
                "node": "5nm" if "5NM" in PRODUCTS[0] else "N/A",
                "wafer_count": WAFERS_PER_LOT,
                "process_start": process_start.isoformat(),
                "equipment": equipment_id,
                "process_step": process_step,
                "status": status,
                "yield": round(yield_pct, 2),
                "is_low_yield": int(yield_pct < LOW_YIELD_THRESHOLD),
                "defect_probability": round(defect_probability, 4),
                "is_demo_incident": is_incident,
                "data_source": "synthetic",
            }
        )
        process_rows.append(
            {
                "lot_id": lot_id,
                "equipment_id": equipment_id,
                "process": process_step,
                "timestamp": process_start.isoformat(),
                **{k: round(v, 3) for k, v in params.items()},
            }
        )

    return pd.DataFrame(lot_rows), pd.DataFrame(process_rows)


def generate_sensor_data(rng: np.random.Generator, process_df: pd.DataFrame) -> pd.DataFrame:
    """Multiple raw readings per lot per key parameter, so mean/std/drift features are genuine."""
    readings_per_lot = 15
    tracked = ["temperature", "rf_power", "pressure", "gas_flow"]
    rows = []
    for _, run in process_df.iterrows():
        drifting = run["equipment_id"] == DRIFT_EQUIPMENT_ID and run[tracked[1]] > 890
        for param in tracked:
            base = run[param]
            trend = rng.uniform(0.5, 1.4) if drifting and param in ("rf_power", "temperature") else 0.0
            for k in range(readings_per_lot):
                noise = rng.normal(0, abs(base) * 0.01 + 0.05)
                rows.append(
                    {
                        "lot_id": run["lot_id"],
                        "equipment_id": run["equipment_id"],
                        "parameter": param,
                        "reading_index": k,
                        "timestamp": (pd.Timestamp(run["timestamp"]) + pd.Timedelta(seconds=k * 30)).isoformat(),
                        "value": round(float(base + trend * k + noise), 4),
                    }
                )
    return pd.DataFrame(rows)


def generate_wafers_and_defects(
    rng: np.random.Generator, lots_df: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    wafer_rows, defect_rows = [], []
    for _, lot in lots_df.iterrows():
        bad_lot = lot["yield"] < LOW_YIELD_THRESHOLD
        if lot["is_demo_incident"]:
            forced_pattern = "Edge-Ring"
        else:
            forced_pattern = None
            pattern_pool = (
                ["Edge-Ring", "Cluster", "Scratch", "Donut", "Random"]
                if bad_lot
                else ["None", "Random", "Loc"]
            )
        for slot in range(1, lot["wafer_count"] + 1):
            wafer_id = f"{lot['lot_id']}_WAFER_{slot:02d}"
            pattern_hint = forced_pattern or rng.choice(pattern_pool)
            wafer_yield_status = "fail" if rng.random() < lot["defect_probability"] else "pass"
            wafer_rows.append(
                {
                    "wafer_id": wafer_id,
                    "lot_id": lot["lot_id"],
                    "slot": slot,
                    "yield_status": wafer_yield_status,
                    "pattern_hint": pattern_hint,
                }
            )

            n_defects = rng.poisson(lam=max(0.1, lot["defect_probability"] * 6))
            for _ in range(int(n_defects)):
                defect_type = rng.choice(DEFECT_TYPES, p=DEFECT_TYPE_WEIGHTS)
                severity = rng.choice(["LOW", "MEDIUM", "HIGH"], p=[0.5, 0.35, 0.15])
                defect_rows.append(
                    {
                        "defect_id": f"{wafer_id}_D{len(defect_rows):07d}",
                        "wafer_id": wafer_id,
                        "defect_type": defect_type,
                        "severity": severity,
                        "x": round(float(rng.uniform(-75, 75)), 2),
                        "y": round(float(rng.uniform(-75, 75)), 2),
                        "size": round(float(rng.uniform(0.05, 2.5)), 3),
                    }
                )
    return pd.DataFrame(wafer_rows), pd.DataFrame(defect_rows)


def generate_maintenance(rng: np.random.Generator, equipment_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, eq in equipment_df.iterrows():
        n_events = rng.integers(3, 9)
        for _ in range(int(n_events)):
            days_ago = rng.uniform(1, LOOKBACK_DAYS)
            if eq.equipment_id == DRIFT_EQUIPMENT_ID:
                days_ago = rng.uniform(DRIFT_WINDOW_DAYS + 5, LOOKBACK_DAYS)  # overdue: nothing recent
            rows.append(
                {
                    "equipment_id": eq.equipment_id,
                    "event_type": rng.choice(["scheduled_pm", "calibration", "part_replacement", "unscheduled_repair"]),
                    "timestamp": (END_DATE - timedelta(days=float(days_ago))).isoformat(),
                    "notes": "Synthetic maintenance event.",
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    settings = get_settings()
    out_dir = settings.resolve_path(settings.synthetic_data_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    rng = _rng()
    print("Generating equipment...")
    equipment_df = generate_equipment(rng)

    print(f"Generating {N_LOTS} lots (incident = LOT_{10000 + INCIDENT_LOT_INDEX} on {DRIFT_EQUIPMENT_ID})...")
    lots_df, process_df = generate_lots(rng, equipment_df)

    print("Generating sensor telemetry...")
    sensor_df = generate_sensor_data(rng, process_df)

    print("Generating wafers and defects...")
    wafers_df, defects_df = generate_wafers_and_defects(rng, lots_df)

    print("Generating maintenance history...")
    maintenance_df = generate_maintenance(rng, equipment_df)

    equipment_df.to_csv(out_dir / "equipment.csv", index=False)
    lots_df.to_csv(out_dir / "lots.csv", index=False)
    process_df.to_csv(out_dir / "process_parameters.csv", index=False)
    sensor_df.to_csv(out_dir / "sensor_data.csv", index=False)
    wafers_df.to_csv(out_dir / "wafers.csv", index=False)
    defects_df.to_csv(out_dir / "defects.csv", index=False)
    maintenance_df.to_csv(out_dir / "maintenance.csv", index=False)

    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": SEED,
        "n_lots": N_LOTS,
        "incident_lot_id": f"LOT_{10000 + INCIDENT_LOT_INDEX}",
        "drift_equipment_id": DRIFT_EQUIPMENT_ID,
        "peer_equipment_id": PEER_EQUIPMENT_ID,
        "low_yield_threshold": LOW_YIELD_THRESHOLD,
        "normal_ranges": NORMAL_RANGES,
        "data_provenance": "synthetic",
        "note": "DEMO / SIMULATED DATA. See app/data/schemas/dataset_schema.py.",
    }
    with open(out_dir / "_generation_metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    print(f"\nDone. Wrote {len(lots_df)} lots, {len(wafers_df)} wafers, {len(defects_df)} defects to {out_dir}")


if __name__ == "__main__":
    main()
