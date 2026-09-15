"""
Shared pytest fixtures.

Tests run against `mongomock` rather than a real MongoDB — that keeps the
suite runnable with no external dependency, while every repository/service
under test still goes through the exact same pymongo-style calls they'd
make against a real server. Seeding + training happen once per session
(seeding ~23k wafer records and training two models isn't free), and
individual tests treat the shared DB as read-mostly; the couple of
services that write (root-cause/recommendation upserts) are idempotent
per lot_id, so sharing is safe.
"""
import sys
from pathlib import Path

import mongomock
import numpy as np
import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "scripts"))

from app.config import get_settings  # noqa: E402


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture(scope="session")
def trained_bundles(settings):
    import train_yield_model as tym

    secom_bundle = tym.train_secom_model(settings)
    reg_bundle, clf_bundle, features_df, dummy_columns = tym.train_lot_yield_models(settings)
    return {
        "secom": secom_bundle,
        "regressor": reg_bundle,
        "classifier": clf_bundle,
        "features_df": features_df,
        "dummy_columns": dummy_columns,
    }


@pytest.fixture(scope="session")
def db(settings, trained_bundles):
    import seed_database as sd
    import train_yield_model as tym

    client = mongomock.MongoClient()
    database = client["wafguard_test"]

    synthetic_dir = settings.resolve_path(settings.synthetic_data_path)
    rng = np.random.default_rng(11)

    sd.seed_equipment(database, synthetic_dir)
    sd.seed_lots_and_process_runs(database, synthetic_dir)
    sd.seed_wafers_and_defects(database, synthetic_dir, rng)
    sd.seed_maintenance(database, synthetic_dir)
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


@pytest.fixture(scope="session")
def incident_lot_id() -> str:
    return "LOT_10901"
