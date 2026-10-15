"""
Wafer-map embedding extraction (Phase 2C).

The classification backbones are used as encoders: the pooled representation
just before the linear head becomes the map's *embedding*, stored per wafer
so defect-pattern similarity search ("show me lots that look like this")
works without re-running the CNN per query.

WaferCNN's 64-d global-pool output, SmallResNet's pooled stem output and
SmallViT's mean-token embedding are all extracted the same way here.
"""
import numpy as np
import torch
import torch.nn as nn


@torch.no_grad()
def extract_embeddings(model: nn.Module, X: np.ndarray, device: str = "cpu", batch_size: int = 256) -> np.ndarray:
    """
    X: (N, 3, H, W) float array. Returns (N, D) float32 embeddings — the
    pooled penultimate representation, i.e. everything except the classifier.
    """
    model = model.to(device)
    model.eval()

    outs = []
    for start in range(0, len(X), batch_size):
        xb = torch.from_numpy(X[start : start + batch_size]).float().to(device)
        logits, feats = model(xb)
        if feats.dim() == 4:  # spatial map -> global average pool
            pooled = feats.mean(dim=(2, 3))
        else:
            pooled = feats
        outs.append(pooled.cpu().numpy())
    return np.concatenate(outs, axis=0).astype(np.float32)


def save_embeddings(npz_path, embeddings: np.ndarray, labels: np.ndarray, lot_ids: list | None = None) -> None:
    payload = {"embeddings": embeddings, "labels": labels}
    if lot_ids is not None:
        payload["lot_ids"] = np.array(lot_ids, dtype=object)
    np.savez_compressed(npz_path, **payload)


def load_embeddings(npz_path) -> dict:
    with np.load(npz_path, allow_pickle=True) as z:
        return {k: z[k] for k in z.files}
