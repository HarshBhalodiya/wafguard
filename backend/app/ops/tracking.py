"""
Experiment tracking (Phase 7, brief P6) — MLflow with a local SQLite store.

`log_run()` records one training/evaluation run: dataset version (the
generator's config hash), model name + version, params, metrics and artifact
files. MLflow is optional at runtime: when it is not installed, or tracking is
disabled (`MLFLOW_ENABLED=false`), the call is a logged no-op returning None —
training must never fail because tracking is unavailable.

Tracking store: settings.mlflow_tracking_uri. A plain directory (default
`./mlruns` under the project root) becomes a local SQLite store
`<dir>/mlflow.db` with artifacts in `<dir>/artifacts` — MLflow 3 refuses the
old plain-file store. A full URI (sqlite://, http://, ...) is used as given.
Browse with:  mlflow ui --backend-store-uri sqlite:///mlruns/mlflow.db
"""
from __future__ import annotations

import json
from pathlib import Path

from app.config import get_settings
from app.utils.logging import get_logger

logger = get_logger(__name__)
EXPERIMENT = "wafguard"


def dataset_version(settings=None) -> str:
    """The synthetic dataset's config hash (or 'unknown' when no metadata exists)."""
    settings = settings or get_settings()
    meta = settings.resolve_path(settings.synthetic_data_path) / "_generation_metadata.json"
    try:
        return str(json.loads(meta.read_text(encoding="utf-8")).get("config_hash") or "unknown")
    except (OSError, ValueError):
        return "unknown"


def resolve_tracking_uri(settings) -> tuple[str, str | None]:
    """(tracking URI, artifact location) for the configured store."""
    uri = settings.mlflow_tracking_uri
    if "://" in uri:
        return uri, None
    root = settings.resolve_path(uri)
    root.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(root / 'mlflow.db').as_posix()}", (root / "artifacts").as_uri()


def _flatten(metrics: dict, prefix: str = "") -> dict[str, float]:
    flat: dict[str, float] = {}
    for key, value in (metrics or {}).items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{name}."))
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and value == value:
            flat[name] = float(value)
    return flat


def log_run(
    run_name: str,
    model_name: str,
    model_version: str,
    params: dict | None = None,
    metrics: dict | None = None,
    artifacts: list | None = None,
    tags: dict | None = None,
) -> str | None:
    """Log one run. Returns the MLflow run id, or None when tracking is off/unavailable."""
    settings = get_settings()
    if not settings.mlflow_enabled:
        return None
    try:
        import mlflow
    except ImportError:
        logger.warning("mlflow is not installed — skipping experiment tracking for %s", run_name)
        return None
    try:
        uri, artifact_location = resolve_tracking_uri(settings)
        mlflow.set_tracking_uri(uri)
        if mlflow.get_experiment_by_name(EXPERIMENT) is None:
            mlflow.create_experiment(EXPERIMENT, artifact_location=artifact_location)
        mlflow.set_experiment(EXPERIMENT)
        with mlflow.start_run(run_name=run_name) as run:
            mlflow.set_tags(
                {
                    "model_name": model_name,
                    "model_version": model_version,
                    "dataset_version": dataset_version(settings),
                    "data_provenance": "synthetic",
                    **(tags or {}),
                }
            )
            flat_params = {k: str(v)[:250] for k, v in _flatten_params(params or {}).items()}
            if flat_params:
                mlflow.log_params(flat_params)
            flat_metrics = _flatten(metrics or {})
            if flat_metrics:
                mlflow.log_metrics({k.replace("@", "_at_"): v for k, v in flat_metrics.items()})
            for artifact in artifacts or []:
                if Path(artifact).exists():
                    mlflow.log_artifact(str(artifact))
            return run.info.run_id
    except Exception:  # noqa: BLE001 - tracking must never break training
        logger.exception("MLflow logging failed for %s; training result is unaffected.", run_name)
        return None


def _flatten_params(params: dict, prefix: str = "") -> dict:
    flat = {}
    for key, value in params.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten_params(value, f"{name}."))
        else:
            flat[name] = value
    return flat
