"""
Multi-signal root-cause engine (spec section 14), upgraded in Phase 3.

For a lot, ranks candidate causes (RF Power, Temperature, ...) by combining
six signals — SHAP attribution, parameter deviation, historical
occurrence, equipment association, wafer-pattern evidence, and temporal
(within-run shape) evidence — using the weights in app.config.ROOTCAUSE_WEIGHTS.

Phase 3 changes (docs/phase3_plan.md, DECISIONS D-021..D-024):

  * wafer-pattern evidence is now a **measured** pattern→cause association
    table (built on the train split by scripts/build_evidence_tables.py
    and consumed here as a versioned JSON artifact) — the authored
    pattern-boost dict (audit item D-004) is gone.
  * equipment association is now **measured** equipment↔fault statistics
    (excursion / failure / maintenance / low-yield rates from
    timeline_events + maintenance + lots, strictly before this lot),
    shrunk toward the fab baseline for low-sample tools, with sample sizes
    reported in the evidence.
  * extended Phase 2A features (slope, excursions, time-outside-range)
    feed the SHAP groups and the evidence items.
  * temporal evidence uses the per-channel drift *shape* (OLS ramp +
    end-drift + persistence) instead of a single last-minus-first delta.
  * every evidence item carries a stable `evidence_id` so recommendations
    can ground themselves in it (P3-6).

Terminology discipline (spec section 45): this produces "root-cause
candidates" with a confidence band, never a "confirmed root cause". Every
evidence item is tagged FACT / MODEL_OUTPUT / INFERENCE with a source so
the UI can be honest about what kind of claim it's showing.
"""
import json
import re
from collections import Counter
from datetime import datetime
from statistics import median
from typing import Optional

from app.config import (
    LOW_YIELD_THRESHOLD,
    PROCESS_PARAMETER_RANGES,
    ROOTCAUSE_CONFIDENCE_THRESHOLDS,
    ROOTCAUSE_WEIGHTS,
    get_settings,
)
from app.database.repositories.lot_repository import LotRepository
from app.database.repositories.prediction_repository import PredictionRepository
from app.database.repositories.rootcause_repository import RootCauseRepository
from app.database.repositories.wafer_repository import WaferRepository
from app.services import risk_service, yield_service
from app.utils.exceptions import NotFoundError
from app.utils.logging import get_logger
from app.utils.time import utc_now

logger = get_logger(__name__)

# candidate display name -> (base parameter for deviation lookups, underlying feature columns for SHAP grouping)
# Phase 2A extended columns (slope / excursions / time_outside_pct) join the SHAP groups so
# their attribution is credited to a candidate instead of floating unattributed (P3-3).
CANDIDATE_DEFINITIONS = {
    "RF Power": ("rf_power", ["rf_power_mean", "rf_power_max", "rf_power_std", "rf_power_drift", "rf_power_slope", "rf_power_excursions", "rf_power_time_outside_pct"]),
    "Temperature": ("temperature", ["temperature_mean", "temperature_max", "temperature_std", "temperature_drift", "temperature_slope", "temperature_excursions", "temperature_time_outside_pct"]),
    "Pressure": ("pressure", ["pressure_mean", "pressure_max", "pressure_std", "pressure_drift", "pressure_slope", "pressure_excursions", "pressure_time_outside_pct"]),
    "Gas Flow": ("gas_flow", ["gas_flow_mean", "gas_flow_max", "gas_flow_std", "gas_flow_drift", "gas_flow_slope", "gas_flow_excursions", "gas_flow_time_outside_pct"]),
    "Etch Time": ("etch_time", ["etch_time"]),
    "Voltage": ("voltage", ["voltage"]),
    "Current": ("current", ["current"]),
    "Equipment Yield History": (None, ["previous_lot_yield", "previous_5_lot_yield"]),
    "Equipment Failure History": (None, ["equipment_failure_count"]),
    "Maintenance Overdue": (None, ["maintenance_days_since_last"]),
}

# Simulator telemetry shape: 480 s run at 10 s sampling = 48 readings/channel/run
# (backend/app/simulator/config.json -> telemetry). Used only to phrase the trend
# across the whole run from the per-reading OLS slope; documented rule, not a guess.
N_READINGS_PER_RUN = 48

HIGH_DEVIATION_THRESHOLD = 0.8  # a past lot "counts" as having this cause if deviation exceeds this
LOW_SAMPLE_LOTS = 10            # below this many prior lots on a tool, its rates are flagged
SHRINKAGE_PSEUDO_LOTS = 10.0    # pseudo-lots pulling a tool's rate toward the fab baseline
MODEL_NAME = "rootcause_multi_signal_v2"

PATTERN_TABLE_FILE = "pattern_cause_associations.v1.json"

# Lazily-loaded measured association table (see _get_pattern_cause_table).
_PATTERN_TABLE_CACHE: dict = {"loaded": False, "table": None}


def set_pattern_cause_table(table: Optional[dict]) -> None:
    """Inject the measured pattern→cause table (tests / evaluation harness).
    Pass None to explicitly disable the table; call reset_pattern_cause_table()
    to return to the default on-disk artifact lookup."""
    _PATTERN_TABLE_CACHE["loaded"] = True
    _PATTERN_TABLE_CACHE["table"] = table


def reset_pattern_cause_table() -> None:
    _PATTERN_TABLE_CACHE["loaded"] = False
    _PATTERN_TABLE_CACHE["table"] = None


def _pattern_table_path():
    settings = get_settings()
    return settings.resolve_path(settings.model_dir) / "evidence" / PATTERN_TABLE_FILE


def _get_pattern_cause_table() -> Optional[dict]:
    """Measured pattern→cause table written by scripts/build_evidence_tables.py.
    Returns None (signal 0, with an honest note) when the artifact hasn't been built."""
    if _PATTERN_TABLE_CACHE["loaded"]:
        return _PATTERN_TABLE_CACHE["table"]
    path = _pattern_table_path()
    table = None
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                table = json.load(f)
        except (OSError, json.JSONDecodeError):
            logger.exception("Pattern-cause table at %s is unreadable; wafer-pattern evidence disabled.", path)
            table = None
    _PATTERN_TABLE_CACHE["loaded"] = True
    _PATTERN_TABLE_CACHE["table"] = table
    return table


def _representative_value(feature_snapshot: dict, base_param: Optional[str]) -> Optional[float]:
    if base_param is None:
        return None
    if f"{base_param}_mean" in feature_snapshot:
        return feature_snapshot[f"{base_param}_mean"]
    return feature_snapshot.get(base_param)


def _dominant_wafer_pattern(wafers: list) -> str:
    labels = [w.get("wafer_map_pattern") or w.get("synthetic_true_pattern") for w in wafers]
    labels = [l for l in labels if l and l != "None"]
    if not labels:
        return "None"
    return Counter(labels).most_common(1)[0][0]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# --------------------------------------------------------------------------------------
# Measured equipment↔fault statistics (P3-2). Replaces the old anomaly_status text-match.
# --------------------------------------------------------------------------------------
def _relative_rate_score(n_events: int, n_lots: int, fab_events: int, fab_lots: int) -> tuple[float, dict]:
    """0 at the fab baseline rate, 1.0 at >=2x the baseline, after small-sample shrinkage.

    A tool with few prior lots is shrunk toward the fab baseline (empirical-Bayes style,
    SHRINKAGE_PSEUDO_LOTS pseudo-lots), so low-N equipment is never over-trusted.
    """
    rate = (n_events + 1) / (n_lots + 2)
    fab_rate = (fab_events + 1) / (fab_lots + 2)
    shrunk = (n_lots * rate + SHRINKAGE_PSEUDO_LOTS * fab_rate) / (n_lots + SHRINKAGE_PSEUDO_LOTS)
    relative = shrunk / fab_rate
    score = max(0.0, min(1.0, relative - 1.0))
    return score, {
        "rate": rate,
        "fab_rate": fab_rate,
        "relative": round(relative, 3),
        "low_sample": n_lots < LOW_SAMPLE_LOTS,
        "n_lots": n_lots,
    }


def _fab_baselines(db, before_ts: str) -> dict:
    """Fab-wide rates measured strictly before a lot's process_start. Computed once per call."""
    n_lots = db.lots.count_documents({"process_start": {"$lt": before_ts}}) or 1

    exc_docs = list(
        db.timeline_events.aggregate(
            [
                {"$match": {"event_type": "PARAMETER_EXCURSION", "ts": {"$lt": before_ts}}},
                {"$group": {"_id": "$payload.parameter", "n": {"$sum": 1}}},
            ]
        )
    )
    excursions = {d["_id"]: d["n"] for d in exc_docs}
    alarms = db.timeline_events.count_documents({"event_type": "EQUIPMENT_ALARM", "ts": {"$lt": before_ts}})
    repairs = db.maintenance.count_documents({"event_type": "unscheduled_repair", "timestamp": {"$lt": before_ts}})
    low_yield = db.lots.count_documents(
        {"process_start": {"$lt": before_ts}, "yield": {"$lt": LOW_YIELD_THRESHOLD}}
    )

    return {
        "n_lots": n_lots,
        "excursions": excursions,
        "n_excursions": sum(excursions.values()),
        "alarms": alarms,
        "repairs": repairs,
        "low_yield_rate": low_yield / n_lots,
        "median_pm_cadence_days": _median_pm_cadence_days(db),
    }


def _median_pm_cadence_days(db) -> Optional[float]:
    """Measured median gap (days) between consecutive maintenance events on the same tool."""
    docs = list(db.maintenance.find({}, {"equipment_id": 1, "timestamp": 1}))
    by_tool: dict[str, list[str]] = {}
    for d in docs:
        eq, ts = d.get("equipment_id"), d.get("timestamp")
        if eq and ts:
            by_tool.setdefault(eq, []).append(str(ts))
    gaps: list[float] = []
    for ts_list in by_tool.values():
        ordered = sorted(datetime.fromisoformat(t) for t in ts_list)
        for a, b in zip(ordered, ordered[1:]):
            gaps.append((b - a).total_seconds() / 86400.0)
    return round(median(gaps), 1) if gaps else None


def _prefetch_equipment_stats(db, lot: dict, fab: dict) -> dict:
    """P7-5: one prefetched bundle of per-equipment counts, replacing the
    2-per-candidate count_documents fan-out (23 round-trips on the profile).
    Returns the exact counters _equipment_association needs; None scores are
    identical because the count inputs are identical."""
    equipment_id = lot.get("equipment")
    before_ts = str(lot["process_start"])
    if not equipment_id:
        return {}
    equip_query = {"$or": [{"equipment": equipment_id}, {"equipment_id": equipment_id}]}
    n_prior_lots = db.lots.count_documents({**equip_query, "process_start": {"$lt": before_ts}})
    n_repairs = db.maintenance.count_documents(
        {"equipment_id": equipment_id, "event_type": "unscheduled_repair", "timestamp": {"$lt": before_ts}}
    )
    n_alarms = db.timeline_events.count_documents(
        {"equipment_id": equipment_id, "event_type": "EQUIPMENT_ALARM", "ts": {"$lt": before_ts}}
    )
    # Per-parameter excursion counts in ONE aggregation instead of one query per candidate.
    exc_docs = list(
        db.timeline_events.aggregate(
            [
                {
                    "$match": {
                        "equipment_id": equipment_id,
                        "event_type": "PARAMETER_EXCURSION",
                        "ts": {"$lt": before_ts},
                    }
                },
                {"$group": {"_id": "$payload.parameter", "n": {"$sum": 1}}},
            ]
        )
    )
    excursions_by_param = {d["_id"]: d["n"] for d in exc_docs}
    return {
        "n_prior_lots": n_prior_lots,
        "n_repairs": n_repairs,
        "n_alarms": n_alarms,
        "excursions_by_param": excursions_by_param,
        "_exc_fetched": True,  # prefetch path ran the one-off aggregation
    }


def _equipment_association(
    db,
    lot: dict,
    candidate: str,
    base_param: Optional[str],
    feature_snapshot: dict,
    fab: dict,
    equip_stats: Optional[dict] = None,
) -> tuple[float, Optional[dict]]:
    """Measured equipment↔fault association for one candidate. Returns (score, evidence_item_or_None).

    P7-5: ``equip_stats`` carries the prefetched counts (preferred path). When
    omitted (back-compat for existing tests/direct callers) the old per-call
    count_documents path runs unchanged."""
    equipment_id = lot.get("equipment")
    before_ts = str(lot["process_start"])
    if not equipment_id:
        return 0.0, None

    if equip_stats is None:  # legacy per-call path (used by some unit tests)
        equip_query = {"$or": [{"equipment": equipment_id}, {"equipment_id": equipment_id}]}
        equip_stats = {
            "n_prior_lots": db.lots.count_documents({**equip_query, "process_start": {"$lt": before_ts}}),
            "n_repairs": db.maintenance.count_documents(
                {"equipment_id": equipment_id, "event_type": "unscheduled_repair", "timestamp": {"$lt": before_ts}}
            ),
            "n_alarms": db.timeline_events.count_documents(
                {"equipment_id": equipment_id, "event_type": "EQUIPMENT_ALARM", "ts": {"$lt": before_ts}}
            ),
            "excursions_by_param": {},
        }
    n_prior_lots = equip_stats["n_prior_lots"]

    if base_param is not None:
        n_exc = equip_stats["excursions_by_param"].get(base_param, 0)
        if not equip_stats.get("_exc_fetched"):
            # legacy-path fallback: the one-off aggregation was not run for this call
            n_exc = db.timeline_events.count_documents(
                {
                    "equipment_id": equipment_id,
                    "event_type": "PARAMETER_EXCURSION",
                    "payload.parameter": base_param,
                    "ts": {"$lt": before_ts},
                }
            )
        score, detail = _relative_rate_score(n_exc, n_prior_lots, fab["excursions"].get(base_param, 0), fab["n_lots"])
        label = (
            f"{equipment_id} logged {n_exc} '{base_param}' excursion events across its prior "
            f"{n_prior_lots} lots vs fab baseline {detail['fab_rate'] * 100:.1f}%/lot "
            f"({detail['relative']:.2f}x after small-sample shrinkage)."
        )
        if detail["low_sample"]:
            label += f" Low sample size ({n_prior_lots} prior lots < {LOW_SAMPLE_LOTS}) — treat with caution."
    elif candidate == "Equipment Failure History":
        n_fail = equip_stats["n_repairs"] + equip_stats["n_alarms"]
        fab_fail = fab["repairs"] + fab["alarms"]
        score, detail = _relative_rate_score(n_fail, n_prior_lots, fab_fail, fab["n_lots"])
        label = (
            f"{equipment_id} logged {n_fail} failure events ({equip_stats['n_repairs']} unscheduled repairs, {equip_stats['n_alarms']} alarms) "
            f"across its prior {n_prior_lots} lots vs fab baseline {detail['fab_rate'] * 100:.1f}%/lot "
            f"({detail['relative']:.2f}x after small-sample shrinkage)."
        )
        if detail["low_sample"]:
            label += f" Low sample size ({n_prior_lots} prior lots < {LOW_SAMPLE_LOTS}) — treat with caution."
    elif candidate == "Maintenance Overdue":
        days = float(feature_snapshot.get("maintenance_days_since_last", 999.0))
        cadence = fab.get("median_pm_cadence_days")
        if not cadence:
            return 0.0, None
        if days >= 999.0:
            score, label = 1.0, (
                f"{equipment_id} has no recorded maintenance event in the dataset window "
                f"(measured fab PM cadence is {cadence:.0f} days)."
            )
        else:
            score = max(0.0, min(1.0, (days - 0.5 * cadence) / cadence))
            label = (
                f"Last maintenance on {equipment_id} was {days:.0f} days before this lot vs the measured "
                f"fab PM cadence of {cadence:.0f} days (median gap between consecutive maintenance events)."
            )
    elif candidate == "Equipment Yield History":
        prev10 = float(feature_snapshot.get("equipment_prev10_low_yield_rate", 0.0) or 0.0)
        fab_rate = fab["low_yield_rate"]
        score = max(0.0, min(1.0, (prev10 - fab_rate) / max(fab_rate, 0.01)))
        label = (
            f"Previous lots on {equipment_id} went low-yield at {prev10:.0%} (rolling window of up to 10 prior lots) "
            f"vs fab-wide {fab_rate:.0%} measured before this lot."
        )
    else:
        return 0.0, None

    if score <= 0.05:
        return score, None
    return score, {
        "signal": "equipment_association",
        "label": label,
        "value": round(score, 4),
        "kind": "FACT",
        "source": "measured from timeline_events / maintenance / lots aggregation (strictly before this lot)",
        "timestamp": utc_now(),
    }


def _historical_occurrence(past_predictions: list[dict], base_param: Optional[str]) -> tuple[float, Optional[dict]]:
    if base_param is None or not past_predictions:
        return 0.0, None
    concerning = [p for p in past_predictions if p.get("risk_level") in ("HIGH", "MEDIUM")]
    if not concerning:
        return 0.0, None
    hits = 0
    for p in concerning:
        snap = p.get("features_snapshot", {})
        value = _representative_value(snap, base_param)
        if value is not None and risk_service.raw_parameter_deviation(base_param, value) > HIGH_DEVIATION_THRESHOLD:
            hits += 1
    score = hits / len(concerning)
    if score <= 0.05:
        return score, None
    detail = {
        "signal": "historical_occurrence",
        "label": (
            f"{base_param.replace('_', ' ').title()} was also abnormal in {hits} of this equipment's "
            f"{len(concerning)} recent at-risk lots (measured from prediction history)."
        ),
        "value": round(score, 4),
        "kind": "FACT",
        "source": "predictions history",
        "timestamp": utc_now(),
    }
    return score, detail


# --------------------------------------------------------------------------------------
# Wafer-pattern evidence — measured association table (P3-1).
# --------------------------------------------------------------------------------------
def _association_for(table: dict, pattern: str, candidate: str) -> Optional[dict]:
    """Lookup in the measured table written by scripts/build_evidence_tables.py."""
    for entry in table.get("associations", []):
        if entry["pattern"] == pattern and entry["candidate"] == candidate:
            return entry
    return None


def _wafer_pattern_signal(dominant_pattern: str, candidate: str, table: Optional[dict]) -> tuple[float, Optional[dict]]:
    if dominant_pattern == "None":
        return 0.0, None
    if table is None:
        return 0.0, {
            "signal": "wafer_pattern",
            "label": (
                f"Wafer maps show a '{dominant_pattern}' pattern, but no measured pattern-cause table is built — "
                "run scripts/build_evidence_tables.py to enable pattern evidence."
            ),
            "value": 0.0,
            "kind": "INFERENCE",
            "source": f"missing artifact: {PATTERN_TABLE_FILE}",
            "timestamp": utc_now(),
        }
    entry = _association_for(table, dominant_pattern, candidate)
    if entry is None:
        return 0.0, None
    src = table.get("source", {})
    evidence = {
        "signal": "wafer_pattern",
        "label": (
            f"Wafer maps show a '{dominant_pattern}' pattern; the measured association table puts "
            f"{entry['rate'] * 100:.0f}% of '{dominant_pattern}' historical lots at {candidate} "
            f"({entry['hits']} of {entry['pattern_lots']} train-split lots, smoothed {entry['smoothed_rate']:.0%}). "
            "An association, not a confirmation."
        ),
        "value": round(entry["smoothed_rate"], 4),
        "kind": "INFERENCE",
        "source": (
            f"measured table {table.get('table_name', 'pattern_cause_associations')} "
            f"v{table.get('table_version', '?')} ({src.get('split', 'train split')}; {src.get('dataset', 'synthetic history labels')})"
        ),
        "timestamp": utc_now(),
    }
    return float(entry["smoothed_rate"]), evidence


# --------------------------------------------------------------------------------------
# Temporal evidence — per-channel drift shape (P3-4).
# --------------------------------------------------------------------------------------
def _temporal_shape(base_param: Optional[str], candidate: str, feature_snapshot: dict) -> tuple[float, Optional[dict]]:
    if base_param is None or base_param not in PROCESS_PARAMETER_RANGES:
        return 0.0, None
    slope = feature_snapshot.get(f"{base_param}_slope")
    drift = feature_snapshot.get(f"{base_param}_drift")
    time_outside = feature_snapshot.get(f"{base_param}_time_outside_pct")
    if slope is None and drift is None and time_outside is None:
        return 0.0, None

    lo, hi = PROCESS_PARAMETER_RANGES[base_param]
    half_width = (hi - lo) / 2
    ramp = max(0.0, min(1.0, abs(slope) * N_READINGS_PER_RUN / (0.6 * half_width))) if slope is not None else 0.0
    end = max(0.0, min(1.0, abs(drift) / (0.6 * half_width))) if drift is not None else 0.0
    persist = max(0.0, min(1.0, float(time_outside or 0.0)))
    score = 0.4 * ramp + 0.3 * end + 0.3 * persist

    if score <= 0.05:
        return score, None

    if ramp >= 0.15 and ramp >= 0.7 * max(end, 1e-6):
        shape = "steady ramp across the run"
    elif end >= 0.15 and end > 1.5 * max(ramp, 1e-6):
        shape = "step change (end state far from start, no matching trend)"
    elif persist >= 0.30:
        shape = "persistent out-of-band readings"
    else:
        shape = "minor within-run variation"

    slope_txt = f"{slope:+.4f}/reading (~{(slope * N_READINGS_PER_RUN):+.2f} over {N_READINGS_PER_RUN} readings)" if slope is not None else "n/a"
    drift_txt = f"{drift:+.2f}" if drift is not None else "n/a"
    evidence = {
        "signal": "temporal_evidence",
        "label": (
            f"{candidate} channel shape: {shape} — OLS slope {slope_txt}, end-vs-start drift {drift_txt}, "
            f"{persist * 100:.0f}% of readings outside the {lo}-{hi} band."
        ),
        "value": round(score, 4),
        "kind": "FACT",
        "source": "sensor_data feature snapshot (per-channel slope / drift / time-outside-range, extended features D-016)",
        "timestamp": utc_now(),
    }
    return score, evidence


def _confidence_band(score: float) -> str:
    if score >= ROOTCAUSE_CONFIDENCE_THRESHOLDS["high_min"]:
        return "HIGH"
    if score >= ROOTCAUSE_CONFIDENCE_THRESHOLDS["medium_min"]:
        return "MEDIUM"
    return "LOW"


def _core_evidence_items(
    candidate: str,
    base_param: Optional[str],
    signals: dict,
    feature_snapshot: dict,
    hist_detail: Optional[dict],
    equip_evidence: Optional[dict],
    temporal_evidence: Optional[dict],
    model_version: str,
) -> list[dict]:
    items = []
    now = utc_now()

    if signals["shap_attribution"] > 0.05:
        items.append(
            {
                "signal": "shap_attribution",
                "label": f"Strong model contributor to the low-yield probability ({signals['shap_attribution'] * 100:.0f}% of total attribution).",
                "value": round(signals["shap_attribution"], 4),
                "kind": "MODEL_OUTPUT",
                "source": f"lot_yield_classifier {model_version}",
                "timestamp": now,
            }
        )
    if base_param is not None and signals["parameter_deviation"] > 0.05:
        value = _representative_value(feature_snapshot, base_param)
        items.append(
            {
                "signal": "parameter_deviation",
                "label": f"{candidate} reading ({value:.1f}) deviates {signals['parameter_deviation'] * 100:.0f}% of the way to/beyond the historical normal-range edge.",
                "value": round(signals["parameter_deviation"], 4),
                "kind": "FACT",
                "source": "process_runs",
                "timestamp": now,
            }
        )
    # P3-3: excursion / time-outside evidence from the extended feature set.
    if base_param is not None:
        excursions = feature_snapshot.get(f"{base_param}_excursions")
        time_outside = feature_snapshot.get(f"{base_param}_time_outside_pct")
        if excursions and excursions > 0:
            lo, hi = PROCESS_PARAMETER_RANGES.get(base_param, (None, None))
            pct = (time_outside or 0.0) * 100
            items.append(
                {
                    "signal": "parameter_deviation",
                    "label": (
                        f"{excursions:.0f} readings outside the {base_param} spec band [{lo}, {hi}] "
                        f"({pct:.0f}% of the run) — persistent deviation, not a single spike."
                        if lo is not None
                        else f"{excursions:.0f} readings outside the {base_param} spec band ({pct:.0f}% of the run)."
                    ),
                    "value": round(float(excursions), 4),
                    "kind": "FACT",
                    "source": "sensor_data via feature snapshot (extended features: excursions / time-outside-range, D-016)",
                    "timestamp": now,
                }
            )
    if hist_detail is not None:
        items.append(hist_detail)
    if equip_evidence is not None:
        items.append(equip_evidence)
    if temporal_evidence is not None:
        items.append(temporal_evidence)
    return items


def compute_root_cause(db, lot_id: str, top_n: int = 5) -> dict:
    # P7-5: hierarchical timing spans for the engine's stages, filed into the
    # process MetricStore (best-effort — never fail a diagnosis over telemetry).
    try:
        from app.ops.registry import METRIC_STORE
        root_span = METRIC_STORE.span("rootcause:compute")
        span_ctx = root_span.__enter__()
    except Exception:  # pragma: no cover — telemetry must never break serving
        root_span, span_ctx = None, None
    try:
        return _compute_root_cause_inner(db, lot_id, top_n, span_ctx)
    finally:
        if root_span is not None:
            try:
                root_span.__exit__(None, None, None)
            except Exception:  # pragma: no cover
                pass


def _compute_root_cause_inner(db, lot_id: str, top_n: int, root_span) -> dict:
    def span(name):
        """Open a child span, or a no-op context manager when telemetry is off."""
        if root_span is None:
            import contextlib
            return contextlib.nullcontext()
        return root_span.child(name)

    with span("db:fetch"):
        lot_repo = LotRepository(db)
    prediction_repo = PredictionRepository(db)
    wafer_repo = WaferRepository(db)

    lot = lot_repo.get_by_lot_id(lot_id)
    if not lot:
        raise NotFoundError(f"Lot {lot_id} was not found.")

    prediction = prediction_repo.latest_for_lot(lot_id)
    if not prediction:
        raise NotFoundError(
            f"No prediction found for lot {lot_id} yet. Run scripts/train_yield_model.py "
            "(it backfills predictions) or POST /api/yield/predict for this lot first."
        )

    feature_snapshot = prediction["features_snapshot"]
    wafers = wafer_repo.by_lot(lot_id)
    dominant_pattern = _dominant_wafer_pattern(wafers)
    past_predictions = prediction_repo.for_equipment(lot["equipment"], exclude_lot_id=lot_id)
    fab = _fab_baselines(db, str(lot["process_start"]))
    pattern_table = _get_pattern_cause_table()

    with span("ml:shap"):
        all_contributions = yield_service.explain_lot(feature_snapshot, top_n=len(feature_snapshot) + 20)
    shap_by_feature = {c.feature: c.shap_value for c in all_contributions}
    total_abs_shap = sum(abs(v) for v in shap_by_feature.values()) or 1.0

    candidates = []
    # P7-5: per-equipment event stats prefetched once (3 round-trips) instead of
    # 2 queries per candidate — on a remote cluster that fan-out dominated p95.
    with span("db:equip_stats"):
        equip_stats = _prefetch_equipment_stats(db, lot, fab)
    for candidate, (base_param, feature_cols) in CANDIDATE_DEFINITIONS.items():
        shap_abs_sum = sum(abs(shap_by_feature.get(col, 0.0)) for col in feature_cols)
        shap_score = shap_abs_sum / total_abs_shap

        value = _representative_value(feature_snapshot, base_param)
        parameter_deviation = min(1.0, risk_service.raw_parameter_deviation(base_param, value)) if base_param else 0.0
        historical_occurrence, hist_detail = _historical_occurrence(past_predictions, base_param)
        equipment_association, equip_evidence = _equipment_association(
            db, lot, candidate, base_param, feature_snapshot, fab, equip_stats
        )
        wafer_pattern_signal, pattern_evidence = _wafer_pattern_signal(dominant_pattern, candidate, pattern_table)
        temporal_evidence_score, temporal_evidence = _temporal_shape(base_param, candidate, feature_snapshot)

        signals = {
            "shap_attribution": shap_score,
            "parameter_deviation": parameter_deviation,
            "historical_occurrence": historical_occurrence,
            "equipment_association": equipment_association,
            "wafer_pattern": wafer_pattern_signal,
            "temporal_evidence": temporal_evidence_score,
        }
        score = sum(ROOTCAUSE_WEIGHTS[k] * v for k, v in signals.items())

        evidence = _core_evidence_items(
            candidate, base_param, signals, feature_snapshot, hist_detail, equip_evidence, temporal_evidence,
            prediction.get("model_version", ""),
        )
        if pattern_evidence is not None:
            evidence.append(pattern_evidence)
        # Stable ids so recommendations can ground themselves in specific evidence (P3-6).
        slug = _slug(candidate)
        for idx, item in enumerate(evidence):
            item["evidence_id"] = f"rc:{lot_id}:{slug}:{idx}"

        candidates.append(
            {
                "candidate": candidate,
                "score": round(score, 4),
                "confidence": _confidence_band(score),
                "signal_breakdown": {k: round(v, 4) for k, v in signals.items()},
                "evidence": evidence,
            }
        )

    # A ranked cause we cannot back with a single evidence item is noise, and P3-6
    # forbids orphan suggestions: drop candidates whose signals are all below the
    # evidence thresholds (their score is then ~0 by construction — signal and
    # evidence cut-offs match 1:1). What remains is only evidence-backed candidates.
    supported = [c for c in candidates if c["evidence"]]
    supported.sort(key=lambda c: c["score"], reverse=True)
    top_candidates = supported[:top_n]

    result = {
        "lot_id": lot_id,
        "model_version": MODEL_NAME,
        "candidates": top_candidates,
        "dominant_wafer_pattern": dominant_pattern,
        "pattern_table_version": (pattern_table or {}).get("table_version"),
        "timestamp": utc_now(),
    }

    RootCauseRepository(db).upsert_for_lot(lot_id, result)
    return result
