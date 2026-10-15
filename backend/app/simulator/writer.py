"""
Writer: persists generator output to data/synthetic/ as CSVs (entities),
Parquet (telemetry + electrical + events + ground truth), and a metadata
JSON with the config hash so any dataset on disk is traceable to the exact
config that produced it.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.config import get_settings
from app.simulator.config import SimulatorConfig

ENTITY_CSVS = ["lots", "equipment", "chambers", "recipes", "maintenance", "wafers", "defects"]
PARQUET_TABLES = ["sensor_data", "electrical_tests", "events", "ground_truth", "interventions"]


def _config_hash(cfg: SimulatorConfig) -> str:
    payload = json.dumps(
        {
            "seed": cfg.seed,
            "n_lots": cfg.n_lots,
            "end_date": cfg.end_date,
            "lookback_days": cfg.lookback_days,
            "wafers_per_lot": cfg.wafers_per_lot,
            "low_yield_threshold": cfg.low_yield_threshold,
            "low_yield_fraction": cfg.low_yield_fraction,
            "fault_rates": cfg.fault_rates,
            "telemetry": {
                "sample_interval_seconds": cfg.telemetry.sample_interval_seconds,
                "run_duration_seconds": cfg.telemetry.run_duration_seconds,
                "channels": cfg.telemetry.channels,
            },
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def write_dataset(cfg: SimulatorConfig, tables: dict) -> Path:
    """Write all tables to the synthetic data dir. Returns the output dir."""
    out = get_settings().resolve_path(get_settings().synthetic_data_path)
    out.mkdir(parents=True, exist_ok=True)

    for name in ENTITY_CSVS:
        tables[name].to_csv(out / f"{name}.csv", index=False)

    for name in PARQUET_TABLES:
        tables[name].to_parquet(out / f"{name}.parquet", index=False)

    metadata = {
        "schema_version": cfg.schema_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": cfg.seed,
        "n_lots": cfg.n_lots,
        "config_hash": _config_hash(cfg),
        "low_yield_threshold": cfg.low_yield_threshold,
        "low_yield_fraction_target": cfg.low_yield_fraction,
        "data_provenance": "synthetic",
        "note": "DEMO / SIMULATED DATA. Ground truth in ground_truth.parquet is for evaluation only - training code must not read it.",
        "tables": {
            **{name: len(tables[name]) for name in ENTITY_CSVS},
            **{name: len(tables[name]) for name in PARQUET_TABLES},
        },
    }
    with open(out / "_generation_metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    return out
