"""
Multi-signal root-cause engine (spec section 14).

For a lot, ranks candidate causes (RF Power, Temperature, ...) by combining
six signals — SHAP attribution, parameter deviation, historical
occurrence, equipment association, wafer-pattern evidence, and temporal
(within-run drift) evidence — using the weights in app.config.ROOTCAUSE_WEIGHTS.

Terminology discipline (spec section 45): this produces "root-cause
candidates" with a confidence band, never a "confirmed root cause". Every
evidence item is tagged FACT / MODEL_OUTPUT / INFERENCE so the UI can be
honest about what kind of claim it's showing.
"""
from collections import Counter
from typing import Optional

from app.config import PROCESS_PARAMETER_RANGES, ROOTCAUSE_CONFIDENCE_THRESHOLDS, ROOTCAUSE_WEIGHTS
from app.database.repositories.equipment_repository import EquipmentRepository
from app.database.repositories.lot_repository import LotRepository
from app.database.repositories.prediction_repository import PredictionRepository
from app.database.repositories.rootcause_repository import RootCauseRepository
from app.database.repositories.wafer_repository import WaferRepository
from app.services import risk_service, yield_service
from app.utils.exceptions import NotFoundError
from app.utils.time import utc_now

# candidate display name -> (base parameter for deviation lookups, underlying feature columns for SHAP grouping)
CANDIDATE_DEFINITIONS = {
    "RF Power": ("rf_power", ["rf_power_mean", "rf_power_max", "rf_power_std", "rf_power_drift"]),
    "Temperature": ("temperature", ["temperature_mean", "temperature_max", "temperature_std", "temperature_drift"]),
    "Pressure": ("pressure", ["pressure_mean", "pressure_max", "pressure_std", "pressure_drift"]),
    "Gas Flow": ("gas_flow", ["gas_flow_mean", "gas_flow_max", "gas_flow_std", "gas_flow_drift"]),
    "Etch Time": ("etch_time", ["etch_time"]),
    "Voltage": ("voltage", ["voltage"]),
    "Current": ("current", ["current"]),
    "Equipment Yield History": (None, ["previous_lot_yield", "previous_5_lot_yield"]),
    "Equipment Failure History": (None, ["equipment_failure_count"]),
    "Maintenance Overdue": (None, ["maintenance_days_since_last"]),
}

WAFER_PATTERN_CANDIDATE_BOOST = {
    "Edge-Ring": {"RF Power": 0.70, "Pressure": 0.30},
    "Edge-Loc": {"RF Power": 0.45, "Gas Flow": 0.30},
    "Donut": {"Pressure": 0.60, "Gas Flow": 0.40},
    "Center": {"Gas Flow": 0.55, "Temperature": 0.30},
    "Scratch": {"Etch Time": 0.30},
    "Near-full": {"RF Power": 0.45, "Temperature": 0.45, "Pressure": 0.35},
    "Loc": {"Temperature": 0.25},
    "Random": {},
    "None": {},
}

HIGH_DEVIATION_THRESHOLD = 0.8  # a past lot "counts" as having this cause if deviation exceeds this
MODEL_NAME = "rootcause_multi_signal_v1"


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


def _historical_occurrence(past_predictions: list[dict], base_param: Optional[str]) -> float:
    if base_param is None or not past_predictions:
        return 0.0
    concerning = [p for p in past_predictions if p.get("risk_level") in ("HIGH", "MEDIUM")]
    if not concerning:
        return 0.0
    hits = 0
    for p in concerning:
        snap = p.get("features_snapshot", {})
        value = _representative_value(snap, base_param)
        if value is not None and risk_service.raw_parameter_deviation(base_param, value) > HIGH_DEVIATION_THRESHOLD:
            hits += 1
    return hits / len(concerning)


def _equipment_association(candidate: str, base_param: Optional[str], equipment_doc: Optional[dict]) -> float:
    if not equipment_doc:
        return 0.0
    if base_param is None:
        # "Equipment Yield/Failure History" / "Maintenance Overdue" candidates ARE about equipment condition.
        return risk_service.compute_equipment_risk_score(equipment_doc)
    anomaly = (equipment_doc.get("anomaly_status") or "").lower()
    if anomaly and (base_param.replace("_", " ") in anomaly or candidate.lower() in anomaly):
        return 0.9
    return 0.4 if equipment_doc.get("health_score", 100) < 80 else 0.1


def _temporal_evidence(base_param: Optional[str], feature_snapshot: dict) -> float:
    if base_param is None or base_param not in PROCESS_PARAMETER_RANGES:
        return 0.0
    drift = feature_snapshot.get(f"{base_param}_drift")
    if drift is None:
        return 0.0
    lo, hi = PROCESS_PARAMETER_RANGES[base_param]
    half_width = (hi - lo) / 2
    return max(0.0, min(1.0, abs(drift) / (half_width * 0.6)))


def _confidence_band(score: float) -> str:
    if score >= ROOTCAUSE_CONFIDENCE_THRESHOLDS["high_min"]:
        return "HIGH"
    if score >= ROOTCAUSE_CONFIDENCE_THRESHOLDS["medium_min"]:
        return "MEDIUM"
    return "LOW"


def _evidence_items(
    candidate: str,
    base_param: Optional[str],
    signals: dict,
    feature_snapshot: dict,
    equipment_doc: Optional[dict],
    dominant_pattern: str,
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
    if signals["historical_occurrence"] > 0.05:
        items.append(
            {
                "signal": "historical_occurrence",
                "label": f"{candidate} was also abnormal in {signals['historical_occurrence'] * 100:.0f}% of this equipment's recent at-risk lots.",
                "value": round(signals["historical_occurrence"], 4),
                "kind": "FACT",
                "source": "predictions history",
                "timestamp": now,
            }
        )
    if signals["equipment_association"] > 0.05 and equipment_doc:
        label = (
            f"Equipment anomaly status: '{equipment_doc.get('anomaly_status')}'."
            if equipment_doc.get("anomaly_status")
            else f"Equipment health score {equipment_doc.get('health_score')}/100."
        )
        items.append(
            {
                "signal": "equipment_association",
                "label": label,
                "value": round(signals["equipment_association"], 4),
                "kind": "FACT",
                "source": "equipment",
                "timestamp": now,
            }
        )
    if signals["wafer_pattern"] > 0.05:
        items.append(
            {
                "signal": "wafer_pattern",
                "label": f"Wafer maps show a '{dominant_pattern}' pattern, a potential process/equipment association with {candidate.lower()}.",
                "value": round(signals["wafer_pattern"], 4),
                "kind": "INFERENCE",
                "source": "wafer pattern classification",
                "timestamp": now,
            }
        )
    if signals["temporal_evidence"] > 0.05:
        drift = feature_snapshot.get(f"{base_param}_drift") if base_param else None
        items.append(
            {
                "signal": "temporal_evidence",
                "label": f"{candidate} showed a drift of {drift:+.2f} across this run's readings." if drift is not None else f"{candidate} showed a within-run drift trend.",
                "value": round(signals["temporal_evidence"], 4),
                "kind": "FACT",
                "source": "sensor_data",
                "timestamp": now,
            }
        )
    return items


def compute_root_cause(db, lot_id: str, top_n: int = 5) -> dict:
    lot_repo = LotRepository(db)
    prediction_repo = PredictionRepository(db)
    equipment_repo = EquipmentRepository(db)
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
    equipment_doc = equipment_repo.get_by_equipment_id(lot["equipment"])
    wafers = wafer_repo.by_lot(lot_id)
    dominant_pattern = _dominant_wafer_pattern(wafers)
    past_predictions = prediction_repo.for_equipment(lot["equipment"], exclude_lot_id=lot_id)

    all_contributions = yield_service.explain_lot(feature_snapshot, top_n=len(feature_snapshot) + 20)
    shap_by_feature = {c.feature: c.shap_value for c in all_contributions}
    total_abs_shap = sum(abs(v) for v in shap_by_feature.values()) or 1.0

    candidates = []
    for candidate, (base_param, feature_cols) in CANDIDATE_DEFINITIONS.items():
        shap_abs_sum = sum(abs(shap_by_feature.get(col, 0.0)) for col in feature_cols)
        shap_score = shap_abs_sum / total_abs_shap

        value = _representative_value(feature_snapshot, base_param)
        parameter_deviation = min(1.0, risk_service.raw_parameter_deviation(base_param, value)) if base_param else 0.0
        historical_occurrence = _historical_occurrence(past_predictions, base_param)
        equipment_association = _equipment_association(candidate, base_param, equipment_doc)
        wafer_pattern_signal = WAFER_PATTERN_CANDIDATE_BOOST.get(dominant_pattern, {}).get(candidate, 0.0)
        temporal_evidence = _temporal_evidence(base_param, feature_snapshot)

        signals = {
            "shap_attribution": shap_score,
            "parameter_deviation": parameter_deviation,
            "historical_occurrence": historical_occurrence,
            "equipment_association": equipment_association,
            "wafer_pattern": wafer_pattern_signal,
            "temporal_evidence": temporal_evidence,
        }
        score = sum(ROOTCAUSE_WEIGHTS[k] * v for k, v in signals.items())

        candidates.append(
            {
                "candidate": candidate,
                "score": round(score, 4),
                "confidence": _confidence_band(score),
                "signal_breakdown": {k: round(v, 4) for k, v in signals.items()},
                "evidence": _evidence_items(
                    candidate, base_param, signals, feature_snapshot, equipment_doc, dominant_pattern, prediction.get("model_version", "")
                ),
            }
        )

    candidates.sort(key=lambda c: c["score"], reverse=True)
    top_candidates = candidates[:top_n]

    result = {
        "lot_id": lot_id,
        "model_version": MODEL_NAME,
        "candidates": top_candidates,
        "dominant_wafer_pattern": dominant_pattern,
        "timestamp": utc_now(),
    }

    RootCauseRepository(db).upsert_for_lot(lot_id, result)
    return result
