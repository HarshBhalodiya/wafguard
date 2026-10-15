"""
Measured pattern→cause association table (Phase 3, P3-1).

Replaces the authored `WAFER_PATTERN_CANDIDATE_BOOST` lookup dict with
associations *measured* from the generator's hidden labels: for each wafer
spatial pattern, what fraction of historical lots carrying that pattern
were diagnosed with each fault (mapped to engine candidates via
`fault_mapping`), with raw counts and smoothing against the global prior.

Evaluation-only module (see app/evaluation/__init__.py): it is the one
place that reads the hidden labels. The root-cause service never imports
the *builder* — it only consumes the versioned JSON artifact this module
produces.

Measurement rules (all deterministic, seed-free):
  - Chronological train split only (first `train_frac` of lots by
    process_start). The test split is never consulted, so the Phase 3
    ranking harness evaluates against genuinely unseen associations.
  - A fault that maps to k acceptable candidates contributes 1/k to each
    (fractional credit) — a multi-mapped fault cannot inflate any one
    candidate's rate.
  - "None"-pattern rows are excluded: a lot with no spatial signature
    offers no pattern evidence (the service scores 0 for it).
  - Smoothing: shrinks each raw rate toward the candidate's global train
    prior so small pattern groups don't produce 0.0/1.0 extremes.
"""
from datetime import datetime, timezone

import pandas as pd

from app.evaluation.fault_mapping import FAULT_TO_CANDIDATES, UNMAPPABLE_FAULTS

TABLE_NAME = "pattern_cause_associations"
TABLE_VERSION = "1.0.0"
DEFAULT_TRAIN_FRAC = 0.70
SMOOTHING_ALPHA = 20.0  # pseudo-lots pulling a rate toward the global prior

# Source citation embedded in the artifact so every consumer can trace it.
SOURCE_DATASET_NOTE = (
    "data/synthetic/ground_truth.parquet — the generator's hidden per-lot labels "
    "(SYNTHETIC data; stands in for historical failure-analysis records in a real fab)"
)


def build_pattern_cause_table(
    lots: pd.DataFrame,
    ground_truth: pd.DataFrame,
    train_frac: float = DEFAULT_TRAIN_FRAC,
    smoothing_alpha: float = SMOOTHING_ALPHA,
) -> dict:
    """Measure P(candidate fault | observed pattern) on the train split.

    Parameters
    ----------
    lots : DataFrame with lot_id + process_start (used only for the
        chronological split point).
    ground_truth : DataFrame with lot_id, true_fault, spatial_signature,
        process_start.
    """
    gt = ground_truth.merge(
        lots[["lot_id", "process_start"]], on="lot_id", how="left", suffixes=("", "_lot")
    )
    split_col = "process_start_lot" if "process_start_lot" in gt.columns else "process_start"
    gt[split_col] = pd.to_datetime(gt[split_col], utc=True, format="mixed")
    gt = gt.sort_values(split_col).reset_index(drop=True)
    n_train = int(len(gt) * train_frac)
    cutoff = gt[split_col].iloc[max(0, n_train - 1)]
    train = gt[gt[split_col] <= cutoff]

    train = train.copy()
    train["pattern"] = train["spatial_signature"].fillna("None")
    patterned = train[train["pattern"] != "None"]

    # Global (train-split) prior: fraction of train lots each candidate is
    # acceptable for, with fractional credit for multi-mapped faults.
    prior_hits: dict[str, float] = {c: 0.0 for c in _all_candidates()}
    for fault in train["true_fault"]:
        accept = FAULT_TO_CANDIDATES.get(fault, frozenset())
        for cand in accept:
            prior_hits[cand] += 1.0 / len(accept)
    n_train_lots = len(train)
    prior_rate = {c: (h / n_train_lots if n_train_lots else 0.0) for c, h in prior_hits.items()}

    associations: list[dict] = []
    for pattern, group in patterned.groupby("pattern"):
        pattern_lots = len(group)
        fault_counts = group["true_fault"].value_counts().to_dict()
        hits: dict[str, float] = {c: 0.0 for c in _all_candidates()}
        for fault, n in fault_counts.items():
            accept = FAULT_TO_CANDIDATES.get(fault, frozenset())
            for cand in accept:
                hits[cand] += n / len(accept)
        for cand in _all_candidates():
            h = hits[cand]
            if h <= 0.0:
                continue
            raw_rate = h / pattern_lots
            smoothed = (h + smoothing_alpha * prior_rate[cand]) / (pattern_lots + smoothing_alpha)
            associations.append(
                {
                    "pattern": str(pattern),
                    "candidate": cand,
                    "hits": round(h, 3),
                    "pattern_lots": int(pattern_lots),
                    "rate": round(raw_rate, 4),
                    "smoothed_rate": round(smoothed, 4),
                    "prior_rate": round(prior_rate[cand], 4),
                    "contributing_faults": {
                        f: int(n)
                        for f, n in fault_counts.items()
                        if cand in FAULT_TO_CANDIDATES.get(f, frozenset())
                    },
                }
            )
    associations.sort(key=lambda a: (-a["smoothed_rate"], a["pattern"], a["candidate"]))

    unmappable = {
        f: int(c) for f, c in train["true_fault"].value_counts().items() if f in UNMAPPABLE_FAULTS
    }

    return {
        "table_name": TABLE_NAME,
        "table_version": TABLE_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "dataset": SOURCE_DATASET_NOTE,
            "split": f"chronological train split (first {train_frac:.0%} of lots by process_start)",
            "train_frac": train_frac,
            "smoothing_alpha": smoothing_alpha,
            "n_train_lots": int(n_train_lots),
            "n_faulty_train": int((train["true_fault"] != "none").sum()),
            "n_patterned_train": int(len(patterned)),
            "builder": "app/evaluation/pattern_associations.py",
        },
        "associations": associations,
        "unmappable_faults_in_train": unmappable,
    }


def association_for(table: dict, pattern: str, candidate: str) -> dict | None:
    """Lookup used by the root-cause service. Returns None when the table has
    no measured association for this (pattern, candidate) pair."""
    for entry in table.get("associations", []):
        if entry["pattern"] == pattern and entry["candidate"] == candidate:
            return entry
    return None


def _all_candidates() -> set[str]:
    out: set[str] = set()
    for accept in FAULT_TO_CANDIDATES.values():
        out |= set(accept)
    return out
