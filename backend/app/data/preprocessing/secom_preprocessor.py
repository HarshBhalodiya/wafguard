"""
SECOM cleaning / feature preparation.

SECOM is small (≈1,567 rows) but wide (≈590 anonymized sensor features)
with substantial missingness and several constant/near-constant columns
— well documented in the UCI listing. This module applies the standard,
defensible cleaning steps rather than anything fab-specific (we don't
know what the anonymized features physically measure).
"""
from typing import List, Tuple

import numpy as np
import pandas as pd

MAX_MISSING_FRACTION = 0.40
MIN_VARIANCE = 1e-8


def clean_secom(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """
    Drops columns that are too sparse or constant, median-imputes the rest.
    Returns (cleaned_df, kept_feature_columns) — label/timestamp untouched.
    """
    feature_cols = [c for c in df.columns if c.startswith("feature_")]
    df = df.copy()

    missing_frac = df[feature_cols].isna().mean()
    kept = missing_frac[missing_frac <= MAX_MISSING_FRACTION].index.tolist()

    variances = df[kept].var(numeric_only=True)
    kept = variances[variances > MIN_VARIANCE].index.tolist()

    df[kept] = df[kept].fillna(df[kept].median(numeric_only=True))
    return df, kept


def build_secom_feature_matrix(df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, pd.Series, List[str]]:
    """Returns (X, y, timestamps, feature_names) ready for model training."""
    cleaned, feature_cols = clean_secom(df)
    cleaned = cleaned.dropna(subset=["timestamp"]).sort_values("timestamp")
    X = cleaned[feature_cols].to_numpy(dtype=float)
    y = cleaned["label"].to_numpy(dtype=int)
    timestamps = cleaned["timestamp"].reset_index(drop=True)
    return X, y, timestamps, feature_cols
