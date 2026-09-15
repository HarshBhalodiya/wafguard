"""
Deeper, standalone evaluation of already-trained model artifacts (spec
section 37 lists this as separate from training). Reloads the saved
artifacts and the feature snapshot written by train_yield_model.py,
rebuilds the exact same chronological split, and reports:

  - baseline vs. production metrics side by side (sanity-checks against
    what training recorded — should match)
  - a readable confusion matrix
  - global SHAP feature importance (mean |SHAP| over a test-set sample)

Writes backend/models/artifacts/evaluation_report.json, which the
Model Performance page can read directly if MongoDB/model_versions isn't
available yet.

Usage:
    cd backend
    .venv\\Scripts\\activate
    python scripts/evaluate_yield_model.py
"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.data.loaders.secom_loader import load_secom  # noqa: E402
from app.data.preprocessing.secom_preprocessor import build_secom_feature_matrix  # noqa: E402
from app.ml import shap_service  # noqa: E402
from app.ml.preprocessing import chronological_split, to_model_matrix  # noqa: E402
from app.ml.yield_model import evaluate_classifier, evaluate_regressor, load_latest_bundle  # noqa: E402
from app.utils.exceptions import DatasetUnavailableError  # noqa: E402


def _print_confusion(name: str, cm: list) -> None:
    print(f"  {name} confusion matrix [[TN, FP], [FN, TP]]: {cm}")


def evaluate_lot_yield(settings) -> dict:
    model_dir = settings.resolve_path(settings.model_dir)
    features_path = model_dir / "lot_features_snapshot.parquet"
    if not features_path.exists():
        print("[LotYield] no feature snapshot found — run train_yield_model.py first.")
        return {}

    features_df = pd.read_parquet(features_path)
    train_df, val_df, test_df = chronological_split(features_df)

    reg_bundle = load_latest_bundle("lot_yield_regressor", model_dir)
    clf_bundle = load_latest_bundle("lot_yield_classifier", model_dir)
    if reg_bundle is None or clf_bundle is None:
        print("[LotYield] model artifacts not found — run train_yield_model.py first.")
        return {}

    _, dummy_columns = to_model_matrix(train_df)
    X_test, _ = to_model_matrix(test_df, known_dummy_columns=dummy_columns)

    print("\n=== Lot Yield Regressor (re-evaluated) ===")
    reg_report = {
        "baseline": evaluate_regressor(reg_bundle["baseline"], X_test, test_df["yield"].to_numpy()),
        "production": evaluate_regressor(reg_bundle["production"], X_test, test_df["yield"].to_numpy()),
    }
    print(f"  baseline (RandomForest): {reg_report['baseline']}")
    print(f"  production (XGBoost):    {reg_report['production']}")

    print("\n=== Lot Yield Classifier (re-evaluated) ===")
    clf_report = {
        "baseline": evaluate_classifier(clf_bundle["baseline"], X_test, test_df["is_low_yield"].to_numpy()),
        "production": evaluate_classifier(clf_bundle["production"], X_test, test_df["is_low_yield"].to_numpy()),
    }
    print(f"  baseline (RandomForest): {clf_report['baseline']}")
    print(f"  production (XGBoost):    {clf_report['production']}")
    _print_confusion("production", clf_report["production"]["confusion_matrix"])

    sample = X_test.sample(min(60, len(X_test)), random_state=7) if len(X_test) > 0 else X_test
    importance = shap_service.global_feature_importance(clf_bundle["production"], sample, top_n=15) if len(sample) else []
    print("\n  Top global SHAP features (mean |SHAP| over a test sample):")
    for item in importance[:10]:
        print(f"    {item['feature']:30s} {item['mean_abs_shap']:.4f}")

    return {"regressor": reg_report, "classifier": clf_report, "global_feature_importance": importance}


def evaluate_secom(settings) -> dict:
    secom_dir = settings.resolve_path(settings.secom_data_path)
    model_dir = settings.resolve_path(settings.model_dir)
    try:
        raw = load_secom(secom_dir)
    except DatasetUnavailableError as exc:
        print(f"[SECOM] skipped: {exc}")
        return {}

    bundle = load_latest_bundle("secom_failure_classifier", model_dir)
    if bundle is None:
        print("[SECOM] model artifact not found — run train_yield_model.py first.")
        return {}

    X, y, timestamps, feature_columns = build_secom_feature_matrix(raw)
    X_df = pd.DataFrame(X, columns=feature_columns)
    order = timestamps.sort_values().index
    X_df, y = X_df.loc[order].reset_index(drop=True), y[order]
    n = len(X_df)
    val_end = int(n * 0.85)
    X_test, y_test = X_df.iloc[val_end:], y[val_end:]

    print("\n=== SECOM Failure Classifier (re-evaluated) ===")
    report = {
        "baseline": evaluate_classifier(bundle["baseline"], X_test, y_test),
        "production": evaluate_classifier(bundle["production"], X_test, y_test),
    }
    print(f"  baseline (RandomForest): {report['baseline']}")
    print(f"  production (XGBoost):    {report['production']}")
    _print_confusion("production", report["production"]["confusion_matrix"])

    sample = X_test.sample(min(60, len(X_test)), random_state=7) if len(X_test) > 0 else X_test
    importance = shap_service.global_feature_importance(bundle["production"], sample, top_n=15) if len(sample) else []
    print("\n  Top global SHAP features (anonymized SECOM sensors, mean |SHAP|):")
    for item in importance[:10]:
        print(f"    {item['feature']:15s} {item['mean_abs_shap']:.4f}")

    return {"classifier": report, "global_feature_importance": importance}


def main() -> None:
    settings = get_settings()
    model_dir = settings.resolve_path(settings.model_dir)

    report = {
        "secom": evaluate_secom(settings),
        "lot_yield": evaluate_lot_yield(settings),
    }

    report_path = model_dir / "evaluation_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\nWrote {report_path}")


if __name__ == "__main__":
    main()
