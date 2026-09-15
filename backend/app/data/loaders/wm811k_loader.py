"""
Loader for the WM-811K wafer-map dataset (Kaggle: qingyi/wm811k-wafer-map).

The public .pkl distribution is a pandas DataFrame pickle with (at least)
these columns: waferMap, dieSize, lotName, waferIndex, trianTestLabel,
failureType. Both trianTestLabel and failureType are typically stored as
nested single-element arrays (or empty arrays when unlabeled) rather than
plain strings — this loader unwraps that.

If the real file isn't present at WM811K_DATA_PATH, callers should fall
back to `app.data.loaders.synthetic_wafer_loader` and clearly label the
result as DEMO / SIMULATED DATA (spec section 4-5) — this loader itself
just raises so that fallback decision stays explicit at the call site.
"""
from pathlib import Path
from typing import Iterator

import numpy as np
import pandas as pd

from app.config import WAFER_PATTERN_CLASSES
from app.utils.exceptions import DatasetUnavailableError

PICKLE_FILENAME = "LSWMD.pkl"


def is_available(wm811k_dir: Path) -> bool:
    return (Path(wm811k_dir) / PICKLE_FILENAME).exists()


def _missing_dataset_message(path: Path) -> str:
    return (
        f"WM-811K dataset not found at '{path / PICKLE_FILENAME}'. Download it from "
        "https://www.kaggle.com/datasets/qingyi/wm811k-wafer-map (requires a free "
        f"Kaggle account) and place '{PICKLE_FILENAME}' in that folder. Until then, "
        "the wafer-vision pipeline runs on procedurally generated synthetic wafer "
        "maps labeled DEMO / SIMULATED DATA."
    )


def _unwrap_nested_label(value) -> str:
    """WM-811K stores labels as nested arrays, e.g. array([['Edge-Ring']], dtype=object)."""
    arr = np.array(value, dtype=object)
    flat = arr.flatten()
    if flat.size == 0:
        return "None"
    label = str(flat[0]).strip()
    return label if label else "None"


def load_wm811k_raw(wm811k_dir: Path) -> pd.DataFrame:
    wm811k_dir = Path(wm811k_dir)
    if not is_available(wm811k_dir):
        raise DatasetUnavailableError(_missing_dataset_message(wm811k_dir))
    df = pd.read_pickle(wm811k_dir / PICKLE_FILENAME)
    return df


def iter_labeled_wafer_maps(wm811k_dir: Path, limit: int | None = None) -> Iterator[dict]:
    """
    Yields {wafer_id, die_grid (list[list[int]], 0=blank/edge 1=pass 2=fail),
    pattern_label, data_source: "wm811k"} for rows that carry one of the
    known WM-811K pattern labels (skips unlabeled rows).
    """
    df = load_wm811k_raw(wm811k_dir)
    count = 0
    for idx, row in df.iterrows():
        failure_type = row.get("failureType")
        label = _unwrap_nested_label(failure_type) if failure_type is not None else "None"
        if label not in WAFER_PATTERN_CLASSES:
            continue
        wafer_map = np.array(row["waferMap"])
        lot_name = row.get("lotName", f"WM811K_LOT_{idx}")
        wafer_index = row.get("waferIndex", idx)
        yield {
            "wafer_id": f"{lot_name}_{wafer_index}",
            "die_grid": wafer_map.astype(int).tolist(),
            "pattern_label": label,
            "data_source": "wm811k",
        }
        count += 1
        if limit and count >= limit:
            return
