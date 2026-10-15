"""
Simulator configuration loading.

`config.json` in this package is the single source of truth for the
synthetic dataset (seeds, counts, fault rates, telemetry shape). Everything
the generator does is either a constant documented here or derived from a
value in this file, so one edit + one regeneration changes the whole
dataset deterministically.
"""
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


@dataclass(frozen=True)
class TelemetryConfig:
    sample_interval_seconds: int
    run_duration_seconds: int
    channels: list
    dropout_rate: float
    stuck_rate: float

    @property
    def n_samples(self) -> int:
        return self.run_duration_seconds // self.sample_interval_seconds


@dataclass(frozen=True)
class ElectricalConfig:
    vbd_mean_normal: float
    vbd_std: float
    leak_mean_normal: float
    leak_std: float
    rsq_mean_normal: float
    rsq_std: float


@dataclass(frozen=True)
class SimulatorConfig:
    schema_version: int
    seed: int
    n_lots: int
    end_date: str
    lookback_days: int
    wafers_per_lot: int
    low_yield_threshold: float
    low_yield_fraction: float
    fault_rates: dict
    telemetry: TelemetryConfig
    electrical: ElectricalConfig

    @property
    def fault_names(self) -> list:
        return list(self.fault_rates.keys())


def load_config(path: Path | None = None) -> SimulatorConfig:
    """Load and validate the simulator config. Raises SystemExit on bad config."""
    with open(path or CONFIG_PATH, encoding="utf-8") as f:
        raw = json.load(f)

    rates = raw["fault_rates"]
    if not 0.0 <= sum(rates.values()) <= 1.0 + 1e-9:
        raise SystemExit(f"fault_rates must sum to <= 1.0 (got {sum(rates.values()):.4f})")
    if rates.get("none", 0) <= 0:
        raise SystemExit("fault_rates must include a positive 'none' probability")
    if raw["n_lots"] < 100:
        raise SystemExit("n_lots must be at least 100")
    if raw["wafers_per_lot"] < 1:
        raise SystemExit("wafers_per_lot must be >= 1")
    if raw["telemetry"]["sample_interval_seconds"] <= 0:
        raise SystemExit("telemetry.sample_interval_seconds must be positive")
    if raw["low_yield_threshold"] <= 0 or raw["low_yield_threshold"] >= 100:
        raise SystemExit("low_yield_threshold must be in (0, 100)")

    return SimulatorConfig(
        schema_version=raw["schema_version"],
        seed=raw["seed"],
        n_lots=raw["n_lots"],
        end_date=raw["end_date"],
        lookback_days=raw["lookback_days"],
        wafers_per_lot=raw["wafers_per_lot"],
        low_yield_threshold=raw["low_yield_threshold"],
        low_yield_fraction=raw["low_yield_fraction"],
        fault_rates=rates,
        telemetry=TelemetryConfig(**raw["telemetry"]),
        electrical=ElectricalConfig(**raw["electrical"]),
    )


@lru_cache
def get_simulator_config() -> SimulatorConfig:
    return load_config()
