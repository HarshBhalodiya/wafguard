"""
Phase 2A tabular model comparison (spec section 9, 37; DECISIONS D-016..D-018).

Trains, on the SAME chronological train/val/test split of the v2 synthetic
dataset:

  Regression (target: lot yield %)
    mean predictor | ridge | RandomForest | XGBoost (default) | XGBoost (Optuna-tuned)

  Classification (target: is_low_yield)
    base-rate predictor | logistic regression | RandomForest | XGBoost (default)
    | XGBoost (Optuna-tuned + isotonic-calibrated)

and reports every model on the untouched test split, plus:
  - XGBoost gain-based feature importance and mean |SHAP| (top 15 each)
  - reliability curve + ECE before/after calibration (reports/calibration_reliability.png)
  - confusion matrix image for the production classifier

Output: reports/tabular_comparison.md — every number in it is measured on
the held-out test split, nothing is assumed.

Usage:
    cd backend
    .venv/Scripts/python.exe scripts/compare_tabular_models.py
"""
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from app.config import get_settings  # noqa: E402
from app.ml import shap_service  # noqa: E402
from app.ml.baselines import fit_classifier_baselines, fit_regressor_baselines  # noqa: E402
from app.ml.calibration import (  # noqa: E402
    calibrate_classifier,
    expected_calibration_error,
    find_best_threshold,
    plot_reliability,
    reliability_curve_data,
)
from app.ml.cv import check_split_leakage_free  # noqa: E402
from app.ml.hpo import optimize_xgb_classifier, optimize_xgb_regressor  # noqa: E402
from app.ml.preprocessing import build_lot_features_bulk, chronological_split, to_model_matrix  # noqa: E402
from app.ml.yield_model import (  # noqa: E402
    evaluate_classifier,
    evaluate_regressor,
    train_classifier,
    train_regressor,
)
from app.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)
N_HPO_TRIALS = 40
REPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "reports"


def _fmt_reg(m: dict) -> str:
    return f"{m['mae']:.2f} | {m['rmse']:.2f} | {m['r2']:.3f}"


def _fmt_clf(m: dict) -> str:
    roc = f"{m['roc_auc']:.3f}" if m.get("roc_auc") is not None else "n/a"
    return f"{m['precision']:.3f} | {m['recall']:.3f} | {m['f1']:.3f} | {roc}"


def main() -> None:
    import train_yield_model as tym

    settings = get_settings()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    lots_df, process_df, sensor_df, maintenance_df = tym.load_synthetic_tables(settings)

    features_df = build_lot_features_bulk(lots_df, process_df, sensor_df, maintenance_df)
    leakage = check_split_leakage_free(features_df)
    print(f"[Split] leakage check: {leakage}")

    train_df, val_df, test_df = chronological_split(features_df)
    X_train, dummy_columns = to_model_matrix(train_df)
    X_val, _ = to_model_matrix(val_df, known_dummy_columns=dummy_columns)
    X_test, _ = to_model_matrix(test_df, known_dummy_columns=dummy_columns)

    y_reg_train, y_reg_val, y_reg_test = (
        train_df["yield"].to_numpy(),
        val_df["yield"].to_numpy(),
        test_df["yield"].to_numpy(),
    )
    y_clf_train, y_clf_val, y_clf_test = (
        train_df["is_low_yield"].to_numpy(),
        val_df["is_low_yield"].to_numpy(),
        test_df["is_low_yield"].to_numpy(),
    )
    print(f"[Data] train={len(X_train)} val={len(X_val)} test={len(X_test)} | features={len(dummy_columns)}")

    # ---------------- Regression ----------------
    print("[Regression] training baselines + RF + default XGB...")
    reg_baselines = fit_regressor_baselines(X_train, y_reg_train)
    reg_rf = train_regressor(X_train, y_reg_train)["baseline"]
    reg_xgb_default = train_regressor(X_train, y_reg_train)["production"]
    print(f"[Regression] Optuna HPO ({N_HPO_TRIALS} trials)...")
    reg_params, reg_hpo = optimize_xgb_regressor(X_train, y_reg_train, X_val, y_reg_val, n_trials=N_HPO_TRIALS)
    reg_xgb_tuned = train_regressor(X_train, y_reg_train)["production"]
    if reg_params:
        from xgboost import XGBRegressor

        reg_xgb_tuned = XGBRegressor(**reg_params, random_state=42, n_jobs=-1).fit(X_train, y_reg_train)

    reg_rows = {
        "mean_predictor": evaluate_regressor(reg_baselines["mean_predictor"], X_test, y_reg_test),
        "ridge": evaluate_regressor(reg_baselines["ridge"], X_test, y_reg_test),
        "random_forest": evaluate_regressor(reg_rf, X_test, y_reg_test),
        "xgboost_default": evaluate_regressor(reg_xgb_default, X_test, y_reg_test),
        "xgboost_tuned": evaluate_regressor(reg_xgb_tuned, X_test, y_reg_test),
    }

    # ---------------- Classification ----------------
    print("[Classification] training baselines + RF + default XGB...")
    clf_baselines = fit_classifier_baselines(X_train, y_clf_train)
    clf_rf = train_classifier(X_train, y_clf_train)["baseline"]
    clf_xgb_uncal = train_classifier(X_train, y_clf_train)["production"]
    print(f"[Classification] Optuna HPO ({N_HPO_TRIALS} trials)...")
    clf_params, clf_hpo = optimize_xgb_classifier(X_train, y_clf_train, X_val, y_clf_val, n_trials=N_HPO_TRIALS)
    if clf_params:
        from xgboost import XGBClassifier

        spw = (len(y_clf_train) - y_clf_train.sum()) / max(1, int(y_clf_train.sum()))
        clf_xgb_uncal = XGBClassifier(
            **clf_params, scale_pos_weight=spw, eval_metric="logloss", random_state=42, n_jobs=-1
        ).fit(X_train, y_clf_train)

    ece_val_before = expected_calibration_error(y_clf_val, clf_xgb_uncal.predict_proba(X_val)[:, 1])
    clf_xgb_cal = calibrate_classifier(clf_xgb_uncal, X_val, y_clf_val, method="isotonic")
    ece_val_after = expected_calibration_error(y_clf_val, clf_xgb_cal.predict_proba(X_val)[:, 1])
    # Calibration rescales probabilities — re-pick the F1-optimal threshold on validation.
    decision_threshold = find_best_threshold(y_clf_val, clf_xgb_cal.predict_proba(X_val)[:, 1])
    print(f"[Classification] calibrated decision threshold (from val): {decision_threshold:.3f}")

    prob_test_uncal = clf_xgb_uncal.predict_proba(X_test)[:, 1]
    prob_test_cal = clf_xgb_cal.predict_proba(X_test)[:, 1]
    ece_test_before = expected_calibration_error(y_clf_test, prob_test_uncal)
    ece_test_after = expected_calibration_error(y_clf_test, prob_test_cal)

    clf_rows = {
        "base_rate_predictor": evaluate_classifier(clf_baselines["base_rate_predictor"], X_test, y_clf_test),
        "logreg": evaluate_classifier(clf_baselines["logreg"], X_test, y_clf_test),
        "random_forest": evaluate_classifier(clf_rf, X_test, y_clf_test),
        "xgboost_default": evaluate_classifier(clf_xgb_uncal, X_test, y_clf_test),
        "xgboost_tuned_calibrated": evaluate_classifier(clf_xgb_cal, X_test, y_clf_test, threshold=decision_threshold),
    }

    # ---------------- Explainability ----------------
    print("[Explainability] gain importance + SHAP...")
    gain_importance = dict(
        sorted(clf_xgb_uncal.get_booster().get_score(importance_type="gain").items(), key=lambda kv: -kv[1])
    )
    shap_sample = X_test.sample(min(300, len(X_test)), random_state=7)
    shap_importance = shap_service.global_feature_importance(clf_xgb_uncal, shap_sample, top_n=15)

    # ---------------- Plots ----------------
    curves = {
        "before calibration (test)": reliability_curve_data(y_clf_test, prob_test_uncal),
        "after calibration (test)": reliability_curve_data(y_clf_test, prob_test_cal),
    }
    plot_reliability(curves, REPORTS_DIR / "calibration_reliability.png")

    cm = confusion_matrix(y_clf_test, (prob_test_cal >= decision_threshold).astype(int))
    fig, ax = plt.subplots(figsize=(4, 4))
    ax.imshow(cm, cmap="Blues")
    for (i, j), v in np.ndenumerate(cm):
        ax.text(j, i, str(v), ha="center", va="center")
    ax.set_xticks([0, 1], labels=["pred OK", "pred LOW"])
    ax.set_yticks([0, 1], labels=["actual OK", "actual LOW"])
    ax.set_title(f"Production classifier confusion matrix (test, thr={decision_threshold:.2f})")
    fig.tight_layout()
    fig.savefig(REPORTS_DIR / "confusion_matrix_low_yield.png", dpi=150)
    plt.close(fig)

    # ---------------- Report ----------------
    lines = [
        "# Tabular model comparison (Phase 2A)",
        "",
        "**Dataset:** synthetic v2 (`data/synthetic/`, DEMO / SIMULATED DATA), chronological split, "
        f"train={len(X_train)} / val={len(X_val)} / test={len(X_test)}.",
        f"**Leakage check:** train end `{leakage['train_end']}` < val start `{leakage['val_start']}` < test start `{leakage['test_start']}` — passed.",
        f"**HPO:** fixed budget, {N_HPO_TRIALS} Optuna trials per model, seeded TPESampler, search on validation split only.",
        "",
        "## Regression (lot yield %) — test split",
        "",
        "| Model | MAE | RMSE | R² |",
        "|---|---|---|---|",
    ]
    for name, m in reg_rows.items():
        lines.append(f"| {name} | {_fmt_reg(m)} |")
    lines += [
        "",
        f"Tuned XGB best val RMSE: {reg_hpo.get('best_value'):.3f}; params: `{json.dumps(reg_hpo.get('best_params', {}))}`.",
        "",
        "## Classification (is_low_yield) — test split",
        "",
        "| Model | Precision | Recall | F1 | ROC-AUC |",
        "|---|---|---|---|---|",
    ]
    for name, m in clf_rows.items():
        lines.append(f"| {name} | {_fmt_clf(m)} |")
    lines += [
        "",
        f"Tuned XGB best val F1: {clf_hpo.get('best_value'):.3f}; params: `{json.dumps(clf_hpo.get('best_params', {}))}`.",
        "",
        "## Probability calibration (isotonic, fit on validation)",
        "",
        f"- ECE (val): {ece_val_before:.4f} → {ece_val_after:.4f}",
        f"- ECE (test): {ece_test_before:.4f} → {ece_test_after:.4f}",
        f"- Decision threshold re-picked on validation after calibration: **{decision_threshold:.3f}** "
        "(calibration rescales scores, so 0.5 is no longer the F1-optimal operating point).",
        "",
        "![Reliability diagram](calibration_reliability.png)",
        "",
        "![Confusion matrix](confusion_matrix_low_yield.png)",
        "",
        "## Feature importance — production classifier (XGBoost gain, top 15)",
        "",
        "| Feature | Gain |",
        "|---|---|",
    ]
    for feat, gain in list(gain_importance.items())[:15]:
        lines.append(f"| {feat} | {gain:.1f} |")
    lines += [
        "",
        "## Feature importance — mean |SHAP| over a test sample (top 15)",
        "",
        "| Feature | mean \\|SHAP\\| |",
        "|---|---|",
    ]
    for item in shap_importance:
        lines.append(f"| {item['feature']} | {item['mean_abs_shap']:.4f} |")
    lines += [
        "",
        "## Honest reading",
        "",
        "- All numbers are measured on the chronological test split; synthetic data only, "
        "so they describe the simulator's fault physics, not a real fab.",
        "- If a tuned model does not beat the simple baselines on a metric, the table shows it — "
        "the point of this report is comparison, not a story.",
        "- Calibration is evaluated on both val (fit) and test (honest); if test ECE does not "
        "improve, the calibrator is overfit to validation and should not be trusted.",
    ]
    report_path = REPORTS_DIR / "tabular_comparison.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[Report] wrote {report_path}")
    print(f"[Report] wrote {REPORTS_DIR / 'calibration_reliability.png'} and confusion_matrix_low_yield.png")

    print("\n=== Regression (test) ===")
    for name, m in reg_rows.items():
        print(f"  {name:26s} MAE={m['mae']:.2f} RMSE={m['rmse']:.2f} R2={m['r2']:.3f}")
    print("\n=== Classification (test) ===")
    for name, m in clf_rows.items():
        print(f"  {name:26s} P={m['precision']:.3f} R={m['recall']:.3f} F1={m['f1']:.3f}")


if __name__ == "__main__":
    main()
