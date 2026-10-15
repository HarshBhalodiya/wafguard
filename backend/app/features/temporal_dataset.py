"""
Temporal dataset builder for sequence models (Phase 2B).

Turns the v2 sensor telemetry (long format: lot_id / parameter / ts_index /
value) into fixed-shape tensors for GRU / Transformer training:

  X:    (N, T, C) float32 — per-channel value series, padded with 0
  mask: (N, T, C) float32 — 1.0 where a real reading exists, 0.0 where the
        channel was missing (e.g. the simulator's sensor_dropout fault).

The mask is the point: a dropped sensor reading must not be silently
treated as "value = 0" — that would look like an extreme excursion to any
model. The GRU / Transformer modules zero masked inputs AND use the mask
for pooling so missing data is *absence*, not signal.

Normalization is fit on the TRAIN split only (per-channel mean/std) and
applied to val/test, to avoid leaking future-split statistics backwards.
"""
import numpy as np
import pandas as pd

TEMPORAL_PARAMS = ["temperature", "rf_power", "pressure", "gas_flow"]


def build_temporal_sequences(
    sensor_df: pd.DataFrame,
    lot_ids: list,
    max_len: int = 150,
    params: list | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Returns (X, mask) with shapes (N, T, C) for the given lot order.

    Lots missing a channel entirely get an all-zero column with mask 0 —
    the model learns 'channel absent' rather than inventing a value.
    """
    params = list(params or TEMPORAL_PARAMS)
    param_to_idx = {p: i for i, p in enumerate(params)}

    n = len(lot_ids)
    X = np.zeros((n, max_len, len(params)), dtype=np.float32)
    mask = np.zeros((n, max_len, len(params)), dtype=np.float32)

    sensor_df = sensor_df[sensor_df.parameter.isin(params)]
    # The simulator encodes a dropped reading as value=NaN (sensor_dropout
    # fault). A NaN is absence, not a value — exclude it so the mask marks
    # that timestep/channel missing instead of poisoning the tensors.
    sensor_df = sensor_df[sensor_df["value"].notna()]
    ts_col = "ts_index" if "ts_index" in sensor_df.columns else "reading_index"

    # Long -> per (lot, param) ordered value list; vectorized via pivot where possible.
    grouped = sensor_df.groupby(["lot_id", "parameter"])
    lot_pos = {lot_id: i for i, lot_id in enumerate(lot_ids)}
    for (lot_id, param), group in grouped:
        pos = lot_pos.get(lot_id)
        if pos is None:
            continue
        c = param_to_idx[param]
        values = group.sort_values(ts_col)["value"].to_numpy(dtype=np.float32)[:max_len]
        t = len(values)
        X[pos, :t, c] = values
        mask[pos, :t, c] = 1.0
    return X, mask


def fit_scaler(X: np.ndarray, mask: np.ndarray) -> dict:
    """Per-channel mean/std over REAL readings only (mask==1). Train split only."""
    means, stds = [], []
    for c in range(X.shape[2]):
        values = X[:, :, c][mask[:, :, c] == 1.0]
        if len(values) == 0:
            means.append(0.0)
            stds.append(1.0)
            continue
        mean = float(values.mean())
        std = float(values.std())
        if std < 1e-8:
            std = 1.0
        means.append(mean)
        stds.append(std)
    return {"mean": np.array(means, dtype=np.float32), "std": np.array(stds, dtype=np.float32)}


def apply_scaler(X: np.ndarray, mask: np.ndarray, scaler: dict) -> np.ndarray:
    """Standardizes X per channel; masked (missing) entries stay exactly 0."""
    Xn = (X - scaler["mean"][None, None, :]) / scaler["std"][None, None, :]
    return (Xn * mask).astype(np.float32)


def chronological_sequence_split(
    lot_ids: list,
    train_frac: float = 0.70,
    val_frac: float = 0.15,
) -> tuple[list, list, list]:
    """Index-based chronological split — lot_ids must already be in process_start order."""
    n = len(lot_ids)
    train_end = int(n * train_frac)
    val_end = int(n * (train_frac + val_frac))
    return lot_ids[:train_end], lot_ids[train_end:val_end], lot_ids[val_end:]
