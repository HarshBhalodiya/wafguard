"""
Synthetic manufacturing dataset generator (v2).

Everything produced here is DEMO / SIMULATED DATA — a deterministic
(seed-locked), causally structured stand-in for a real fab. The causal
chain (fault -> mechanism -> sensor signature + spatial pattern + defect
density + yield loss) is built in on purpose so the root-cause engine and
evidence graph have something measurable to evaluate against.

Ground truth (the hidden fault label per lot) is returned separately and
written by scripts/build_dataset.py to data/synthetic/ground_truth.parquet.
Nothing in app/features/ or app/ml/ may read it — enforced by a test.

Usage (via the one command):
    python scripts/build_dataset.py
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd

from app.config import PROCESS_PARAMETER_RANGES
from app.simulator.config import SimulatorConfig
from app.simulator.interventions import generate_interventions
from app.simulator.mechanisms import MECHANISMS, mechanism_for
from app.simulator.process_physics import (
    BACKGROUND_DEFECT_PROB,
    CHANNEL_SPECS,
    DEGRADATION_DAYS_FULL,
    DEGRADATION_MAX_EXCESS,
    DEGRADATION_RISK_GROWTH,
    EXCURSION_MAGNITUDE,
    EXCURSION_RATE_ON_NORMAL,
    FAULT_SEVERITY,
    RECOVER_TIME_SECONDS,
    SENSOR_DRIFT_BIAS,
    YIELD_NOISE_SIGMA,
)

TOOL_FAMILIES = {
    "ETCH": 12,
    "CVD": 8,
    "CMP": 6,
    "LITHO": 6,
    "IMPLANT": 4,
    "DIFFUSION": 4,
}
CHAMBERS_PER_TOOL = (1, 3)
N_RECIPES = 24
PRODUCTS = ["5NM_CPU", "7NM_GPU", "10NM_SOC", "14NM_MCU", "22NM_IO"]
RECIPE_STEP_MAP = {
    "ETCH": ["ETCH_STD_A", "ETCH_STD_B", "ETCH_ADV_C"],
    "CVD": ["CVD_LINER", "CVD_BULK", "CVD_CAP"],
    "CMP": ["CMP_OX", "CMP_METAL"],
    "LITHO": ["LITHO_AR1", "LITHO_AR2", "LITHO_TRIM"],
    "IMPLANT": ["IMP_WELL", "IMP_SDE"],
    "DIFFUSION": ["DIFF_OX", "DIFF_ANNEAL"],
}
DEFECT_TYPES = ["Particle", "Scratch", "Crack", "Pattern Defect", "Contamination", "Edge Defect", "Unknown"]
EVENT_TYPES = [
    "PROCESS_START", "PROCESS_END", "PARAMETER_EXCURSION", "EQUIPMENT_ALARM",
    "MAINTENANCE", "RECIPE_TRANSITION", "INSPECTION",
]


@dataclass
class LotRun:
    """One lot's process run with all generated artifacts for it."""
    lot_id: str
    equipment_id: str
    chamber_id: str
    recipe_id: str
    process_step: str
    product: str
    process_start: datetime
    fault_type: str
    params: dict = field(default_factory=dict)
    telemetry: Optional[pd.DataFrame] = None
    yield_pct: float = 0.0
    defect_probability: float = 0.0
    defect_density: float = 0.0
    spatial_signature: str = "None"
    electrical_pass_rate: float = 1.0


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def generate_equipment_and_chambers(rng: np.random.Generator) -> tuple[pd.DataFrame, pd.DataFrame]:
    eq_rows, chamber_rows = [], []
    for family, count in TOOL_FAMILIES.items():
        for i in range(1, count + 1):
            equipment_id = f"{family}_{i:03d}"
            eq_rows.append(
                {
                    "equipment_id": equipment_id,
                    "tool_family": family,
                    "location": f"BAY_{rng.integers(1, 10)}",
                    "install_date": (_utc(datetime.fromisoformat("2024-01-01T00:00:00+00:00")) - timedelta(days=int(rng.integers(30, 900)))).isoformat(),
                    "health_score": round(float(rng.uniform(85, 99)), 1),
                    "status": "operational",
                    "anomaly_status": None,
                    "is_simulated_metadata": True,
                }
            )
            n_chambers = int(rng.integers(CHAMBERS_PER_TOOL[0], CHAMBERS_PER_TOOL[1] + 1))
            for c in range(1, n_chambers + 1):
                chamber_rows.append(
                    {
                        "chamber_id": f"{equipment_id}_CH{c}",
                        "equipment_id": equipment_id,
                        "chamber_index": c,
                        "is_simulated_metadata": True,
                    }
                )
    return pd.DataFrame(eq_rows), pd.DataFrame(chamber_rows)


def generate_recipes(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for i in range(1, N_RECIPES + 1):
        step = rng.choice(list(RECIPE_STEP_MAP.keys()))
        rows.append(
            {
                "recipe_id": f"REC_{i:03d}",
                "process_step": step,
                "sub_recipe": rng.choice(RECIPE_STEP_MAP[step]),
                "version": f"v{rng.integers(1, 4)}.{rng.integers(0, 9)}",
                "is_simulated_metadata": True,
            }
        )
    return pd.DataFrame(rows)


def generate_maintenance(rng: np.random.Generator, equipment_df: pd.DataFrame, end: datetime, lookback_days: int) -> pd.DataFrame:
    rows = []
    for _, eq in equipment_df.iterrows():
        n_events = int(rng.integers(2, 7))
        for _ in range(n_events):
            days_ago = float(rng.uniform(1, lookback_days))
            rows.append(
                {
                    "event_id": f"MAINT_{len(rows):06d}",
                    "equipment_id": eq.equipment_id,
                    "chamber_id": None,
                    "event_type": rng.choice(["scheduled_pm", "calibration", "part_replacement", "unscheduled_repair"], p=[0.45, 0.25, 0.15, 0.15]),
                    "timestamp": (end - timedelta(days=days_ago)).isoformat(),
                    "notes": "Synthetic maintenance event.",
                }
            )
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df.sort_values("timestamp").reset_index(drop=True)


def _days_since_last_maintenance(maint_df: pd.DataFrame, equipment_id: str, when: datetime) -> float:
    """Days since this equipment's last maintenance strictly before `when`.
    Sentinel 999.0 means 'not serviced inside the generated window'."""
    sub = maint_df[(maint_df.equipment_id == equipment_id) & (maint_df["timestamp"] < pd.Timestamp(when))]
    if sub.empty:
        return 999.0
    last = pd.Timestamp(sub["timestamp"].max())
    return max(0.0, (pd.Timestamp(when) - last).total_seconds() / 86400.0)


def _assign_faults(rng: np.random.Generator, cfg: SimulatorConfig, maint_df: pd.DataFrame, lot_starts: list[datetime], equipment_ids: list[str]) -> list[str]:
    """Assign one fault type per lot. Equipment with recent PMs gets a bonus
    against 'equipment_degradation'; degradation odds rise with days-since-PM."""
    fault_names = [f for f in cfg.fault_names if f != "none"]
    base_p = np.array([cfg.fault_rates[f] for f in fault_names])
    p_none = cfg.fault_rates["none"]

    assignments = []
    for lot_start, eq in zip(lot_starts, equipment_ids):
        days_since = _days_since_last_maintenance(maint_df, eq, lot_start)
        degradation_boost = 1.0 + DEGRADATION_RISK_GROWTH * min(1.0, days_since / DEGRADATION_DAYS_FULL)
        # Scale non-degradation fault mass to keep the total fault rate near target,
        # then give the freed probability to degradation for aged tools.
        p = base_p.astype(float).copy()
        deg_idx = fault_names.index("equipment_degradation")
        others = [i for i in range(len(fault_names)) if i != deg_idx]
        scale = (1.0 - p_none - p[deg_idx] * (degradation_boost - 1.0) * 0.5) / max(1e-9, (1.0 - p_none))
        p[others] *= max(0.2, scale)
        p[deg_idx] *= degradation_boost
        p = p / p.sum()
        fault = rng.choice(len(fault_names), p=p) if rng.random() > p_none else -1
        assignments.append("none" if fault == -1 else fault_names[int(fault)])
    return assignments


def _sample_params(rng: np.random.Generator, cfg: SimulatorConfig, fault: str, equipment_health: float, days_since_pm: float) -> dict:
    """Per-run scalar parameters: in-spec unless the fault shifts a channel."""
    params = {}
    sev_map = FAULT_SEVERITY.get(fault, {})
    degradation_excess = DEGRADATION_MAX_EXCESS * min(1.0, days_since_pm / DEGRADATION_DAYS_FULL) if fault == "equipment_degradation" else 0.0
    for name, spec in CHANNEL_SPECS.items():
        value = float(rng.normal(spec.center, spec.noise_sigma))
        if name in sev_map:
            lo, hi = sev_map[name]
            severity = rng.uniform(lo, hi)
            direction = 1.0 if rng.random() < 0.6 else -1.0
            value = spec.center + direction * severity * spec.half_width
        if degradation_excess and name == "vibration":
            value += (0.6 + 0.4 * rng.random()) * degradation_excess * spec.half_width
        params[name] = round(float(np.clip(value, spec.lo - 2 * spec.half_width, spec.hi + 2 * spec.half_width)), 4)
    return params


def _generate_telemetry(rng: np.random.Generator, cfg: SimulatorConfig, run: LotRun, days_since_pm: float) -> pd.DataFrame:
    """Time-series telemetry for one run: shaped by the lot's fault."""
    tel_cfg = cfg.telemetry
    n = tel_cfg.n_samples
    t = np.arange(n) * tel_cfg.sample_interval_seconds
    rows = []

    sev_map = FAULT_SEVERITY.get(run.fault_type, {})
    degradation_excess = DEGRADATION_MAX_EXCESS * min(1.0, days_since_pm / DEGRADATION_DAYS_FULL) if run.fault_type == "equipment_degradation" else 0.0
    drift_biases = SENSOR_DRIFT_BIAS if run.fault_type == "sensor_drift" else {}
    transition_ramp = run.fault_type == "recipe_transition_problem"
    excursion_channels = set()
    if run.fault_type == "none" and rng.random() < EXCURSION_RATE_ON_NORMAL:
        excursion_channels.add(str(rng.choice(tel_cfg.channels)))

    for channel in tel_cfg.channels:
        spec = CHANNEL_SPECS[channel]
        base = run.params.get(channel, spec.center)
        series = rng.normal(base, spec.noise_sigma, size=n)

        if channel in sev_map:
            lo, hi = sev_map[channel]
            severity = rng.uniform(lo, hi)
            slope = direction = (1.0 if rng.random() < 0.65 else -1.0) * severity * spec.half_width / n
            series = series + slope * np.arange(n)
            peak = severity * spec.half_width
            if peak > spec.half_width:
                # Excursion event: log the moment it crosses the band edge.
                run.params.setdefault(f"{channel}_excursion_at", int(np.argmax(np.abs(slope * np.arange(n)) > spec.half_width)))

        if drift_biases:
            bias = drift_biases.get(channel, 0.0) * spec.half_width
            series = series + bias * (np.arange(n) / n)

        if degradation_excess and channel == "vibration":
            series = series + degradation_excess * spec.half_width * (0.5 + 0.5 * np.arange(n) / n)

        if transition_ramp and channel in ("gas_flow", "pressure"):
            ramp_len = min(n, RECOVER_TIME_SECONDS // tel_cfg.sample_interval_seconds)
            offset = rng.uniform(0.6, 1.0) * spec.half_width * (1 if rng.random() < 0.5 else -1)
            series[:ramp_len] += offset

        if channel in excursion_channels:
            start = int(rng.integers(0, n // 2))
            dur = int(rng.integers(3, max(4, n // 6)))
            magnitude = rng.uniform(*EXCURSION_MAGNITUDE) * spec.half_width * (1 if rng.random() < 0.5 else -1)
            series[start : start + dur] += magnitude

        # Missing-sensor artifacts on every run (independent of fault type).
        dropout_mask = rng.random(n) < tel_cfg.dropout_rate
        stuck_mask = rng.random(n) < tel_cfg.stuck_rate
        if stuck_mask.any():
            stuck_val = float(series[stuck_mask][0])
            series[stuck_mask] = stuck_val
        series[dropout_mask] = np.nan

        for i in range(n):
            rows.append(
                {
                    "lot_id": run.lot_id,
                    "equipment_id": run.equipment_id,
                    "chamber_id": run.chamber_id,
                    "parameter": channel,
                    "ts_index": i,
                    "timestamp": (run.process_start + timedelta(seconds=int(t[i]))).isoformat(),
                    "value": None if np.isnan(series[i]) else round(float(series[i]), 4),
                }
            )
    return pd.DataFrame(rows)


def _generate_outcomes(rng: np.random.Generator, cfg: SimulatorConfig, run: LotRun, unexplained_defect_prob: float) -> None:
    """Fault -> mechanism -> (yield, defects, spatial pattern, electrical)."""
    mech = mechanism_for(run.fault_type)

    # --- defect probability: mechanism-driven + unexplained background ---
    p_mech = mech.base_defect_probability if mech else 0.0
    run.defect_probability = float(np.clip(p_mech + unexplained_defect_prob + rng.normal(0, 0.02), 0.001, 0.9))

    # --- yield: baseline minus mechanism loss, minus realized die loss, plus noise ---
    base_yield = 98.5
    mech_loss = mech.yield_loss_pp if mech else 0.0
    realized_loss = run.defect_probability * 12.0  # per-wafer fails cost some yield
    run.yield_pct = float(np.clip(base_yield - mech_loss - realized_loss + rng.normal(0, YIELD_NOISE_SIGMA), 35.0, 99.9))

    # --- spatial signature + defect density ---
    if mech:
        run.spatial_signature = mech.spatial_signature
        run.defect_density = float(np.clip(0.004 + mech.particle_multiplier * BACKGROUND_DEFECT_PROB * rng.uniform(0.8, 1.3), 0.0, 0.35))
        run.electrical_pass_rate = float(np.clip(1.0 - (mech.yield_loss_pp / 100.0) * rng.uniform(0.7, 1.2), 0.3, 1.0))
    else:
        run.spatial_signature = rng.choice(["None", "Random", "Loc"], p=[0.70, 0.20, 0.10])
        run.defect_density = float(np.clip(BACKGROUND_DEFECT_PROB * rng.uniform(0.5, 1.5), 0.0, 0.05))
        run.electrical_pass_rate = float(np.clip(rng.normal(0.985, 0.01), 0.85, 1.0))


def _generate_wafers_and_defects(rng: np.random.Generator, cfg: SimulatorConfig, run: LotRun) -> tuple[list, list]:
    """Per-wafer records + coordinate-level defects following the signature."""
    mech = mechanism_for(run.fault_type)
    wafers, defects = [], []
    signature_pool = [run.spatial_signature] if run.spatial_signature != "None" else ["None"]
    defect_count_multiplier = mech.particle_multiplier if mech else 1.0

    for slot in range(1, cfg.wafers_per_lot + 1):
        wafer_id = f"{run.lot_id}_W{slot:02d}"
        fails = rng.random() < run.defect_probability
        wafers.append(
            {
                "wafer_id": wafer_id,
                "lot_id": run.lot_id,
                "slot": slot,
                "yield_status": "fail" if fails else "pass",
                "true_pattern": run.spatial_signature if fails else "None",
                "defect_density": round(run.defect_density * rng.uniform(0.7, 1.3), 5),
                "electrical_pass_rate": round(run.electrical_pass_rate * rng.uniform(0.97, 1.03), 4),
                "pattern_source": "generator_causal" if fails and mech else "generator_background",
            }
        )
        n_defects = rng.poisson(run.defect_density * 400 * (1.0 if fails else 0.3) * (defect_count_multiplier ** 0.5))
        for _ in range(int(n_defects)):
            if mech and rng.random() < mech.edge_bias:
                angle = rng.uniform(0, 2 * np.pi)
                x, y = 70 * np.cos(angle), 70 * np.sin(angle)
            elif mech and rng.random() < mech.scratch_bias:
                x = float(rng.uniform(-70, 70))
                y = x * rng.uniform(0.9, 1.1) + rng.normal(0, 2.0)
            else:
                x, y = float(rng.uniform(-75, 75)), float(rng.uniform(-75, 75))
            defects.append(
                {
                    "defect_id": f"{wafer_id}_D{len(defects):05d}",
                    "wafer_id": wafer_id,
                    "lot_id": run.lot_id,
                    "defect_type": rng.choice(DEFECT_TYPES, p=[0.32, 0.14, 0.06, 0.18, 0.16, 0.10, 0.04]),
                    "severity": rng.choice(["LOW", "MEDIUM", "HIGH"], p=[0.5, 0.35, 0.15]),
                    "x": round(x, 2),
                    "y": round(y, 2),
                    "size": round(float(rng.uniform(0.05, 2.5)), 3),
                    "timestamp": (run.process_start + timedelta(hours=int(rng.integers(1, 48)))).isoformat(),
                }
            )
    return wafers, defects


def _lot_events(run: LotRun, cfg: SimulatorConfig, maint_events: list, end_of_run: datetime) -> list:
    """Ordered timeline events for one lot."""
    def _event_ts(value) -> str:
        """Maintenance rows carry datetimes; everything here is ISO strings."""
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    events = []
    base = {
        "lot_id": run.lot_id,
        "equipment_id": run.equipment_id,
        "chamber_id": run.chamber_id,
        "recipe_id": run.recipe_id,
    }
    events.append({**base, "event_type": "PROCESS_START", "ts": run.process_start.isoformat(), "payload": {"process_step": run.process_step}})
    for channel, value in run.params.items():
        if channel.endswith("_excursion_at"):
            continue
        spec = CHANNEL_SPECS[channel]
        if value < spec.lo or value > spec.hi:
            at = run.params.get(f"{channel}_excursion_at", cfg.telemetry.run_duration_seconds // 2)
            events.append(
                {
                    **base,
                    "event_type": "PARAMETER_EXCURSION",
                    "ts": (run.process_start + timedelta(seconds=int(at))).isoformat(),
                    "payload": {"parameter": channel, "value": value, "limit": [spec.lo, spec.hi]},
                }
            )
    if run.fault_type == "recipe_transition_problem":
        events.append({**base, "event_type": "RECIPE_TRANSITION", "ts": (run.process_start + timedelta(seconds=30)).isoformat(), "payload": {"from_recipe": run.recipe_id, "to_recipe": run.recipe_id, "note": "transition instability"}})
    if run.fault_type == "equipment_degradation":
        events.append({**base, "event_type": "EQUIPMENT_ALARM", "ts": (run.process_start + timedelta(seconds=cfg.telemetry.run_duration_seconds // 2)).isoformat(), "payload": {"alarm": "vibration_high"}})
    for m in maint_events:
        events.append({**base, "event_type": "MAINTENANCE", "ts": _event_ts(m["timestamp"]), "payload": {"maintenance_event_id": m["event_id"], "maintenance_type": m["event_type"]}})
    events.append({**base, "event_type": "PROCESS_END", "ts": end_of_run.isoformat(), "payload": {"yield": round(run.yield_pct, 2)}})
    events.append({**base, "event_type": "INSPECTION", "ts": end_of_run.isoformat(), "payload": {"defect_density": run.defect_density, "true_pattern": run.spatial_signature}})
    return events


def generate_dataset(cfg: SimulatorConfig) -> dict:
    """Generate the full dataset. Returns DataFrames; does not write to disk."""
    rng = np.random.default_rng(cfg.seed)
    end = _utc(datetime.fromisoformat(cfg.end_date.replace("Z", "+00:00")))
    start = end - timedelta(days=cfg.lookback_days)

    equipment_df, chambers_df = generate_equipment_and_chambers(rng)
    recipes_df = generate_recipes(rng)
    maint_df = generate_maintenance(rng, equipment_df, end, cfg.lookback_days)

    equipment_ids = equipment_df["equipment_id"].tolist()
    chamber_by_eq = chambers_df.groupby("equipment_id")["chamber_id"].apply(list).to_dict()
    recipe_pool = recipes_df.to_dict("records")

    offsets = rng.uniform(0, cfg.lookback_days * 24 * 3600, size=cfg.n_lots)
    lot_starts = sorted(_utc(start + timedelta(seconds=float(o))) for o in offsets)
    lot_ids = [f"LOT_{10000 + i}" for i in range(cfg.n_lots)]
    eq_for_lot = [equipment_ids[i] for i in rng.integers(0, len(equipment_ids), size=cfg.n_lots)]
    faults = _assign_faults(rng, cfg, maint_df, lot_starts, eq_for_lot)

    maint_by_eq: dict = {eq: [] for eq in equipment_ids}
    for _, m in maint_df.iterrows():
        maint_by_eq[m["equipment_id"]].append(m.to_dict())

    lots, telemetry_frames, wafer_rows, defect_rows, event_rows, gt_rows, electrical_rows = [], [], [], [], [], [], []
    status_cutoff = end - timedelta(days=2)

    for i in range(cfg.n_lots):
        lot_id = lot_ids[i]
        eq = eq_for_lot[i]
        chamber_id = str(rng.choice(chamber_by_eq[eq]))
        recipe = recipe_pool[int(rng.integers(0, len(recipe_pool)))]
        fault = faults[i]
        run = LotRun(
            lot_id=lot_id,
            equipment_id=eq,
            chamber_id=chamber_id,
            recipe_id=recipe["recipe_id"],
            process_step=recipe["process_step"],
            product=str(rng.choice(PRODUCTS)),
            process_start=lot_starts[i],
            fault_type=fault,
        )
        days_since_pm = _days_since_last_maintenance(maint_df, eq, lot_starts[i])
        eq_health = float(equipment_df.loc[equipment_df.equipment_id == eq, "health_score"].iloc[0])
        run.params = _sample_params(rng, cfg, fault, eq_health, days_since_pm)

        run.telemetry = _generate_telemetry(rng, cfg, run, days_since_pm)
        telemetry_frames.append(run.telemetry)

        _generate_outcomes(rng, cfg, run, unexplained_defect_prob=BACKGROUND_DEFECT_PROB)
        wafers, defects = _generate_wafers_and_defects(rng, cfg, run)
        wafer_rows.extend(wafers)
        defect_rows.extend(defects)

        end_of_run = run.process_start + timedelta(seconds=cfg.telemetry.run_duration_seconds)
        is_incident = fault != "none"
        lots.append(
            {
                "lot_id": lot_id,
                "product": run.product,
                "recipe_id": run.recipe_id,
                "process_step": run.process_step,
                "equipment_id": eq,
                "chamber_id": chamber_id,
                "process_start": run.process_start.isoformat(),
                "process_end": end_of_run.isoformat(),
                "wafer_count": cfg.wafers_per_lot,
                "status": "completed" if end_of_run < status_cutoff else "in_progress",
                "yield": round(run.yield_pct, 2),
                "is_low_yield": int(run.yield_pct < cfg.low_yield_threshold),
                "defect_probability": round(run.defect_probability, 4),
                "defect_density": round(run.defect_density, 5),
                "spatial_signature": run.spatial_signature,
                "equipment_days_since_pm": round(days_since_pm, 2),
                "is_simulated": True,
                "data_source": "synthetic",
            }
        )
        electrical_rows.append(
            {
                "lot_id": lot_id,
                "vbd_mean": round(cfg.electrical.vbd_mean_normal - (mechanism_for(fault).yield_loss_pp / 100.0) * rng.uniform(0.5, 1.5) if mechanism_for(fault) else cfg.electrical.vbd_mean_normal + rng.normal(0, cfg.electrical.vbd_std), 4),
                "leak_mean": round(cfg.electrical.leak_mean_normal + (mechanism_for(fault).yield_loss_pp / 20.0) * rng.uniform(0.5, 1.5) if mechanism_for(fault) else cfg.electrical.leak_mean_normal + rng.normal(0, cfg.electrical.leak_std), 4),
                "rsq_mean": round(cfg.electrical.rsq_mean_normal - (mechanism_for(fault).yield_loss_pp / 300.0) * rng.uniform(0.5, 1.5) if mechanism_for(fault) else cfg.electrical.rsq_mean_normal + rng.normal(0, cfg.electrical.rsq_std), 4),
                "pass_rate": round(run.electrical_pass_rate, 4),
            }
        )

        gt_rows.append(
            {
                "lot_id": lot_id,
                "true_fault": fault,
                "mechanism": mechanism_for(fault).name if mechanism_for(fault) else "none",
                "spatial_signature": run.spatial_signature,
                "onset_ts_index": int(run.params.get(f"{[c for c in FAULT_SEVERITY.get(fault, {})].pop() if FAULT_SEVERITY.get(fault, {}) else 'pressure'}_excursion_at", cfg.telemetry.run_duration_seconds // 2)) if fault in FAULT_SEVERITY and FAULT_SEVERITY[fault] else 0,
                "process_start": run.process_start.isoformat(),
            }
        )

        maint_events = [m for m in maint_by_eq[eq] if abs((lot_starts[i] - m["timestamp"]).total_seconds()) < 6 * 3600]
        event_rows.extend(_lot_events(run, cfg, maint_events, end_of_run))

    telemetry_df = pd.concat(telemetry_frames, ignore_index=True)
    lots_df = pd.DataFrame(lots)
    gt_df = pd.DataFrame(gt_rows)
    return {
        "lots": lots_df,
        "equipment": equipment_df,
        "chambers": chambers_df,
        "recipes": recipes_df,
        "maintenance": maint_df,
        "sensor_data": telemetry_df,
        "wafers": pd.DataFrame(wafer_rows),
        "defects": pd.DataFrame(defect_rows),
        "electrical_tests": pd.DataFrame(electrical_rows),
        "events": pd.DataFrame(event_rows),
        "ground_truth": gt_df,
        # Phase 5: simulated engineer responses to low-yield lots (separate RNG
        # stream — every table above is unchanged by adding this one).
        "interventions": generate_interventions(lots_df, gt_df, cfg.seed, cfg.low_yield_threshold),
    }
