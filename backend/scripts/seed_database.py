"""
Seeds MongoDB from the generated synthetic dataset (schema v2).

Run after scripts/build_dataset.py. Safe to re-run - the seed is
FINGERPRINTED (data/synthetic/_generation_metadata.json config_hash) and
RESUMABLE:

- A `seed_state` doc records which collections are complete for the
  current dataset fingerprint. Re-runs skip completed collections and
  redo only the interrupted one.
- Every insert uses a deterministic _id derived from the row's natural
  key, so a re-run can never create duplicates.
- Bulk writes are batched and retried on transient network errors
  (required for remote clusters such as Atlas M0).

If the fingerprint changes (dataset regenerated), ALL seeded collections
are wiped and reloaded from scratch.

Seeds *base* entities (equipment, chambers, recipes, lots, wafers,
defects, maintenance, electrical tests, timeline events, demo users).
Telemetry stays in Parquet (too bulky for Mongo); the events parquet is
loaded into `timeline_events` because the timeline API queries it.

Wafer die grids are NOT stored: the wafer map viewer materializes a
deterministic grid on demand (see app.services.wafer_map_store).

It deliberately does NOT compute yield predictions / risk / root-cause -
those are populated by scripts/train_yield_model.py.

Usage:
    cd backend
    .venv/Scripts/python.exe scripts/seed_database.py
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from pymongo.errors import BulkWriteError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Mongo stores a bounded per-lot defect sample for the UI/API (Atlas M0's
# 512 MB quota cannot hold all 2.2M rows). The FULL defect table always
# remains in data/synthetic/defects.csv for ML/evaluation phases. Set the
# env var to 0 for a (quota-permitting) full load.
MAX_DEFECTS_PER_LOT = int(os.environ.get("SEED_MAX_DEFECTS_PER_LOT", "50"))

from app.config import get_settings  # noqa: E402
from app.database import mongodb  # noqa: E402
from app.utils.logging import get_logger  # noqa: E402
from app.utils.mongo_sanitize import scrub_nans  # noqa: E402
from app.utils.security import hash_password  # noqa: E402

logger = get_logger(__name__)

DEMO_USERS = [
    {"username": "admin", "password": "wafguard-admin", "full_name": "System Administrator", "role": "admin"},
    {"username": "engineer", "password": "wafguard-engineer", "full_name": "Priya Sharma", "role": "process_engineer"},
    {"username": "yieldeng", "password": "wafguard-yield", "full_name": "Marcus Webb", "role": "yield_engineer"},
    {"username": "equipeng", "password": "wafguard-equip", "full_name": "Ines Duarte", "role": "equipment_engineer"},
    {"username": "viewer", "password": "wafguard-viewer", "full_name": "Read-Only Viewer", "role": "viewer"},
]

REQUIRED_FILES = [
    "lots.csv", "equipment.csv", "chambers.csv", "recipes.csv", "maintenance.csv",
    "wafers.csv", "defects.csv", "sensor_data.parquet", "electrical_tests.parquet",
    "events.parquet", "ground_truth.parquet",
]

# Remote clusters (Atlas M0 and friends) kill very large single bulk writes;
# batched inserts with retry on transient network errors make the seed finish
# reliably locally AND remotely.
BATCH_SIZE = 10_000
RETRY_ATTEMPTS = 6
RETRY_BACKOFF_SECONDS = 4

# Seeding order. Each entry: (collection, id field or None for hash-composite).
SEED_STEPS: list[tuple[str, str | None]] = [
    ("equipment", "equipment_id"),
    ("chambers", "chamber_id"),
    ("recipes", "recipe_id"),
    ("lots", "lot_id"),
    ("process_runs", None),        # one summary row per lot
    ("wafers", "wafer_id"),
    ("defects", "defect_id"),
    ("maintenance", "event_id"),
    ("electrical_tests", None),    # one row per lot
    ("timeline_events", None),     # composite event
    ("interventions", "intervention_id"),  # Phase 5: synthetic intervention history (optional file)
]
SEEDED_COLLECTIONS = [name for name, _ in SEED_STEPS]


def _dataset_fingerprint(synthetic_dir: Path) -> str:
    meta_path = synthetic_dir / "_generation_metadata.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        return str(meta.get("config_hash") or meta.get("generated_at"))
    return hashlib.sha1(str(synthetic_dir.resolve()).encode()).hexdigest()[:16]


def _expected_counts(synthetic_dir: Path) -> dict[str, int]:
    """Expected doc counts per collection from the generator metadata."""
    meta_path = synthetic_dir / "_generation_metadata.json"
    if not meta_path.exists():
        return {}
    meta = json.loads(meta_path.read_text())
    tables = meta.get("tables", {})
    return {
        "equipment": tables.get("equipment", 0),
        "chambers": tables.get("chambers", 0),
        "recipes": tables.get("recipes", 0),
        "lots": tables.get("lots", 0),
        "process_runs": tables.get("lots", 0),
        "wafers": tables.get("wafers", 0),
        "defects": tables.get("defects", 0),
        "maintenance": tables.get("maintenance", 0),
        "electrical_tests": tables.get("electrical_tests", 0),
        "timeline_events": tables.get("events", 0),
        "interventions": tables.get("interventions", 0),
    }


def _deterministic_id(values: tuple) -> str:
    raw = "|".join(str(v) for v in values)
    return hashlib.sha1(raw.encode()).hexdigest()


def insert_many_batched(collection, records: list, batch_size: int = BATCH_SIZE) -> int:
    """Insert records in batches; retry transient failures; tolerate duplicate-key
    errors from an interrupted earlier attempt (deterministic _ids make re-inserts
    idempotent)."""
    inserted = 0
    for start in range(0, len(records), batch_size):
        # Scrub here rather than in every _records builder: an empty CSV cell
        # becomes float('nan') via pandas, and a NaN double in Mongo 500s any
        # endpoint that later returns the document (NaN is not valid JSON).
        batch = scrub_nans(records[start : start + batch_size])
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                collection.insert_many(batch, ordered=False)
                inserted += len(batch)
                break
            except BulkWriteError as exc:
                codes = {e.get("code") for e in exc.details.get("writeErrors", [])}
                if codes <= {11000}:  # only duplicate-key errors: the docs are in place
                    inserted += len(batch)
                    break
                if attempt == RETRY_ATTEMPTS:
                    raise
                logger.warning("insert batch failed (%s) - retry %d/%d", codes, attempt, RETRY_ATTEMPTS)
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
            except Exception as exc:  # pymongo AutoReconnect & friends
                if attempt == RETRY_ATTEMPTS:
                    raise
                logger.warning(
                    "insert batch failed (%s: %s) - retry %d/%d",
                    type(exc).__name__, exc, attempt, RETRY_ATTEMPTS,
                )
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    return inserted


def _load(synthetic_dir: Path, name: str) -> pd.DataFrame:
    path = synthetic_dir / name
    if not path.exists():
        raise SystemExit(f"{path} not found - run 'python scripts/build_dataset.py' first.")
    if name.endswith(".parquet"):
        return pd.read_parquet(path)
    return pd.read_csv(path)


def _sanitize(value):
    """Convert numpy scalars/arrays into BSON-encodable Python types."""
    if isinstance(value, np.ndarray):
        return [_sanitize(v) for v in value.tolist()]
    if isinstance(value, np.generic):
        return _sanitize(value.item())
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    return value


def _records(df: pd.DataFrame) -> list:
    records = df.replace({np.nan: None}).to_dict("records")
    return [{k: _sanitize(v) for k, v in r.items()} for r in records]


def _with_deterministic_ids(records: list, id_field: str | None) -> list:
    """Attach a deterministic _id so re-inserts after an interruption are
    duplicate-proof. Natural key when available, content hash otherwise."""
    if id_field is not None:
        for r in records:
            r["_id"] = f"{id_field}:{r[id_field]}"
        return records
    for r in records:
        r["_id"] = _deterministic_id(tuple(sorted((k, str(v)) for k, v in r.items() if k != "_id")))
    return records


def _state_read(db) -> dict:
    return db.seed_state.find_one({"_id": "seed"}) or {}


def _state_mark(db, fingerprint: str, completed: list[str]) -> None:
    db.seed_state.replace_one(
        {"_id": "seed"},
        {"_id": "seed", "fingerprint": fingerprint, "completed": completed, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
        upsert=True,
    )


def seed_base_collections(db, synthetic_dir: Path) -> None:
    """Seed every base collection, resuming from prior progress if any."""
    fingerprint = _dataset_fingerprint(synthetic_dir)
    expected = _expected_counts(synthetic_dir)
    state = _state_read(db)
    completed: list[str] = list(state.get("completed", []))

    if state.get("fingerprint") not in (None, fingerprint):
        logger.info("Dataset fingerprint changed (%s -> %s) - full reseed.", state.get("fingerprint"), fingerprint)
        completed = []

    if state.get("fingerprint") is None:
        # No state doc but collections may exist from an older (pre-resumable)
        # seed: recover from the counts of the current dataset so we don't
        # re-push hundreds of thousands of docs.
        recovered = [
            name
            for name in SEEDED_COLLECTIONS
            if expected.get(name) and db[name].count_documents({}) == expected[name]
        ]
        if recovered:
            completed = recovered
            logger.info("Recovered progress from document counts: %s", completed)

    sources = {
        "equipment": lambda: _preprocessed_equipment(synthetic_dir),
        "chambers": lambda: _records(_load(synthetic_dir, "chambers.csv")),
        "recipes": lambda: _records(_load(synthetic_dir, "recipes.csv")),
        "lots": lambda: _lot_records(synthetic_dir),
        "process_runs": lambda: _process_run_records(synthetic_dir),
        "wafers": lambda: _records(_load(synthetic_dir, "wafers.csv")),
        "defects": lambda: _records(_load(synthetic_dir, "defects.csv")),
        "maintenance": lambda: _records(_load(synthetic_dir, "maintenance.csv")),
        "electrical_tests": lambda: _records(_load(synthetic_dir, "electrical_tests.parquet")),
        "timeline_events": lambda: _records(_load(synthetic_dir, "events.parquet")),
        # Optional: datasets generated before Phase 5 have no interventions file.
        "interventions": lambda: (
            _records(_load(synthetic_dir, "interventions.parquet"))
            if (synthetic_dir / "interventions.parquet").exists()
            else []
        ),
    }
    id_fields = dict(SEED_STEPS)

    def _bounded_defects() -> list:
        records = _records(_load(synthetic_dir, "defects.csv"))
        if MAX_DEFECTS_PER_LOT <= 0:
            return records
        df = pd.DataFrame(records)
        bounded = df.groupby("lot_id", sort=False).head(MAX_DEFECTS_PER_LOT)
        note = (
            f"defects capped at {MAX_DEFECTS_PER_LOT}/lot for Mongo "
            f"({len(bounded):,} of {len(df):,} rows); full table in defects.csv"
        )
        logger.warning("%s", note)
        print(f"[seed] {note}", flush=True)
        state_note = db.seed_state.find_one({"_id": "seed"}) or {}
        db.seed_state.update_one({"_id": "seed"}, {"$set": {"note": note}}, upsert=True)
        return bounded.to_dict("records")

    sources["defects"] = _bounded_defects

    for name in SEEDED_COLLECTIONS:
        if name in completed:
            print(f"[seed] {name}: complete (skipped)", flush=True)
            continue
        records = _with_deterministic_ids(sources[name](), id_fields[name])
        # Monotonic resume: keep whatever an interrupted earlier run already
        # landed (deterministic _ids make re-inserts duplicate-proof) and only
        # fetch/insert the missing remainder. Collections that were never
        # started still get wiped first.
        existing = db[name].count_documents({})
        if existing > len(records):
            # Collection is larger than this source produces (e.g. a full load
            # followed by a capped re-run): wipe and reload to the bound.
            print(f"[seed] {name}: {existing:,} docs exceed source {len(records):,} - reloading", flush=True)
            existing = 0
        if existing and existing == len(records):
            completed.append(name)
            _state_mark(db, fingerprint, completed)
            print(f"[seed] {name}: complete (skipped)", flush=True)
            continue
        if existing:
            print(f"[seed] {name}: resuming, {existing:,}/{len(records):,} already present", flush=True)
            have = {doc["_id"] for doc in db[name].find({}, {"_id": 1})}
            records = [r for r in records if r["_id"] not in have]
            print(f"[seed] {name}: inserting remaining {len(records):,} docs...", flush=True)
        else:
            print(f"[seed] {name}: inserting {len(records)} docs...", flush=True)
            db[name].delete_many({})
        insert_many_batched(db[name], records)
        completed.append(name)
        _state_mark(db, fingerprint, completed)
        print(f"[seed] {name}: done", flush=True)

    _state_mark(db, fingerprint, completed)
    logger.info("Seed complete for fingerprint %s: %s", fingerprint, ", ".join(completed))


def _preprocessed_equipment(synthetic_dir: Path) -> list:
    records = _records(_load(synthetic_dir, "equipment.csv"))
    for r in records:
        r["recent_lots"] = []
    return records


def _lot_records(synthetic_dir: Path) -> list:
    """lots collection keeps the v1 field names the API/UI already consume
    ("equipment" + "process_parameters"), so existing endpoints work."""
    lots = _load(synthetic_dir, "lots.csv")
    records = []
    for _, row in lots.iterrows():
        records.append(
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
    return records


def _process_run_records(synthetic_dir: Path) -> list:
    """One summary row per lot (telemetry stays in parquet)."""
    lots = _load(synthetic_dir, "lots.csv")
    return [
        {
            "lot_id": row["lot_id"],
            "equipment_id": row["equipment_id"],
            "chamber_id": row["chamber_id"],
            "process": row["process_step"],
            "timestamp": row["process_start"],
        }
        for _, row in lots.iterrows()
    ]


def seed_quality_reports(db, synthetic_dir: Path) -> None:
    """Phase 7: validate the ingested dataset and store one quality report per lot."""
    from app.quality.validation import store_reports, summarize, validate_tables

    reports = validate_tables(_load(synthetic_dir, "lots.csv"), _load(synthetic_dir, "sensor_data.parquet"))
    n = store_reports(db, reports)
    print(f"[seed] data_quality_reports: {n} lot reports, status counts {summarize(reports)['status_counts']}", flush=True)


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

    # Remote clusters (Atlas) need a longer handshake than the API's fast-fail.
    mongodb.connect(timeout_ms=10_000)
    db = mongodb.get_database()  # raises DatabaseUnavailableError with a clear message if Mongo is down

    started = time.time()
    seed_base_collections(db, synthetic_dir)
    seed_users(db)
    seed_quality_reports(db, synthetic_dir)

    mongodb.ensure_indexes(db)

    counts = {name: db[name].count_documents({}) for name in SEEDED_COLLECTIONS}
    print("\nDatabase seeded (schema v2, resumable). Collection counts:")
    for name, n in counts.items():
        print(f"  {name:18s} {n:>9,d}")
    print(f"\nTotal seed wall time: {time.time() - started:.0f}s")
    print("Demo logins (username / password / role):")
    for u in DEMO_USERS:
        print(f"  {u['username']:10s} / {u['password']:20s} / {u['role']}")
    print(
        "\nNote: predicted_yield / risk_probability / risk_level are still null. "
        "Run 'python scripts/train_yield_model.py' to train the models and backfill them."
    )


if __name__ == "__main__":
    main()
