"""
Bootstrap ensemble for what-if uncertainty (Phase 6, brief P5).

B XGBoost regressors, each trained on a bootstrap resample of the TRAIN split
with the production regressor's tuned hyper-parameters. For one input row the
members disagree; that spread is the model (epistemic) uncertainty reported as
a P10-P90 range. A second, wider band is a split-conformal 80% prediction
interval: the ensemble median plus the 10th/90th percentiles of its residuals
on the VALIDATION split. Its empirical coverage on the TEST split is measured
at training time and stored in the artifact — a range whose coverage was never
checked would be a decoration, not uncertainty.

Leakage: train/val/test are the same chronological split the production model
uses (app.ml.preprocessing.chronological_split); the test split is only scored.
"""
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from xgboost import XGBRegressor

from app.ml.preprocessing import chronological_split, to_model_matrix

ENSEMBLE_NAME = "whatif_ensemble"
ENSEMBLE_VERSION = "1.0.0"
DEFAULT_MEMBERS = 20
INTERVAL_QUANTILES = (0.10, 0.90)  # an 80% band, for both the spread and the conformal interval
DEFAULT_PARAMS = {
    "n_estimators": 300,
    "max_depth": 5,
    "learning_rate": 0.05,
    "subsample": 0.9,
    "colsample_bytree": 0.9,
}


@dataclass
class EnsembleBundle:
    members: list
    feature_columns: list
    residual_quantiles: tuple
    feature_ranges: dict
    metrics: dict
    version: str = ENSEMBLE_VERSION


def _artifact_path(model_dir: Path) -> Path:
    return Path(model_dir) / f"{ENSEMBLE_NAME}_v{ENSEMBLE_VERSION}.joblib"


def train_ensemble(
    features_df: pd.DataFrame,
    feature_columns: list,
    params: dict | None = None,
    n_members: int = DEFAULT_MEMBERS,
    seed: int = 42,
) -> EnsembleBundle:
    """Fit the bootstrap members and measure interval coverage on the test split."""
    train_df, val_df, test_df = chronological_split(features_df.sort_values("process_start").reset_index(drop=True))
    X_train, _ = to_model_matrix(train_df, known_dummy_columns=feature_columns)
    X_val, _ = to_model_matrix(val_df, known_dummy_columns=feature_columns)
    X_test, _ = to_model_matrix(test_df, known_dummy_columns=feature_columns)
    y_train, y_val, y_test = (d["yield"].to_numpy(dtype=float) for d in (train_df, val_df, test_df))

    rng = np.random.default_rng(seed)
    members = []
    for i in range(n_members):
        idx = rng.integers(0, len(X_train), size=len(X_train))
        model = XGBRegressor(**{**DEFAULT_PARAMS, **(params or {})}, random_state=seed + i, n_jobs=-1)
        model.fit(X_train.iloc[idx], y_train[idx])
        members.append(model)

    def _median(X):
        return np.median(np.vstack([m.predict(X) for m in members]), axis=0)

    val_residuals = y_val - _median(X_val)
    q_lo, q_hi = (float(np.quantile(val_residuals, q)) for q in INTERVAL_QUANTILES)

    test_pred_matrix = np.vstack([m.predict(X_test) for m in members])
    test_median = np.median(test_pred_matrix, axis=0)
    lo, hi = test_median + q_lo, test_median + q_hi
    spread = np.quantile(test_pred_matrix, INTERVAL_QUANTILES[1], axis=0) - np.quantile(
        test_pred_matrix, INTERVAL_QUANTILES[0], axis=0
    )
    metrics = {
        "n_members": n_members,
        "n_train": int(len(X_train)),
        "n_val": int(len(X_val)),
        "n_test": int(len(X_test)),
        "test_mae_of_median": round(float(np.mean(np.abs(y_test - test_median))), 4),
        "conformal_interval": {
            "nominal_coverage": round(INTERVAL_QUANTILES[1] - INTERVAL_QUANTILES[0], 2),
            "test_coverage": round(float(np.mean((y_test >= lo) & (y_test <= hi))), 4),
            "mean_width": round(float(np.mean(hi - lo)), 4),
            "residual_quantiles_from": "validation split",
        },
        "member_spread_p10_p90": {
            "test_mean_width": round(float(np.mean(spread)), 4),
            "test_median_width": round(float(np.median(spread)), 4),
            "note": "model (epistemic) uncertainty only — narrower than the outcome interval by design",
        },
        "split": "chronological 70/15/15 (same as the production model)",
    }
    numeric = X_train.select_dtypes("number")
    feature_ranges = {col: [float(numeric[col].min()), float(numeric[col].max())] for col in numeric.columns}
    return EnsembleBundle(
        members=members,
        feature_columns=list(feature_columns),
        residual_quantiles=(q_lo, q_hi),
        feature_ranges=feature_ranges,
        metrics=metrics,
    )


def save_ensemble(bundle: EnsembleBundle, model_dir: Path, dataset: str = "") -> Path:
    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    path = _artifact_path(model_dir)
    joblib.dump(bundle, path)
    metadata = {
        "model_name": ENSEMBLE_NAME,
        "version": bundle.version,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "dataset": dataset,
        "metrics": bundle.metrics,
        "artifact_file": path.name,
    }
    (model_dir / f"{ENSEMBLE_NAME}_v{bundle.version}.metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return path


_CACHE: dict = {}


def load_ensemble(model_dir: Path) -> EnsembleBundle | None:
    """Cached by (path, mtime) so a retrain is picked up without a restart."""
    path = _artifact_path(model_dir)
    if not path.exists():
        return None
    key = (str(path), path.stat().st_mtime)
    if key not in _CACHE:
        _CACHE.clear()
        _CACHE[key] = joblib.load(path)
    return _CACHE[key]


def member_predictions(bundle: EnsembleBundle, X: pd.DataFrame) -> np.ndarray:
    """Shape (n_members, n_rows)."""
    return np.vstack([m.predict(X) for m in bundle.members])
