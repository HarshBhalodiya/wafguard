"""
Generic train/evaluate/persist helpers shared by both tabular models:

  Model A — SECOM failure classifier (real UCI data)
  Model B — synthetic lot yield regressor + low-yield classifier

Kept generic here; `scripts/train_yield_model.py` supplies the actual
data and decides which of these to call for which model. RandomForest is
always the baseline, XGBoost the production candidate (spec section 9) —
both get evaluated and reported, never just the one that looks better.
"""
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
    roc_auc_score,
)
from xgboost import XGBClassifier, XGBRegressor

RANDOM_STATE = 42


@dataclass
class ModelBundle:
    model_name: str
    version: str
    task: str  # "classification" | "regression"
    baseline: Any
    production: Any
    feature_columns: list
    metrics: dict = field(default_factory=dict)
    dataset: str = ""
    trained_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------
def train_classifier(X_train: pd.DataFrame, y_train: np.ndarray) -> dict:
    n_pos = int(y_train.sum())
    n_neg = len(y_train) - n_pos
    scale_pos_weight = (n_neg / n_pos) if n_pos > 0 else 1.0

    baseline = RandomForestClassifier(
        n_estimators=300, max_depth=8, class_weight="balanced", random_state=RANDOM_STATE, n_jobs=-1
    )
    baseline.fit(X_train, y_train)

    production = XGBClassifier(
        n_estimators=300,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.9,
        colsample_bytree=0.9,
        scale_pos_weight=scale_pos_weight,
        eval_metric="logloss",
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )
    production.fit(X_train, y_train)
    return {"baseline": baseline, "production": production}


def train_regressor(X_train: pd.DataFrame, y_train: np.ndarray) -> dict:
    baseline = RandomForestRegressor(n_estimators=300, max_depth=10, random_state=RANDOM_STATE, n_jobs=-1)
    baseline.fit(X_train, y_train)

    production = XGBRegressor(
        n_estimators=400,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.9,
        colsample_bytree=0.9,
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )
    production.fit(X_train, y_train)
    return {"baseline": baseline, "production": production}


# ---------------------------------------------------------------------------
# Evaluation — every number here is measured on held-out data, never assumed.
# ---------------------------------------------------------------------------
def evaluate_classifier(model, X_test: pd.DataFrame, y_test: np.ndarray, threshold: float = 0.5) -> dict:
    proba = model.predict_proba(X_test)[:, 1]
    preds = (proba >= threshold).astype(int)

    n_pos, n_neg = int(y_test.sum()), int(len(y_test) - y_test.sum())
    metrics = {
        "precision": float(precision_score(y_test, preds, zero_division=0)),
        "recall": float(recall_score(y_test, preds, zero_division=0)),
        "f1": float(f1_score(y_test, preds, zero_division=0)),
        "decision_threshold": float(threshold),
        "class_balance": {"positive": n_pos, "negative": n_neg, "positive_rate": round(n_pos / max(1, len(y_test)), 4)},
        "confusion_matrix": confusion_matrix(y_test, preds).tolist(),
        "n_test_samples": int(len(y_test)),
    }
    # ROC-AUC / PR-AUC are undefined with only one class present in y_test.
    if n_pos > 0 and n_neg > 0:
        metrics["roc_auc"] = float(roc_auc_score(y_test, proba))
        metrics["pr_auc"] = float(average_precision_score(y_test, proba))
    else:
        metrics["roc_auc"] = None
        metrics["pr_auc"] = None
        metrics["note"] = "ROC-AUC/PR-AUC undefined: test split contains only one class."
    return metrics


def evaluate_regressor(model, X_test: pd.DataFrame, y_test: np.ndarray) -> dict:
    preds = model.predict(X_test)
    return {
        "mae": float(mean_absolute_error(y_test, preds)),
        "rmse": float(np.sqrt(mean_squared_error(y_test, preds))),
        "r2": float(r2_score(y_test, preds)) if len(y_test) > 1 else None,
        "n_test_samples": int(len(y_test)),
    }


# ---------------------------------------------------------------------------
# Persistence (spec section 36)
# ---------------------------------------------------------------------------
def save_bundle(bundle: ModelBundle, model_dir: Path) -> Path:
    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = model_dir / f"{bundle.model_name}_v{bundle.version}.joblib"
    joblib.dump(
        {
            "baseline": bundle.baseline,
            "production": bundle.production,
            "feature_columns": bundle.feature_columns,
        },
        artifact_path,
    )

    metadata = {
        "model_name": bundle.model_name,
        "version": bundle.version,
        "task": bundle.task,
        "dataset": bundle.dataset,
        "trained_at": bundle.trained_at,
        "features": bundle.feature_columns,
        "metrics": bundle.metrics,
        "artifact_file": artifact_path.name,
    }
    metadata_path = model_dir / f"{bundle.model_name}_v{bundle.version}.metadata.json"
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    latest_pointer = model_dir / f"{bundle.model_name}.latest.json"
    with open(latest_pointer, "w", encoding="utf-8") as f:
        json.dump({"version": bundle.version, "artifact_file": artifact_path.name}, f, indent=2)

    return artifact_path


def load_latest_bundle(model_name: str, model_dir: Path) -> Optional[dict]:
    model_dir = Path(model_dir)
    pointer_path = model_dir / f"{model_name}.latest.json"
    if not pointer_path.exists():
        return None
    with open(pointer_path, encoding="utf-8") as f:
        pointer = json.load(f)
    artifact_path = model_dir / pointer["artifact_file"]
    if not artifact_path.exists():
        return None
    loaded = joblib.load(artifact_path)

    metadata_path = model_dir / f"{model_name}_v{pointer['version']}.metadata.json"
    metadata = {}
    if metadata_path.exists():
        with open(metadata_path, encoding="utf-8") as f:
            metadata = json.load(f)

    return {**loaded, "version": pointer["version"], "metadata": metadata}
