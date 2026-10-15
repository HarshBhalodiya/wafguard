"""
Optuna hyperparameter search for the XGBoost production models (Phase 2A).

Discipline (docs/DECISIONS.md, D-017):
  - FIXED BUDGET: every search runs exactly `n_trials` trials on the
    chronological *validation* split. The test split is never touched
    during search.
  - Seeded sampler (TPESampler with RANDOM_STATE) so a re-run reproduces
    the same search; the study itself is in-memory (no shared storage).
  - Only a small, physically meaningful search space — no fishing trip.
"""
import numpy as np
import optuna
from sklearn.metrics import f1_score, mean_squared_error
from xgboost import XGBClassifier, XGBRegressor

from app.ml.yield_model import RANDOM_STATE

optuna.logging.set_verbosity(optuna.logging.WARNING)


def _suggest_common(trial: optuna.Trial) -> dict:
    return {
        "n_estimators": trial.suggest_int("n_estimators", 150, 600, step=50),
        "max_depth": trial.suggest_int("max_depth", 3, 8),
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
        "subsample": trial.suggest_float("subsample", 0.6, 1.0),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.6, 1.0),
        "min_child_weight": trial.suggest_float("min_child_weight", 1.0, 20.0, log=True),
        "reg_lambda": trial.suggest_float("reg_lambda", 1e-2, 10.0, log=True),
    }


def _scale_pos_weight(y: np.ndarray) -> float:
    n_pos = int(np.sum(y))
    n_neg = len(y) - n_pos
    return (n_neg / n_pos) if n_pos > 0 else 1.0


def optimize_xgb_classifier(
    X_train,
    y_train: np.ndarray,
    X_val,
    y_val: np.ndarray,
    n_trials: int = 40,
) -> tuple[dict, dict]:
    """Returns (best_params, study_summary). Maximizes F1 on the validation split."""
    if n_trials <= 0:
        return {}, {"n_trials": 0, "best_value": None}

    def objective(trial: optuna.Trial) -> float:
        params = _suggest_common(trial)
        model = XGBClassifier(
            **params,
            scale_pos_weight=_scale_pos_weight(y_train),
            eval_metric="logloss",
            random_state=RANDOM_STATE,
            n_jobs=-1,
        )
        model.fit(X_train, y_train)
        preds = (model.predict_proba(X_val)[:, 1] >= 0.5).astype(int)
        return f1_score(y_val, preds, zero_division=0)

    sampler = optuna.samplers.TPESampler(seed=RANDOM_STATE)
    study = optuna.create_study(direction="maximize", sampler=sampler)
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    summary = {
        "n_trials": n_trials,
        "best_value": float(study.best_value),
        "best_params": dict(study.best_params),
        "sampler": "TPESampler(seed=42)",
    }
    return dict(study.best_params), summary


def optimize_xgb_regressor(
    X_train,
    y_train: np.ndarray,
    X_val,
    y_val: np.ndarray,
    n_trials: int = 40,
) -> tuple[dict, dict]:
    """Returns (best_params, study_summary). Minimizes validation RMSE."""
    if n_trials <= 0:
        return {}, {"n_trials": 0, "best_value": None}

    def objective(trial: optuna.Trial) -> float:
        params = _suggest_common(trial)
        model = XGBRegressor(**params, random_state=RANDOM_STATE, n_jobs=-1)
        model.fit(X_train, y_train)
        preds = model.predict(X_val)
        return float(np.sqrt(mean_squared_error(y_val, preds)))

    sampler = optuna.samplers.TPESampler(seed=RANDOM_STATE)
    study = optuna.create_study(direction="minimize", sampler=sampler)
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    summary = {
        "n_trials": n_trials,
        "best_value": float(study.best_value),
        "best_params": dict(study.best_params),
        "sampler": "TPESampler(seed=42)",
    }
    return dict(study.best_params), summary
