"""
Train the what-if uncertainty ensemble (Phase 6, brief P5).

Reads the engineered feature snapshot written by scripts/train_yield_model.py
(models/artifacts/lot_features_snapshot.parquet) and the production regressor's
tuned hyper-parameters, fits B bootstrap XGBoost members on the TRAIN split,
measures the 80% conformal interval's coverage on the TEST split, and writes
models/artifacts/whatif_ensemble_v1.0.0.joblib + metadata.

Usage:
    cd backend
    python scripts/train_whatif_ensemble.py [--members 20]
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.ml.yield_model import load_latest_bundle  # noqa: E402
from app.ops.tracking import log_run  # noqa: E402
from app.whatif.ensemble import DEFAULT_MEMBERS, save_ensemble, train_ensemble  # noqa: E402


def train(settings, n_members: int = DEFAULT_MEMBERS, features_df: pd.DataFrame | None = None):
    model_dir = settings.resolve_path(settings.model_dir)
    regressor = load_latest_bundle("lot_yield_regressor", model_dir)
    if regressor is None:
        raise SystemExit("lot_yield_regressor is missing — run scripts/train_yield_model.py first.")
    if features_df is None:
        snapshot = model_dir / "lot_features_snapshot.parquet"
        if not snapshot.exists():
            raise SystemExit(f"{snapshot} is missing — run scripts/train_yield_model.py first.")
        features_df = pd.read_parquet(snapshot)
    params = ((regressor.get("metadata") or {}).get("metrics", {}).get("hpo") or {}).get("best_params") or None
    bundle = train_ensemble(features_df, regressor["feature_columns"], params=params, n_members=n_members)
    path = save_ensemble(bundle, model_dir, dataset="Synthetic lots (DEMO / SIMULATED DATA)")
    log_run(
        run_name=f"whatif_ensemble_v{bundle.version}",
        model_name="whatif_ensemble",
        model_version=bundle.version,
        params={"n_members": n_members, **(params or {})},
        metrics=bundle.metrics,
    )
    return bundle, path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--members", type=int, default=DEFAULT_MEMBERS)
    args = parser.parse_args()
    bundle, path = train(get_settings(), n_members=args.members)
    print(f"[whatif] saved -> {path}")
    print(json.dumps(bundle.metrics, indent=2))


if __name__ == "__main__":
    main()
