"""
Orchestrates yield/risk inference for a lot: load trained model bundles,
build the model input from a feature snapshot, predict, blend in the risk
score, and (optionally) explain the prediction with SHAP.

Model B ("lot_yield") is actually two bundles sharing one feature layout —
a regressor for predicted_yield and a classifier for the low-yield
probability that feeds the risk engine — because a single XGBoost model
can't natively do both regression and calibrated-probability
classification well at once, and spec section 10 wants both reported.
"""
from pathlib import Path
from typing import Optional

import pandas as pd

from app.config import get_settings
from app.ml import shap_service
from app.ml.preprocessing import single_row_to_model_matrix
from app.ml.yield_model import load_latest_bundle
from app.services.risk_service import compute_historical_defect_risk, compute_risk
from app.utils.exceptions import ModelUnavailableError
from app.utils.logging import get_logger

logger = get_logger(__name__)

MODEL_B_REGRESSOR = "lot_yield_regressor"
MODEL_B_CLASSIFIER = "lot_yield_classifier"
MODEL_A_CLASSIFIER = "secom_failure_classifier"

_BUNDLE_CACHE: dict = {}


def _model_dir() -> Path:
    settings = get_settings()
    return settings.resolve_path(settings.model_dir)


def get_bundle(model_name: str, force_reload: bool = False) -> dict:
    if force_reload:
        _BUNDLE_CACHE.pop(model_name, None)
    if model_name not in _BUNDLE_CACHE:
        bundle = load_latest_bundle(model_name, _model_dir())
        if bundle is None:
            raise ModelUnavailableError(
                f"Model '{model_name}' has not been trained yet. Run "
                "'python scripts/train_yield_model.py' from backend/ first."
            )
        _BUNDLE_CACHE[model_name] = bundle
    return _BUNDLE_CACHE[model_name]


def clear_bundle_cache() -> None:
    _BUNDLE_CACHE.clear()


def predict_lot(
    feature_snapshot: dict,
    equipment_doc: Optional[dict],
    recent_equipment_yields: list,
    use_production: bool = True,
) -> dict:
    """
    Returns predicted_yield, the classifier's raw model_probability, the
    blended risk_probability/risk_level (spec section 12), and enough
    metadata to persist a `predictions` document.
    """
    key = "production" if use_production else "baseline"

    regressor_bundle = get_bundle(MODEL_B_REGRESSOR)
    classifier_bundle = get_bundle(MODEL_B_CLASSIFIER)
    feature_columns = regressor_bundle["feature_columns"]

    X = single_row_to_model_matrix(feature_snapshot, feature_columns)
    predicted_yield = float(regressor_bundle[key].predict(X)[0])
    model_probability = float(classifier_bundle[key].predict_proba(X)[0][1])

    historical_defect_risk = compute_historical_defect_risk(recent_equipment_yields)
    risk = compute_risk(model_probability, feature_snapshot, equipment_doc, historical_defect_risk)

    return {
        "predicted_yield": round(predicted_yield, 2),
        "model_probability": round(model_probability, 4),
        "risk_probability": risk["risk_probability"],
        "risk_level": risk["risk_level"],
        "risk_breakdown": risk["breakdown"],
        "model_name": MODEL_B_CLASSIFIER,
        "model_version": classifier_bundle["version"],
        "regressor_version": regressor_bundle["version"],
        "feature_columns": feature_columns,
    }


def explain_lot(feature_snapshot: dict, top_n: int = 8) -> list[shap_service.FeatureContribution]:
    """
    SHAP explanation of the classifier's low-yield probability for this lot (spec section 13).

    Since Phase 2A the production classifier is a CalibratedClassifierCV
    wrapping the XGBoost model. SHAP's TreeExplainer cannot introspect the
    calibration wrapper, so attribution runs on the inner (uncalibrated)
    XGBoost model — the calibrated wrapper is a monotone remap of its
    scores, so feature attributions are unchanged; only the absolute
    output scale differs, which is exactly what "model attribution, not
    physics" labeling (shap_service docstring) already covers.
    """
    classifier_bundle = get_bundle(MODEL_B_CLASSIFIER)
    X = single_row_to_model_matrix(feature_snapshot, classifier_bundle["feature_columns"])
    model = classifier_bundle["production"]
    if hasattr(model, "calibrated_classifiers_"):  # CalibratedClassifierCV
        model = model.calibrated_classifiers_[0].estimator
    if hasattr(model, "estimator"):  # sklearn FrozenEstimator wrapper
        model = model.estimator
    return shap_service.explain_instance(model, X, top_n=top_n)


def get_recent_equipment_yields(db, equipment_id: str, exclude_lot_id: Optional[str] = None, limit: int = 10) -> list:
    """Most recent *actual* yields for other lots on this equipment — the raw
    input to risk_service.compute_historical_defect_risk. Shared here so the
    live predict endpoint, the what-if simulator, and any future caller all
    define "recent history" the same way instead of drifting apart."""
    from app.database.repositories.lot_repository import LotRepository

    lot_repo = LotRepository(db)
    fetch_limit = limit + 1 if exclude_lot_id else limit
    recent_lots = lot_repo.find_many({"equipment": equipment_id}, sort=[("process_start", -1)], limit=fetch_limit)
    yields = [l["yield"] for l in recent_lots if l.get("lot_id") != exclude_lot_id and l.get("yield") is not None]
    return yields[:limit]


def predict_secom(feature_row: dict) -> dict:
    """Model A: SECOM failure probability for a raw feature_0..feature_n row."""
    bundle = get_bundle(MODEL_A_CLASSIFIER)
    feature_columns = bundle["feature_columns"]
    X = pd.DataFrame([{col: feature_row.get(col, 0.0) for col in feature_columns}], columns=feature_columns)
    proba = float(bundle["production"].predict_proba(X)[0][1])
    return {"fail_probability": round(proba, 4), "model_version": bundle["version"]}
