"""
Central application configuration.

Everything here is read from environment variables (see .env.example).
Risk/root-cause scoring weights live here too, per the project design's
requirement that the scoring methodology be explicit and configurable
rather than hard-coded inline in service logic.
"""
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- MongoDB ---
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_database: str = "wafguard"

    # --- Dataset paths ---
    secom_data_path: str = "./data/secom"
    wm811k_data_path: str = "./data/wm811k"
    synthetic_data_path: str = "./data/synthetic"

    # --- Model artifacts ---
    model_dir: str = "./backend/models/artifacts"

    # --- API server ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- Auth ---
    jwt_secret_key: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480

    # --- Optional LLM copilot ---
    llm_api_key: str = ""
    llm_model: str = ""

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def resolve_path(self, relative: str) -> Path:
        """Resolve a configured path relative to the project root."""
        p = Path(relative)
        return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()


@lru_cache
def get_settings() -> "Settings":
    return Settings()


# ---------------------------------------------------------------------------
# Risk engine weights (spec: "make the scoring methodology explicit and
# configurable" — see docs/risk_and_rootcause_methodology.md for the
# full write-up of how each term is computed).
# ---------------------------------------------------------------------------
RISK_WEIGHTS = {
    "model_probability": 0.45,
    "parameter_deviation": 0.25,
    "equipment_risk": 0.20,
    "historical_defect_risk": 0.10,
}

RISK_THRESHOLDS = {
    "low_max": 30,
    "medium_max": 60,
    # anything above medium_max is HIGH
}

# ---------------------------------------------------------------------------
# Root-cause multi-signal engine weights
# ---------------------------------------------------------------------------
ROOTCAUSE_WEIGHTS = {
    "shap_attribution": 0.30,
    "parameter_deviation": 0.20,
    "historical_occurrence": 0.15,
    "equipment_association": 0.15,
    "wafer_pattern": 0.10,
    "temporal_evidence": 0.10,
}

ROOTCAUSE_CONFIDENCE_THRESHOLDS = {
    "high_min": 0.70,
    "medium_min": 0.40,
    # below medium_min is LOW confidence
}

# Below this synthetic `yield` value a lot counts as "low yield" — used to
# derive the classification target and to define "historical defect risk"
# (spec section 12: fraction of an equipment's recent lots that were low-yield).
LOW_YIELD_THRESHOLD = 85.0

# ---------------------------------------------------------------------------
# Process parameter "normal operating range" reference.
#
# Single source of truth for what counts as a deviation — used by BOTH the
# synthetic data generator (to decide what's an "abnormal" reading) and the
# risk/root-cause engines (to score how far a real reading has drifted).
# Keeping one copy means the risk engine's notion of "normal" can never
# quietly drift out of sync with the data it's scoring.
# ---------------------------------------------------------------------------
PROCESS_PARAMETER_RANGES = {
    "temperature": (430.0, 445.0),
    "pressure": (2.4, 3.1),
    "rf_power": (840.0, 875.0),
    "gas_flow": (45.0, 58.0),
    "etch_time": (30.0, 40.0),
    "voltage": (200.0, 240.0),
    "current": (8.0, 14.0),
}

# WM-811K canonical pattern taxonomy (Wu, Jang & Chen; used by the public
# WM-811K dataset). Kept as a single source of truth for the vision model,
# the synthetic wafer-map generator, and the frontend.
WAFER_PATTERN_CLASSES = [
    "Center",
    "Donut",
    "Edge-Loc",
    "Edge-Ring",
    "Loc",
    "Random",
    "Scratch",
    "Near-full",
    "None",
]
