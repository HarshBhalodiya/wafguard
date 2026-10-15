"""
Add the Phase 5 intervention history to an EXISTING synthetic dataset.

`python scripts/build_dataset.py` now writes interventions.parquet itself; this
script exists so a dataset generated before Phase 5 does not have to be fully
regenerated. It produces exactly what the generator would (same function, same
inputs, same seed) and records the table count in _generation_metadata.json.

Usage:
    cd backend
    python scripts/build_interventions.py
"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.simulator.config import load_config  # noqa: E402
from app.simulator.interventions import generate_interventions  # noqa: E402


def main() -> None:
    settings = get_settings()
    cfg = load_config()
    data_dir = settings.resolve_path(settings.synthetic_data_path)
    lots = pd.read_csv(data_dir / "lots.csv")
    ground_truth = pd.read_parquet(data_dir / "ground_truth.parquet")
    table = generate_interventions(lots, ground_truth, cfg.seed, cfg.low_yield_threshold)
    table.to_parquet(data_dir / "interventions.parquet", index=False)

    meta_path = data_dir / "_generation_metadata.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta.setdefault("tables", {})["interventions"] = len(table)
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"Wrote {len(table)} synthetic interventions -> {data_dir / 'interventions.parquet'}")
    print(f"  recovered: {int(table['recovered'].sum())}/{len(table)}  |  actions used: {table['action_id'].nunique()}")
    print(table.groupby("action_id")["recovered"].agg(["sum", "count"]).sort_values("count", ascending=False).head(10).to_string())


if __name__ == "__main__":
    main()
