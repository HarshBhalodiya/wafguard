"""
Synthetic intervention history (Phase 5, brief P4).

SYNTHETIC DATA. The generated lots record no engineering actions, so the brief's
"historical interventions" table is simulated here, as a separate, documented
overlay. It does NOT change any lot, wafer or sensor row; it adds one record per
simulated engineer response to a low-yield lot:

    trigger lot (low yield) -> engineer diagnosis -> action from the KB
        -> verification-lot yield after the action

How each field is produced (all seeded, all constants named below):

  * Trigger: every low-yield lot (yield < threshold) whose tool has had no
    simulated intervention in the previous COOLDOWN_HOURS.
  * Diagnosis: the engineer's diagnosis, in the root-cause engine's candidate
    vocabulary. With probability DIAGNOSIS_ACCURACY it is a candidate that fits
    the lot's hidden fault (FAULT_DIAGNOSES); otherwise any KB cause at random
    (a misdiagnosis). Faults with no fitting candidate are always "misdiagnosed".
  * Action: uniform over the KB's actions for the diagnosed cause.
  * Outcome: the action fixes the problem with probability ACTION_EFFICACY
    [action][hidden fault] (default FALLBACK_EFFICACY when the action does not
    address that fault). Fixed -> the verification lot's yield is drawn from the
    fab's healthy-yield distribution; not fixed -> the problem persists
    (yield_before + small noise).

The hidden fault is used ONLY inside this module (the simulator owns ground
truth). The written table holds the engineer's *diagnosis*, never the true
fault, so the recommendation engine that reads it cannot see ground truth.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.knowledge import load_engineering_kb

INTERVENTION_SEED_OFFSET = 7_919   # separate RNG stream: the lot data stays byte-identical
COOLDOWN_HOURS = 12.0
DIAGNOSIS_ACCURACY = 0.70
FALLBACK_EFFICACY = 0.12           # an action that does not address the fault rarely "works"
PERSIST_NOISE = (1.0, 3.0)         # mean / sd of the yield change when nothing was fixed
ACTION_DELAY_HOURS = (2.0, 10.0)   # verification-lot timing after the trigger lot ends

# Which candidates an engineer could reasonably name for each hidden fault.
FAULT_DIAGNOSES: dict[str, list[str]] = {
    "rf_power_drift": ["RF Power"],
    "chamber_pressure_instability": ["Pressure"],
    "recipe_transition_problem": ["Gas Flow", "Pressure"],
    "sensor_drift": ["Temperature", "RF Power", "Pressure", "Gas Flow"],
    "equipment_degradation": ["Maintenance Overdue", "Equipment Failure History", "Equipment Yield History"],
    "contamination_event": ["Pressure", "Equipment Yield History"],
    "sensor_dropout": [],
    "none": [],
}

# P(fix | action, hidden fault). Actions absent for a fault use FALLBACK_EFFICACY.
ACTION_EFFICACY: dict[str, dict[str, float]] = {
    "rf_generator_recalibration": {"rf_power_drift": 0.85, "sensor_drift": 0.35},
    "rf_match_network_service": {"rf_power_drift": 0.70},
    "rf_peer_comparison": {"rf_power_drift": 0.30},
    "throttle_valve_service": {"chamber_pressure_instability": 0.80, "recipe_transition_problem": 0.30},
    "vacuum_leak_check": {"chamber_pressure_instability": 0.45, "contamination_event": 0.30},
    "chamber_wet_clean": {"contamination_event": 0.85, "chamber_pressure_instability": 0.35},
    "thermocouple_recalibration": {"sensor_drift": 0.60},
    "temperature_loop_tuning": {"sensor_drift": 0.40},
    "mfc_recalibration": {"recipe_transition_problem": 0.55, "sensor_drift": 0.40},
    "gas_line_purge": {"contamination_event": 0.50, "recipe_transition_problem": 0.30},
    "recipe_transition_review": {"recipe_transition_problem": 0.80},
    "recipe_timing_review": {"recipe_transition_problem": 0.40},
    "power_supply_check": {"rf_power_drift": 0.20},
    "current_draw_check": {"rf_power_drift": 0.15},
    "chamber_health_review": {"equipment_degradation": 0.45, "contamination_event": 0.40},
    "seasoning_run": {"contamination_event": 0.55, "equipment_degradation": 0.25},
    "component_replacement": {"equipment_degradation": 0.75},
    "failure_history_review": {"equipment_degradation": 0.30},
    "preventive_maintenance": {"equipment_degradation": 0.85, "contamination_event": 0.35},
    "vibration_inspection": {"equipment_degradation": 0.60},
}

INTERVENTION_COLUMNS = [
    "intervention_id", "trigger_lot_id", "equipment_id", "process_step", "timestamp",
    "diagnosed_cause", "action_id", "action_text", "yield_before", "yield_after",
    "yield_change", "recovered", "is_simulated", "data_source",
]


def generate_interventions(
    lots: pd.DataFrame,
    ground_truth: pd.DataFrame,
    seed: int,
    low_yield_threshold: float,
) -> pd.DataFrame:
    """Simulate the engineer-response history for a generated dataset.

    `lots` needs lot_id, equipment_id (or equipment), process_step, process_end
    (or process_start) and yield; `ground_truth` needs lot_id and true_fault.
    Returns a DataFrame with INTERVENTION_COLUMNS (empty if no low-yield lots).
    """
    rng = np.random.default_rng(seed + INTERVENTION_SEED_OFFSET)
    kb_causes: dict = load_engineering_kb()["causes"]
    all_causes = sorted(kb_causes)

    lots = lots.rename(columns={"equipment": "equipment_id"}) if "equipment_id" not in lots.columns else lots
    end_col = "process_end" if "process_end" in lots.columns else "process_start"
    frame = lots[["lot_id", "equipment_id", "process_step", end_col, "yield"]].merge(
        ground_truth[["lot_id", "true_fault"]], on="lot_id", how="left"
    )
    # `yield` is a Python keyword, so itertuples() would mangle it — rename first.
    frame = frame.rename(columns={"yield": "lot_yield"})
    frame["ended_at"] = pd.to_datetime(frame[end_col], utc=True)
    frame = frame.sort_values("ended_at", kind="stable").reset_index(drop=True)

    healthy = frame.loc[frame["lot_yield"] >= low_yield_threshold, "lot_yield"]
    healthy_mean = float(healthy.mean()) if len(healthy) else 95.0
    healthy_sd = float(healthy.std()) if len(healthy) > 1 else 2.0

    last_action_at: dict[str, pd.Timestamp] = {}
    rows: list[dict] = []
    for row in frame.itertuples(index=False):
        if row.lot_yield >= low_yield_threshold:
            continue
        lot_yield = float(row.lot_yield)
        previous = last_action_at.get(row.equipment_id)
        if previous is not None and (row.ended_at - previous).total_seconds() < COOLDOWN_HOURS * 3600:
            continue

        fault = row.true_fault if isinstance(row.true_fault, str) else "none"
        fitting = [c for c in FAULT_DIAGNOSES.get(fault, []) if c in kb_causes]
        if fitting and rng.random() < DIAGNOSIS_ACCURACY:
            diagnosis = str(rng.choice(fitting))
        else:
            diagnosis = str(rng.choice(all_causes))
        actions = kb_causes[diagnosis]
        action = actions[int(rng.integers(0, len(actions)))]

        p_fix = ACTION_EFFICACY.get(action["id"], {}).get(fault, FALLBACK_EFFICACY)
        if rng.random() < p_fix:
            yield_after = rng.normal(healthy_mean, healthy_sd)
        else:
            yield_after = lot_yield + rng.normal(*PERSIST_NOISE)
        yield_after = float(np.clip(yield_after, 35.0, 99.9))

        when = row.ended_at + pd.Timedelta(hours=float(rng.uniform(*ACTION_DELAY_HOURS)))
        last_action_at[row.equipment_id] = when
        rows.append(
            {
                "intervention_id": f"INT_{len(rows):05d}",
                "trigger_lot_id": row.lot_id,
                "equipment_id": row.equipment_id,
                "process_step": row.process_step,
                "timestamp": when.isoformat(),
                "diagnosed_cause": diagnosis,
                "action_id": action["id"],
                "action_text": action["text"].format(equipment=row.equipment_id, peers="peer tools"),
                "yield_before": round(lot_yield, 2),
                "yield_after": round(yield_after, 2),
                "yield_change": round(yield_after - lot_yield, 2),
                "recovered": bool(yield_after >= low_yield_threshold),
                "is_simulated": True,
                "data_source": "synthetic_intervention_model",
            }
        )
    return pd.DataFrame(rows, columns=INTERVENTION_COLUMNS)
