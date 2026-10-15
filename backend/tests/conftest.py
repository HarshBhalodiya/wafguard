"""
Shared pytest fixtures.

Tests run against `mongomock` rather than a real MongoDB, and generate
their own small deterministic dataset (200 lots via the Phase 1 simulator)
instead of depending on data/synthetic/ being present. Seeding + training
happen once per session; individual tests treat the shared DB as
read-mostly; the couple of services that write (root-cause/recommendation
upserts, wafer-map patterns) are idempotent per lot_id, so sharing is safe.
"""
import shutil
import sys
from pathlib import Path

import mongomock
import numpy as np
import pandas as pd
import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "scripts"))

from app.config import get_settings  # noqa: E402
from app.simulator.config import load_config  # noqa: E402
from app.simulator.generator import generate_dataset  # noqa: E402

TEST_LOT_COUNT = 1500


@pytest.fixture(scope="session")
def settings(tmp_path_factory):
    """App settings with `model_dir` redirected to a session tmp copy (P6-5).

    Training fixtures call save_bundle(), which used to write the small
    in-session test models OVER the real models/artifacts/ — so a pytest run
    silently replaced production artifacts (phase2 report known problem 2).
    Now the real artifacts are copied into a tmp dir once per session and the
    tests train/serve there; the repo's models/artifacts/ is never touched.
    The copy (not an empty dir) keeps the wafer CNN, evidence table and
    embeddings loadable for the vision/similarity tests.
    """
    settings = get_settings()
    real_dir = settings.resolve_path(settings.model_dir)
    isolated = tmp_path_factory.mktemp("model_artifacts")
    if real_dir.exists():
        for item in real_dir.iterdir():
            if item.is_dir():
                shutil.copytree(item, isolated / item.name, dirs_exist_ok=True)
            else:
                shutil.copy2(item, isolated / item.name)
    settings.model_dir = str(isolated)
    # Phase 7: experiment tracking writes to a session tmp store, never ./mlruns.
    settings.mlflow_tracking_uri = str(tmp_path_factory.mktemp("mlruns"))
    return settings


@pytest.fixture(scope="session")
def sim_tables():
    """Small deterministic synthetic dataset generated in-process for tests."""
    cfg = load_config()
    cfg = dataclasses_replace(cfg, n_lots=TEST_LOT_COUNT)
    return generate_dataset(cfg)


def dataclasses_replace(cfg, **kwargs):
    import dataclasses

    return dataclasses.replace(cfg, **kwargs)


@pytest.fixture(scope="session")
def trained_bundles(settings, sim_tables):
    import train_yield_model as tym

    secom_bundle = tym.train_secom_model(settings)
    reg_bundle, clf_bundle, features_df, dummy_columns = tym.train_lot_yield_models(
        settings, tables=sim_tables, n_hpo_trials=5
    )
    return {
        "secom": secom_bundle,
        "regressor": reg_bundle,
        "classifier": clf_bundle,
        "features_df": features_df,
        "dummy_columns": dummy_columns,
    }


@pytest.fixture(scope="session")
def db(settings, sim_tables, trained_bundles):
    import seed_database as sd
    import train_yield_model as tym

    client = mongomock.MongoClient()
    database = client["wafguard_test"]

    # Seed from the in-process generated tables (no disk dependency).
    _seed_from_tables(database, sim_tables)
    sd.seed_users(database)

    tym.sync_model_versions(database, [trained_bundles["secom"], trained_bundles["regressor"], trained_bundles["classifier"]])
    tym.backfill_predictions(
        database,
        trained_bundles["features_df"],
        trained_bundles["dummy_columns"],
        trained_bundles["regressor"],
        trained_bundles["classifier"],
    )
    return database


def _seed_from_tables(database, tables: dict) -> None:
    """Seed the mongomock database directly from generator output (schema v2),
    mirroring seed_database.seed_base_collections without touching the disk."""
    import numpy as _np

    def recs(df: pd.DataFrame) -> list:
        return df.replace({_np.nan: None}).to_dict("records")

    database.equipment.delete_many({})
    equipment_records = recs(tables["equipment"])
    for r in equipment_records:
        r["recent_lots"] = []
    database.equipment.insert_many(equipment_records)

    database.chambers.delete_many({})
    database.chambers.insert_many(recs(tables["chambers"]))
    database.recipes.delete_many({})
    database.recipes.insert_many(recs(tables["recipes"]))

    database.lots.delete_many({})
    database.process_runs.delete_many({})
    lot_records = []
    for _, row in tables["lots"].iterrows():
        lot_records.append(
            {
                "lot_id": row["lot_id"],
                "product": row["product"],
                "recipe_id": row["recipe_id"],
                "process_step": row["process_step"],
                "equipment": row["equipment_id"],
                "equipment_id": row["equipment_id"],
                "chamber_id": row["chamber_id"],
                "process_start": row["process_start"],
                "process_end": row["process_end"],
                "wafer_count": int(row["wafer_count"]),
                "status": row["status"],
                "yield": float(row["yield"]),
                "predicted_yield": None,
                "risk_probability": None,
                "risk_level": None,
                "defect_probability": float(row["defect_probability"]),
                "defect_density": float(row["defect_density"]),
                "spatial_signature": row["spatial_signature"],
                "equipment_days_since_pm": float(row["equipment_days_since_pm"]),
                "is_simulated": True,
                "data_source": "synthetic",
            }
        )
    database.lots.insert_many(lot_records)
    database.process_runs.insert_many(
        [
            {
                "lot_id": row["lot_id"],
                "equipment_id": row["equipment_id"],
                "chamber_id": row["chamber_id"],
                "process": row["process_step"],
                "timestamp": row["process_start"],
            }
            for _, row in tables["lots"].iterrows()
        ]
    )

    database.wafers.delete_many({})
    database.wafers.insert_many(recs(tables["wafers"]))
    database.defects.delete_many({})
    defect_records = recs(tables["defects"])
    if defect_records:
        database.defects.insert_many(defect_records)
    database.maintenance.delete_many({})
    database.maintenance.insert_many(recs(tables["maintenance"]))
    database.electrical_tests.delete_many({})
    database.electrical_tests.insert_many(recs(tables["electrical_tests"]))
    database.timeline_events.delete_many({})
    event_records = recs(tables["events"])
    if event_records:
        database.timeline_events.insert_many(event_records)
    database.interventions.delete_many({})
    intervention_records = recs(tables["interventions"])
    if intervention_records:
        database.interventions.insert_many(intervention_records)


@pytest.fixture(scope="session", autouse=True)
def injected_pattern_cause_table(sim_tables):
    """Phase 3 (P3-1): build the measured pattern→cause table from the in-process
    test dataset (train split, via app.evaluation) and inject it into the root-cause
    engine — tests then exercise the *measured* path hermetically, never depending on
    the on-disk artifact under models/artifacts/."""
    from app.evaluation.pattern_associations import build_pattern_cause_table
    from app.services import rootcause_service

    table = build_pattern_cause_table(
        sim_tables["lots"][["lot_id", "process_start"]], sim_tables["ground_truth"]
    )
    rootcause_service.set_pattern_cause_table(table)
    yield table
    rootcause_service.reset_pattern_cause_table()


@pytest.fixture(scope="session")
def incident_lot_id(sim_tables, trained_bundles) -> str:
    """A deterministic faulty lot to anchor investigation tests on.

    Must come from the *test* split (chronologically last 15%): models are
    trained on earlier lots, so a training-split lot can be memorized and
    scored LOW-risk — the opposite of what investigation tests assert.
    Picking a test-split faulty lot also matches reality: investigations
    target recent lots the model has never seen.
    """
    from app.ml.preprocessing import chronological_split

    features_df = trained_bundles["features_df"]
    _, _, test_df = chronological_split(features_df.copy())
    test_ids = set(test_df["lot_id"])

    gt = sim_tables["ground_truth"]
    faulty = gt[gt["lot_id"].isin(test_ids) & (gt["true_fault"] == "rf_power_drift")]
    if faulty.empty:
        faulty = gt[gt["lot_id"].isin(test_ids) & (gt["true_fault"] != "none")]
    if faulty.empty:
        pytest.skip("no faulty lots in the test split of the generated dataset")
    return str(faulty["lot_id"].iloc[0])
