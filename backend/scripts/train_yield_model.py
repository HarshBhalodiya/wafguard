"""
Trains both tabular models (spec sections 9-10, 28, 36):

  Model A - secom_failure_classifier   (real UCI SECOM data)
  Model B - lot_yield_regressor + lot_yield_classifier (synthetic lots)

For each: a RandomForest baseline and an XGBoost production candidate,
evaluated on a held-out *chronological* test split (spec section 11 - no
random shuffling of time-ordered manufacturing data). Every metric here
is measured on that split; nothing is hard-coded downstream.

After training Model B, this script also backfills every lot's
predicted_yield / risk_probability / risk_level in MongoDB (if reachable),
writes a `predictions` document per lot, and raises `alerts` for
newly-HIGH-risk lots. If MongoDB isn't up yet, training still completes
and artifacts are saved - re-run this script once Mongo + seed_database.py
are ready to backfill.

Usage:
    cd backend
    .venv\\Scripts\\activate
    python scripts/train_yield_model.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from xgboost import XGBClassifier, XGBRegressor

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.data.loaders.secom_loader import load_secom  # noqa: E402
from app.data.preprocessing.secom_preprocessor import build_secom_feature_matrix  # noqa: E402
from app.database import mongodb  # noqa: E402
from app.database.repositories.equipment_repository import EquipmentRepository  # noqa: E402
from app.database.repositories.lot_repository import LotRepository  # noqa: E402
from app.database.repositories.model_version_repository import ModelVersionRepository  # noqa: E402
from app.database.repositories.prediction_repository import PredictionRepository  # noqa: E402
from app.ml.baselines import fit_classifier_baselines, fit_regressor_baselines  # noqa: E402
from app.ml.calibration import calibrate_classifier, expected_calibration_error, find_best_threshold  # noqa: E402
from app.ml.hpo import optimize_xgb_classifier, optimize_xgb_regressor  # noqa: E402
from app.ml.preprocessing import (  # noqa: E402
    NUMERIC_FEATURE_COLUMNS,
    build_lot_features_bulk,
    chronological_split,
    single_row_to_model_matrix,
    to_model_matrix,
)
from app.ops.tracking import log_run  # noqa: E402
from app.ml.yield_model import (  # noqa: E402
    ModelBundle,
    evaluate_classifier,
    evaluate_regressor,
    save_bundle,
    train_classifier,
    train_regressor,
)
from app.services import alert_dispatch_service  # noqa: E402
from app.services.risk_service import compute_historical_defect_risk, compute_risk  # noqa: E402
from app.utils.exceptions import DatabaseUnavailableError, DatasetUnavailableError  # noqa: E402
from app.utils.logging import get_logger  # noqa: E402
from app.utils.time import utc_now  # noqa: E402

logger = get_logger(__name__)
MODEL_VERSION = "2.0.0"


def train_secom_model(settings) -> ModelBundle | None:
    secom_dir = settings.resolve_path(settings.secom_data_path)
    try:
        raw = load_secom(secom_dir)
    except DatasetUnavailableError as exc:
        print(f"[SECOM] skipped: {exc}")
        return None

    X, y, timestamps, feature_columns = build_secom_feature_matrix(raw)
    X_df = pd.DataFrame(X, columns=feature_columns)

    order = timestamps.sort_values().index
    X_df, y = X_df.loc[order].reset_index(drop=True), y[order]

    n = len(X_df)
    train_end, val_end = int(n * 0.70), int(n * 0.85)
    X_train, X_val, X_test = X_df.iloc[:train_end], X_df.iloc[train_end:val_end], X_df.iloc[val_end:]
    y_train, y_val, y_test = y[:train_end], y[train_end:val_end], y[val_end:]

    print(
        f"[SECOM] train={len(X_train)} val={len(X_val)} test={len(X_test)} "
        f"| fail-rate train={y_train.mean():.3f} test={y_test.mean():.3f}"
    )

    models = train_classifier(X_train, y_train)
    metrics = {
        "validation": evaluate_classifier(models["production"], X_val, y_val),
        "baseline": evaluate_classifier(models["baseline"], X_test, y_test),
        "production": evaluate_classifier(models["production"], X_test, y_test),
    }

    bundle = ModelBundle(
        model_name="secom_failure_classifier",
        version=MODEL_VERSION,
        task="classification",
        baseline=models["baseline"],
        production=models["production"],
        feature_columns=feature_columns,
        metrics=metrics,
        dataset="UCI SECOM (real, public semiconductor manufacturing dataset)",
    )
    artifact_path = save_bundle(bundle, settings.resolve_path(settings.model_dir))
    print(f"[SECOM] saved -> {artifact_path}")
    print(f"[SECOM] production test metrics: {metrics['production']}")
    return bundle


def load_synthetic_tables(settings) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load the schema-v2 synthetic tables (CSVs + telemetry parquet).

    Accepts both layouts so the script works before and after the Phase 1
    regeneration: v2 (lots.csv + sensor_data.parquet) is preferred; the v1
    layout (lots.csv + sensor_data.csv + process_parameters.csv) still loads.
    """
    data_dir = settings.resolve_path(settings.synthetic_data_path)
    if (data_dir / "sensor_data.parquet").exists():
        lots = pd.read_csv(data_dir / "lots.csv")
        sensor = pd.read_parquet(data_dir / "sensor_data.parquet")
        maintenance = pd.read_csv(data_dir / "maintenance.csv")

        # v2 lots.csv carries equipment_id; derive the v1-style view the
        # feature builder expects (equipment + scalar process params).
        lots = lots.rename(columns={"equipment_id": "equipment"})
        lots["node"] = "synthetic"
        lots["is_demo_incident"] = False

        # Scalar per-run parameters = per-lot telemetry means (plus direct
        # params derived from their channels). Downsample telemetry to
        # per-lot/parameter means to form `process_parameters`.
        agg = (
            sensor.groupby(["lot_id", "parameter"])["value"].mean().unstack("parameter").reset_index()
        )
        agg = agg.rename(
            columns={
                "pressure": "pressure",
                "temperature": "temperature",
                "rf_power": "rf_power",
                "gas_flow": "gas_flow",
            }
        )
        for col in ("etch_time", "voltage", "current"):
            if col not in agg.columns:
                agg[col] = {"etch_time": 35.0, "voltage": 220.0, "current": 11.0}[col]
        process = agg
        sensor_for_features = sensor.rename(columns={"ts_index": "reading_index"})
        return lots, process, sensor_for_features, maintenance

    # v1 fallback
    required = ["lots.csv", "process_parameters.csv", "sensor_data.csv", "maintenance.csv"]
    missing = [name for name in required if not (data_dir / name).exists()]
    if missing:
        raise SystemExit(
            f"Missing synthetic data files {missing} in {data_dir} - "
            "run 'python scripts/build_dataset.py' first."
        )
    return (
        pd.read_csv(data_dir / "lots.csv"),
        pd.read_csv(data_dir / "process_parameters.csv"),
        pd.read_csv(data_dir / "sensor_data.csv"),
        pd.read_csv(data_dir / "maintenance.csv"),
    )


def train_lot_yield_models(
    settings,
    tables: dict | None = None,
    n_hpo_trials: int = 40,
    calibration_method: str = "isotonic",
) -> tuple[ModelBundle, ModelBundle, pd.DataFrame, list]:
    """Train on the v2 tables if given in-memory (tests), else load from disk.

    Phase 2A additions: fixed-budget Optuna HPO on the validation split,
    probability calibration (prefit on validation), and simple baseline
    models evaluated on the same chronological test split.
    """
    if tables is not None:
        lots_df = tables["lots"].rename(columns={"equipment_id": "equipment"})
        lots_df["node"] = "synthetic"
        lots_df["is_demo_incident"] = False
        sensor_df = tables["sensor_data"].rename(columns={"ts_index": "reading_index"})
        maintenance_df = tables["maintenance"]
        agg = tables["sensor_data"].groupby(["lot_id", "parameter"])["value"].mean().unstack("parameter").reset_index()
        for col in ("etch_time", "voltage", "current"):
            if col not in agg.columns:
                agg[col] = {"etch_time": 35.0, "voltage": 220.0, "current": 11.0}[col]
        process_df = agg
    else:
        lots_df, process_df, sensor_df, maintenance_df = load_synthetic_tables(settings)
    features_df = build_lot_features_bulk(lots_df, process_df, sensor_df, maintenance_df)

    train_df, val_df, test_df = chronological_split(features_df)
    print(
        f"[LotYield] train={len(train_df)} val={len(val_df)} test={len(test_df)} "
        f"| low-yield rate train={train_df.is_low_yield.mean():.3f} test={test_df.is_low_yield.mean():.3f}"
    )

    X_train, dummy_columns = to_model_matrix(train_df)
    X_val, _ = to_model_matrix(val_df, known_dummy_columns=dummy_columns)
    X_test, _ = to_model_matrix(test_df, known_dummy_columns=dummy_columns)

    y_reg_train = train_df["yield"].to_numpy()
    y_reg_val = val_df["yield"].to_numpy()
    y_reg_test = test_df["yield"].to_numpy()
    y_clf_train = train_df["is_low_yield"].to_numpy()
    y_clf_val = val_df["is_low_yield"].to_numpy()
    y_clf_test = test_df["is_low_yield"].to_numpy()

    # ---- Regressor: RF baseline, Optuna-tuned XGBoost production ----
    reg_models = train_regressor(X_train, y_reg_train)
    reg_best_params, reg_hpo = optimize_xgb_regressor(X_train, y_reg_train, X_val, y_reg_val, n_trials=n_hpo_trials)
    if reg_best_params:
        reg_models["production"] = XGBRegressor(
            **reg_best_params, random_state=42, n_jobs=-1
        )
        reg_models["production"].fit(X_train, y_reg_train)

    reg_metrics = {
        "validation": evaluate_regressor(reg_models["production"], X_val, y_reg_val),
        "baseline": evaluate_regressor(reg_models["baseline"], X_test, y_reg_test),
        "production": evaluate_regressor(reg_models["production"], X_test, y_reg_test),
        "hpo": reg_hpo,
        "baselines": {
            name: evaluate_regressor(model, X_test, y_reg_test)
            for name, model in fit_regressor_baselines(X_train, y_reg_train).items()
        },
    }
    reg_bundle = ModelBundle(
        model_name="lot_yield_regressor",
        version=MODEL_VERSION,
        task="regression",
        baseline=reg_models["baseline"],
        production=reg_models["production"],
        feature_columns=dummy_columns,
        metrics=reg_metrics,
        dataset="Synthetic lots (DEMO / SIMULATED DATA)",
    )
    save_bundle(reg_bundle, settings.resolve_path(settings.model_dir))
    print(f"[LotYield regressor] production test metrics: {reg_metrics['production']}")

    # ---- Classifier: RF baseline, Optuna-tuned + calibrated XGBoost ----
    clf_models = train_classifier(X_train, y_clf_train)
    clf_best_params, clf_hpo = optimize_xgb_classifier(X_train, y_clf_train, X_val, y_clf_val, n_trials=n_hpo_trials)
    if clf_best_params:
        clf_models["production"] = XGBClassifier(
            **clf_best_params,
            scale_pos_weight=(len(y_clf_train) - y_clf_train.sum()) / max(1, int(y_clf_train.sum())),
            eval_metric="logloss",
            random_state=42,
            n_jobs=-1,
        )
        clf_models["production"].fit(X_train, y_clf_train)

    calibration_info: dict = {"method": None}
    production_clf = clf_models["production"]
    decision_threshold = 0.5
    if calibration_method and len(np.unique(y_clf_val)) > 1:
        ece_before = expected_calibration_error(y_clf_val, production_clf.predict_proba(X_val)[:, 1])
        production_clf = calibrate_classifier(production_clf, X_val, y_clf_val, method=calibration_method)
        ece_after = expected_calibration_error(y_clf_val, production_clf.predict_proba(X_val)[:, 1])
        # Calibration rescales probabilities, so 0.5 is no longer the same
        # operating point — re-pick the threshold on validation (never test).
        decision_threshold = find_best_threshold(y_clf_val, production_clf.predict_proba(X_val)[:, 1])
        calibration_info = {
            "method": calibration_method,
            "val_ece_before": ece_before,
            "val_ece_after": ece_after,
            "decision_threshold": decision_threshold,
        }
        print(
            f"[LotYield classifier] calibration ({calibration_method}): val ECE {ece_before:.4f} -> {ece_after:.4f} "
            f"| decision threshold {decision_threshold:.3f}"
        )
    clf_models["production"] = production_clf

    clf_metrics = {
        "validation": evaluate_classifier(production_clf, X_val, y_clf_val, threshold=decision_threshold),
        "baseline": evaluate_classifier(clf_models["baseline"], X_test, y_clf_test),
        "production": evaluate_classifier(production_clf, X_test, y_clf_test, threshold=decision_threshold),
        "hpo": clf_hpo,
        "calibration": calibration_info,
        "baselines": {
            name: evaluate_classifier(model, X_test, y_clf_test)
            for name, model in fit_classifier_baselines(X_train, y_clf_train).items()
        },
    }
    clf_bundle = ModelBundle(
        model_name="lot_yield_classifier",
        version=MODEL_VERSION,
        task="classification",
        baseline=clf_models["baseline"],
        production=production_clf,
        feature_columns=dummy_columns,
        metrics=clf_metrics,
        dataset="Synthetic lots (DEMO / SIMULATED DATA)",
    )
    save_bundle(clf_bundle, settings.resolve_path(settings.model_dir))
    print(f"[LotYield classifier] production test metrics: {clf_metrics['production']}")

    artifacts_dir = settings.resolve_path(settings.model_dir)
    features_path = artifacts_dir / "lot_features_snapshot.parquet"
    features_df.to_parquet(features_path, index=False)
    print(f"[LotYield] saved engineered feature snapshot -> {features_path}")

    # Phase 7: experiment tracking (local MLflow file store; a no-op if unavailable).
    split_info = {"split": "chronological 70/15/15", "n_train": len(train_df), "n_val": len(val_df), "n_test": len(test_df)}
    for bundle, params in ((reg_bundle, reg_best_params), (clf_bundle, clf_best_params)):
        run_id = log_run(
            run_name=f"{bundle.model_name}_v{bundle.version}",
            model_name=bundle.model_name,
            model_version=bundle.version,
            params={**(params or {}), **split_info, "n_hpo_trials": n_hpo_trials, "calibration": calibration_method},
            metrics={k: v for k, v in bundle.metrics.items() if k in ("validation", "production", "baseline", "calibration")},
            artifacts=[artifacts_dir / f"{bundle.model_name}_v{bundle.version}.metadata.json"],
        )
        if run_id:
            print(f"[MLflow] logged {bundle.model_name} run {run_id}")

    return reg_bundle, clf_bundle, features_df, dummy_columns


def sync_model_versions(db, bundles: list[ModelBundle]) -> None:
    repo = ModelVersionRepository(db)
    for bundle in bundles:
        if bundle is None:
            continue
        repo.insert_one(
            {
                "model_name": bundle.model_name,
                "version": bundle.version,
                "task": bundle.task,
                "dataset": bundle.dataset,
                "trained_at": bundle.trained_at,
                "features": bundle.feature_columns,
                "metrics": bundle.metrics,
            }
        )
    print(f"[Mongo] recorded {sum(1 for b in bundles if b is not None)} model_versions entries.")


def backfill_predictions(db, features_df: pd.DataFrame, dummy_columns: list, reg_bundle: ModelBundle, clf_bundle: ModelBundle) -> None:
    lot_repo = LotRepository(db)
    equipment_repo = EquipmentRepository(db)
    prediction_repo = PredictionRepository(db)

    db.predictions.delete_many({})
    db.alerts.delete_many({})

    equipment_by_id = {e["equipment_id"]: e for e in equipment_repo.all()}
    features_sorted = features_df.sort_values("process_start").reset_index(drop=True)

    risk_level_counts = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
    for _, row in features_sorted.iterrows():
        numeric_snapshot = {c: float(row[c]) for c in NUMERIC_FEATURE_COLUMNS}
        full_snapshot = {**numeric_snapshot, "equipment": row["equipment"], "process_step": row["process_step"]}

        X = single_row_to_model_matrix(full_snapshot, dummy_columns)
        predicted_yield = round(float(reg_bundle.production.predict(X)[0]), 2)
        model_probability = round(float(clf_bundle.production.predict_proba(X)[0][1]), 4)

        equipment_doc = equipment_by_id.get(row["equipment"])
        prior_same_equipment = features_sorted[
            (features_sorted.equipment == row["equipment"]) & (features_sorted.process_start < row["process_start"])
        ].tail(10)
        historical_defect_risk = compute_historical_defect_risk(prior_same_equipment["yield"].tolist())

        risk = compute_risk(model_probability, full_snapshot, equipment_doc, historical_defect_risk)
        risk_level_counts[risk["risk_level"]] += 1

        lot_repo.update_prediction(
            row["lot_id"],
            {
                "predicted_yield": predicted_yield,
                "risk_probability": risk["risk_probability"],
                "risk_level": risk["risk_level"],
            },
        )
        prediction_repo.insert_one(
            {
                "lot_id": row["lot_id"],
                "model_name": clf_bundle.model_name,
                "model_version": clf_bundle.version,
                "predicted_yield": predicted_yield,
                "risk_probability": risk["risk_probability"],
                "risk_level": risk["risk_level"],
                "features_snapshot": {**full_snapshot, "model_probability": model_probability},
                "risk_breakdown": risk["breakdown"],
                "timestamp": utc_now(),
            }
        )

        if risk["risk_level"] == "HIGH":
            # Phase 4: alerts go through the dispatcher (log channel by default;
            # webhook/SMTP when configured) so a raised alert is always *delivered*
            # and every attempt is recorded in alert_deliveries.
            alert_dispatch_service.raise_alert(
                db,
                lot_id=row["lot_id"],
                severity="HIGH",
                risk_level="HIGH",
                risk_probability=risk["risk_probability"],
                reason=(
                    f"Lot {row['lot_id']} on {row['equipment']} is at "
                    f"{risk['risk_probability'] * 100:.0f}% predicted low-yield risk."
                ),
                source="backfill",
            )

    print(f"[Backfill] updated {len(features_sorted)} lots. Risk level distribution: {risk_level_counts}")


def store_monitoring_snapshots(db, clf_bundle: ModelBundle) -> None:
    """Phase 7: back-fill one model-monitoring snapshot per day from the fresh predictions."""
    from app.quality import monitoring

    threshold = (clf_bundle.metrics.get("calibration") or {}).get("decision_threshold") or monitoring.DEFAULT_DECISION_THRESHOLD
    snapshots = monitoring.build_daily_snapshots(
        monitoring.monitoring_frame_from_db(db), threshold=float(threshold), model_version=clf_bundle.version
    )
    monitoring.store_snapshots(db, snapshots)
    print(f"[Monitoring] stored {len(snapshots)} daily snapshots.")


def main() -> None:
    settings = get_settings()

    secom_bundle = train_secom_model(settings)
    reg_bundle, clf_bundle, features_df, dummy_columns = train_lot_yield_models(settings)

    # Remote clusters (Atlas) need a longer handshake than the API's fast-fail.
    mongodb.connect(timeout_ms=10_000)
    try:
        db = mongodb.get_database()
    except DatabaseUnavailableError as exc:
        print(f"\n[Mongo] {exc}")
        print("Training complete, but MongoDB isn't reachable — re-run this script once it is, to backfill predictions.")
        return

    sync_model_versions(db, [secom_bundle, reg_bundle, clf_bundle])
    backfill_predictions(db, features_df, dummy_columns, reg_bundle, clf_bundle)
    store_monitoring_snapshots(db, clf_bundle)
    print("\nDone. Dashboard/Lots/Risk Monitor endpoints now have real predictions to serve.")


if __name__ == "__main__":
    main()
