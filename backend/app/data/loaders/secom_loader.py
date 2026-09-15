"""
Loader for the UCI SECOM semiconductor manufacturing dataset.

Expected files (produced by `scripts/download_secom.py`, or placed
manually) inside `SECOM_DATA_PATH`:
    secom.data          - whitespace-separated matrix, 1 row per example,
                          1 column per sensor feature, "NaN" for missing.
    secom_labels.data   - two whitespace-separated columns per row:
                          label (-1 = pass, 1 = fail) and a timestamp
                          like "19/07/2008 11:55:00".

Source: https://www.kaggle.com/datasets/paresh2047/uci-semcom (mirrors
the original UCI ML Repository dataset at
https://archive.ics.uci.edu/dataset/179/secom). This is a real, public
semiconductor manufacturing dataset used here for prototype modeling —
it does not represent any specific modern fab or node.
"""
from pathlib import Path

import pandas as pd

from app.utils.exceptions import DatasetUnavailableError

DATA_FILENAME = "secom.data"
LABELS_FILENAME = "secom_labels.data"


def _missing_dataset_message(path: Path) -> str:
    return (
        f"SECOM dataset not found at '{path}'. Run "
        "'python scripts/download_secom.py' from backend/ to fetch it "
        f"automatically from UCI, or manually place '{DATA_FILENAME}' and "
        f"'{LABELS_FILENAME}' in that folder. "
        "Source: https://www.kaggle.com/datasets/paresh2047/uci-semcom"
    )


def is_available(secom_dir: Path) -> bool:
    return (secom_dir / DATA_FILENAME).exists() and (secom_dir / LABELS_FILENAME).exists()


def load_secom(secom_dir: Path) -> pd.DataFrame:
    """
    Returns a DataFrame with columns: feature_0..feature_{n-1}, label
    (0 = pass, 1 = fail — remapped from SECOM's -1/1), timestamp.
    Raises DatasetUnavailableError if the files aren't present.
    """
    secom_dir = Path(secom_dir)
    if not is_available(secom_dir):
        raise DatasetUnavailableError(_missing_dataset_message(secom_dir))

    features = pd.read_csv(secom_dir / DATA_FILENAME, sep=r"\s+", header=None, na_values=["NaN"])
    features.columns = [f"feature_{i}" for i in range(features.shape[1])]

    # The distributed file quotes the timestamp, e.g. -1 "19/07/2008 11:55:00".
    # pandas' default quote handling already merges that into a single
    # "label_raw, datetime_str" pair (the embedded space doesn't split it).
    labels_raw = pd.read_csv(
        secom_dir / LABELS_FILENAME,
        sep=r"\s+",
        header=None,
        names=["label_raw", "datetime_str"],
        quotechar='"',
    )
    labels = pd.DataFrame(
        {
            # SECOM: -1 = pass, 1 = fail. Remap to a conventional 0/1 "is_fail" target.
            "label": (labels_raw["label_raw"] == 1).astype(int),
            "timestamp": pd.to_datetime(
                labels_raw["datetime_str"], format="%d/%m/%Y %H:%M:%S", errors="coerce"
            ),
        }
    )

    if len(features) != len(labels):
        raise DatasetUnavailableError(
            f"SECOM data/labels row-count mismatch ({len(features)} vs {len(labels)}). "
            "The dataset files may be corrupted — try re-running scripts/download_secom.py."
        )

    combined = pd.concat([features, labels], axis=1)
    return combined
