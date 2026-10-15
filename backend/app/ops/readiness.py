"""
Operational readiness checks (Phase 6, P6-3).

Answers one question honestly: *would this app actually serve predictions if
someone hit it right now?* `/api/health` previously reported only MongoDB
reachability — a "healthy" response with no dataset or no trained bundles is
a lie of omission, because every prediction/root-cause/similarity endpoint
degrades to a 503 "run scripts/... first" error in that state.

Design constraints (P6-3):
  * `/api/health` is unauthenticated (RBAC exemption, Phase 4) — so the
    readiness block it serves is **shallow**: booleans, version strings, and
    the dataset fingerprint only. No filesystem paths, no collection counts.
  * Every check is a pure function over explicit directories (testable
    against tmp dirs; `scripts/doctor.py` reuses the same functions with the
    full detail view).
  * Missing files degrade to `present: false` + the exact command that fixes
    it — never to an exception, never to a fabricated "ready".
"""
import json
from pathlib import Path
from typing import Optional

from app.config import get_settings

# Bundles the API needs to serve its core paths. `wafer_pattern_cnn` is
# required for the vision endpoints; the two tabular bundles gate every
# prediction/root-cause/similarity answer.
TABULAR_BUNDLES = ("lot_yield_regressor", "lot_yield_classifier")
OPTIONAL_BUNDLES = ("wafer_pattern_cnn", "secom_failure_classifier")
EVIDENCE_TABLE_FILE = "pattern_cause_associations.v1.json"
TEMPORAL_EMBEDDING_FILE = "temporal_embeddings.parquet"
VISION_EMBEDDING_FILE = "wafer_embeddings.npz"


def _latest_pointer(model_dir: Path, model_name: str) -> Optional[dict]:
    pointer_path = model_dir / f"{model_name}.latest.json"
    if not pointer_path.exists():
        return None
    try:
        with open(pointer_path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def bundle_readiness(model_dir: Path, model_name: str) -> dict:
    """One trained bundle: is the latest pointer resolvable to a real artifact?"""
    pointer = _latest_pointer(model_dir, model_name)
    if pointer is None:
        return {"present": False, "version": None}
    artifact_path = model_dir / pointer.get("artifact_file", "")
    return {"present": artifact_path.exists(), "version": pointer.get("version")}


def dataset_readiness(data_dir: Path) -> dict:
    """The synthetic dataset on disk, identified by its config fingerprint."""
    metadata_path = data_dir / "_generation_metadata.json"
    if not metadata_path.exists():
        return {
            "present": False,
            "schema_version": None,
            "config_hash": None,
            "fix": "python scripts/build_dataset.py",
        }
    try:
        with open(metadata_path, encoding="utf-8") as f:
            metadata = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return {"present": False, "schema_version": None, "config_hash": None,
                "fix": f"dataset metadata unreadable ({exc.__class__.__name__}); regenerate with python scripts/build_dataset.py"}
    return {
        "present": True,
        "schema_version": metadata.get("schema_version"),
        "config_hash": metadata.get("config_hash"),
    }


def collect_readiness(settings=None) -> dict:
    """Full readiness picture (used verbatim by scripts/doctor.py)."""
    settings = settings or get_settings()
    data_dir = settings.resolve_path(settings.synthetic_data_path)
    model_dir = settings.resolve_path(settings.model_dir)

    bundles = {
        name: bundle_readiness(model_dir, name)
        for name in (*TABULAR_BUNDLES, *OPTIONAL_BUNDLES)
    }
    dataset = dataset_readiness(data_dir)
    tabular_ready = all(bundles[name]["present"] for name in TABULAR_BUNDLES)
    return {
        "dataset": dataset,
        "bundles": bundles,
        "tabular_ready": tabular_ready,
        "evidence_table": {
            "present": (model_dir / "evidence" / EVIDENCE_TABLE_FILE).exists(),
            "fix": "python scripts/build_evidence_tables.py",
        },
        "embeddings": {
            "temporal": {"present": (model_dir / TEMPORAL_EMBEDDING_FILE).exists(),
                         "fix": "python scripts/train_temporal_model.py"},
            "vision": {"present": (model_dir / VISION_EMBEDDING_FILE).exists(),
                       "fix": "python scripts/train_wafer_model.py"},
        },
    }


def shallow_readiness(settings=None) -> dict:
    """The /api/health view: booleans + version strings + fingerprint only.

    The health route is unauthenticated, so this deliberately omits the fix
    commands' target paths and anything beyond what /docs already exposes.
    """
    full = collect_readiness(settings)
    return {
        "dataset_present": full["dataset"]["present"],
        "dataset_fingerprint": full["dataset"]["config_hash"],
        "tabular_models_ready": full["tabular_ready"],
        "model_versions": {
            name: entry["version"] for name, entry in full["bundles"].items() if entry["present"]
        },
        "evidence_table_present": full["evidence_table"]["present"],
        "temporal_embeddings_present": full["embeddings"]["temporal"]["present"],
        "vision_embeddings_present": full["embeddings"]["vision"]["present"],
    }
