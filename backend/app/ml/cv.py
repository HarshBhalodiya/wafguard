"""
Time-series cross-validation for chronological manufacturing data (Phase 2A).

sklearn's KFold/StratifiedKFold shuffle, which is wrong for time-ordered
production lots: a random fold trains on lots that were produced *after*
the lots it validates on. This module provides the correct primitive —
expanding-window folds where every training index strictly precedes every
validation index — plus an explicit leakage assertion that callers (and
tests) can use to prove a split is order-safe.
"""
from typing import NamedTuple

import numpy as np
import pandas as pd


class TemporalLeakageError(ValueError):
    """Raised when a supposedly chronological split lets training data bleed past validation data."""


class Fold(NamedTuple):
    fold: int
    train_idx: np.ndarray
    val_idx: np.ndarray


def expanding_window_folds(
    n_samples: int,
    n_folds: int = 5,
    min_train_frac: float = 0.50,
) -> list[Fold]:
    """
    Expanding-window CV over a chronologically sorted range [0, n_samples).

    Fold i trains on [0, end_i) and validates on [end_i, end_i + gap_i).
    The training window only grows, mirroring how a deployed model is
    retrained as new production data arrives.
    """
    if n_folds < 2:
        raise ValueError("need at least 2 folds")
    min_train = int(n_samples * min_train_frac)
    remaining = n_samples - min_train
    step = remaining // n_folds
    if step < 1:
        raise ValueError(f"{n_samples} samples too few for {n_folds} folds")

    folds = []
    start = min_train
    for i in range(n_folds):
        end = start + step if i < n_folds - 1 else n_samples
        if end >= n_samples and i < n_folds - 1:
            break
        val_end = end
        train_idx = np.arange(0, start)
        val_idx = np.arange(start, val_end)
        folds.append(Fold(fold=i, train_idx=train_idx, val_idx=val_idx))
        start = val_end
    return folds


def assert_temporal_order(train_df: pd.DataFrame, val_df: pd.DataFrame, time_col: str = "process_start") -> None:
    """
    Raises TemporalLeakageError if any training row's timestamp is at or
    after any validation row's timestamp (the definition of leakage for
    time-ordered data). Both frames must carry `time_col`.
    """
    if time_col not in train_df.columns or time_col not in val_df.columns:
        raise ValueError(f"both frames must contain '{time_col}'")
    train_max = pd.to_datetime(train_df[time_col]).max()
    val_min = pd.to_datetime(val_df[time_col]).min()
    if train_max is not None and val_min is not None and train_max >= val_min:
        raise TemporalLeakageError(
            f"temporal leakage: latest training timestamp {train_max} is not before "
            f"earliest validation timestamp {val_min}"
        )


def check_split_leakage_free(features_df: pd.DataFrame, time_col: str = "process_start") -> dict:
    """
    Convenience check for the canonical train/val/test chronological split:
    verifies train < val < test in time and reports the boundary timestamps.
    Used by tests and by the comparison scripts as a run-time guard.
    """
    from app.ml.preprocessing import chronological_split

    train_df, val_df, test_df = chronological_split(features_df)
    assert_temporal_order(train_df, val_df, time_col)
    assert_temporal_order(pd.concat([train_df, val_df]), test_df, time_col)
    return {
        "train_end": str(pd.to_datetime(train_df[time_col]).max()),
        "val_start": str(pd.to_datetime(val_df[time_col]).min()),
        "val_end": str(pd.to_datetime(val_df[time_col]).max()),
        "test_start": str(pd.to_datetime(test_df[time_col]).min()),
        "leakage_free": True,
    }
