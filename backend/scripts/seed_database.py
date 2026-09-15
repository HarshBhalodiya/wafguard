"""
Seeds MongoDB from the generated synthetic CSVs (spec section 47).

Run after `generate_synthetic_data.py`. Safe to re-run — it clears and
reloads the collections it owns rather than appending duplicates.

This script seeds *base* entities (equipment, lots, process runs, wafers,
defects, maintenance, demo users). It deliberately does NOT compute yield
predictions / risk / root-cause here — those are populated by
`train_yield_model.py` (which backfills predictions once trained) or
lazily by the API on first request. See README "Database seeding".

Usage:
    cd backend
    .venv\\Scripts\\activate
    python scripts/seed_database.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.data.loaders.synthetic_wafer_loader import generate_synthetic_wafer_maps  # noqa: E402
from app.database import mongodb  # noqa: E402
from app.utils.logging import get_logger  # noqa: E402
from app.utils.security import hash_password  # noqa: E402

logger = get_logger(__name__)

DEMO_USERS = [
    {"username": "admin", "password": "wafguard-admin", "full_name": "System Administrator", "role": "admin"},
    {"username": "engineer", "password": "wafguard-engineer", "full_name": "Priya Sharma", "role": "process_engineer"},
    {"username": "yieldeng", "password": "wafguard-yield", "full_name": "Marcus Webb", "role": "yield_engineer"},
    {"username": "equipeng", "password": "wafguard-equip", "full_name": "Ines Duarte", "role": "equipment_engineer"},
    {"username": "viewer", "password": "wafguard-viewer", "full_name": "Read-Only Viewer", "role": "viewer"},
]

WAFER_MAP_POOL_PER_CLASS = 80
WAFER_MAP_GRID_SIZE = 48


def _read_csv(synthetic_dir: Path, name: str) -> pd.DataFrame:
    path = synthetic_dir / name
    if not path.exists():
        raise SystemExit(f"{path} not found — run 'python scripts/generate_synthetic_data.py' first.")
    return pd.read_csv(path)


def build_wafer_map_pool(rng: np.random.Generator) -> dict:
    pool: dict[str, list] = {}
    for sample in generate_synthetic_wafer_maps(n_per_class=WAFER_MAP_POOL_PER_CLASS, grid_size=WAFER_MAP_GRID_SIZE, seed=7):
        pool.setdefault(sample.pattern_label, []).append(sample.die_grid)
    return pool


def seed_equipment(db, synthetic_dir: Path) -> None:
    df = _read_csv(synthetic_dir, "equipment.csv")
    db.equipment.delete_many({})
    records = df.replace({np.nan: None}).to_dict("records")
    for r in records:
        r["recent_lots"] = []
    db.equipment.insert_many(records)
    logger.info("Seeded %d equipment records.", len(records))


def seed_lots_and_process_runs(db, synthetic_dir: Path) -> pd.DataFrame:
    lots_df = _read_csv(synthetic_dir, "lots.csv")
    process_df = _read_csv(synthetic_dir, "process_parameters.csv")

    db.lots.delete_many({})
    db.process_runs.delete_many({})

    process_by_lot = process_df.set_index("lot_id")
    lot_records = []
    for _, row in lots_df.iterrows():
        proc = process_by_lot.loc[row["lot_id"]]
        lot_records.append(
            {
                "lot_id": row["lot_id"],
                "product": row["product"],
                "node": row["node"],
                "wafer_count": int(row["wafer_count"]),
                "process_start": row["process_start"],
                "equipment": row["equipment"],
                "process_step": row["process_step"],
                "status": row["status"],
                "yield": float(row["yield"]),
                "predicted_yield": None,
                "risk_probability": None,
                "risk_level": None,
                "process_parameters": {
                    "temperature": float(proc["temperature"]),
                    "pressure": float(proc["pressure"]),
                    "rf_power": float(proc["rf_power"]),
                    "gas_flow": float(proc["gas_flow"]),
                    "etch_time": float(proc["etch_time"]),
                    "voltage": float(proc["voltage"]),
                    "current": float(proc["current"]),
                },
                "is_demo_incident": bool(row["is_demo_incident"]),
                "data_source": "synthetic",
            }
        )
    db.lots.insert_many(lot_records)

    process_records = process_df.replace({np.nan: None}).to_dict("records")
    db.process_runs.insert_many(process_records)

    logger.info("Seeded %d lots and %d process runs.", len(lot_records), len(process_records))
    return lots_df


def seed_wafers_and_defects(db, synthetic_dir: Path, rng: np.random.Generator) -> None:
    wafers_df = _read_csv(synthetic_dir, "wafers.csv")
    defects_df = _read_csv(synthetic_dir, "defects.csv")

    db.wafers.delete_many({})
    db.defects.delete_many({})

    pool = build_wafer_map_pool(rng)
    wafer_records = []
    for _, row in wafers_df.iterrows():
        pattern_hint = row["pattern_hint"]
        candidates = pool.get(pattern_hint) or pool["Random"]
        die_grid = candidates[int(rng.integers(0, len(candidates)))]
        wafer_records.append(
            {
                "wafer_id": row["wafer_id"],
                "lot_id": row["lot_id"],
                "slot": int(row["slot"]),
                "yield_status": row["yield_status"],
                "synthetic_true_pattern": pattern_hint,
                "wafer_map_pattern": None,
                "wafer_map_confidence": None,
                "wafer_map_source": "wm811k_synthetic",
                "die_grid": die_grid,
            }
        )
    db.wafers.insert_many(wafer_records)

    defect_records = defects_df.replace({np.nan: None}).to_dict("records")
    if defect_records:
        db.defects.insert_many(defect_records)

    logger.info("Seeded %d wafers (with synthetic wafer maps) and %d defects.", len(wafer_records), len(defect_records))


def seed_maintenance(db, synthetic_dir: Path) -> None:
    df = _read_csv(synthetic_dir, "maintenance.csv")
    db.maintenance.delete_many({})
    db.maintenance.insert_many(df.replace({np.nan: None}).to_dict("records"))
    logger.info("Seeded %d maintenance events.", len(df))


def seed_users(db) -> None:
    db.users.delete_many({})
    for u in DEMO_USERS:
        db.users.insert_one(
            {
                "username": u["username"],
                "hashed_password": hash_password(u["password"]),
                "full_name": u["full_name"],
                "role": u["role"],
            }
        )
    logger.info("Seeded %d demo users (see README for credentials).", len(DEMO_USERS))


def main() -> None:
    settings = get_settings()
    synthetic_dir = settings.resolve_path(settings.synthetic_data_path)

    mongodb.connect()
    db = mongodb.get_database()  # raises DatabaseUnavailableError with a clear message if Mongo is down

    rng = np.random.default_rng(11)

    seed_equipment(db, synthetic_dir)
    seed_lots_and_process_runs(db, synthetic_dir)
    seed_wafers_and_defects(db, synthetic_dir, rng)
    seed_maintenance(db, synthetic_dir)
    seed_users(db)

    mongodb.ensure_indexes(db)

    print("\nDatabase seeded.")
    print("Demo logins (username / password / role):")
    for u in DEMO_USERS:
        print(f"  {u['username']:10s} / {u['password']:20s} / {u['role']}")
    print(
        "\nNote: predicted_yield / risk_probability / risk_level are still null. "
        "Run 'python scripts/train_yield_model.py' to train the models and backfill them."
    )


if __name__ == "__main__":
    main()
