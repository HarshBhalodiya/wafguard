"""
Builds the measured pattern→cause association table (Phase 3, P3-1).

Reads the generator's hidden labels (evaluation-only, never used for
training) and writes a versioned JSON artifact that the root-cause
service consumes instead of the old authored `WAFER_PATTERN_CANDIDATE_BOOST`
dict. Measurements use the chronological train split only, so evaluating
ranking on the test split stays leakage-free.

Usage:
    cd backend
    python scripts/build_evidence_tables.py
    # optional: --data-dir <dir> --out <file.json>
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.evaluation.pattern_associations import (  # noqa: E402
    TABLE_VERSION,
    build_pattern_cause_table,
)

DEFAULT_OUT_NAME = "pattern_cause_associations.v1.json"


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=None, help="synthetic data dir (default: config)")
    parser.add_argument("--out", default=None, help="output JSON path (default: <model_dir>/evidence/)")
    args = parser.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else settings.resolve_path(settings.synthetic_data_path)
    gt_path = data_dir / "ground_truth.parquet"
    lots_path = data_dir / "lots.csv"
    if not gt_path.exists() or not lots_path.exists():
        raise SystemExit(f"Missing {gt_path.name}/{lots_path.name} in {data_dir} — run scripts/build_dataset.py first.")

    lots = pd.read_csv(lots_path, usecols=["lot_id", "process_start"])
    ground_truth = pd.read_parquet(gt_path)
    table = build_pattern_cause_table(lots, ground_truth)

    out_path = Path(args.out) if args.out else settings.resolve_path(settings.model_dir) / "evidence" / DEFAULT_OUT_NAME
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(table, f, indent=2)

    src = table["source"]
    print(f"[Evidence] pattern_cause_associations v{TABLE_VERSION} -> {out_path}")
    print(
        f"[Evidence] train split: {src['n_train_lots']} lots "
        f"({src['n_faulty_train']} faulty, {src['n_patterned_train']} with a spatial pattern)"
    )
    print(f"[Evidence] unmappable faults in train split: {table['unmappable_faults_in_train']}")
    print(f"[Evidence] {len(table['associations'])} measured (pattern, candidate) associations:")
    for a in table["associations"][:15]:
        print(
            f"  {a['pattern']:<11} -> {a['candidate']:<28} "
            f"rate={a['rate']:.3f} smoothed={a['smoothed_rate']:.3f} "
            f"hits={a['hits']:>6.1f}/{a['pattern_lots']} lots"
        )
    if len(table["associations"]) > 15:
        print(f"  ... {len(table['associations']) - 15} more (see the JSON artifact)")


if __name__ == "__main__":
    main()
