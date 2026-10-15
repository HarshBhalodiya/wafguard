"""
Synthetic manufacturing dataset simulator.

All data produced by this package is DEMO / SIMULATED DATA. The generator
is deterministic (seed in config.json) and causally structured: hidden
faults drive sensor signatures, defect mechanisms, spatial patterns, and
yield loss, with the hidden fault labels written separately to
ground_truth.parquet (evaluation only — never a training input).
"""
from app.simulator.config import SimulatorConfig, get_simulator_config, load_config

__all__ = ["SimulatorConfig", "get_simulator_config", "load_config"]
