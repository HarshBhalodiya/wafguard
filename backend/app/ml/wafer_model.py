"""
Compact PyTorch CNN for wafer-map pattern classification (spec section 16).

Deliberately small (~40K parameters, 3 conv blocks) — spec explicitly asks
for a compact network, not an unnecessarily huge one, and the input is a
64x64 categorical die-status grid, not a rich photograph, so there's
little to gain from a deeper backbone.

Persistence mirrors app.ml.yield_model's pattern (versioned artifact +
metadata.json + a `.latest.json` pointer) so every trained model in the
project — tabular or vision — is discoverable the same way.
"""
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset

from app.config import WAFER_PATTERN_CLASSES

INPUT_SIZE = 64
NUM_CLASSES = len(WAFER_PATTERN_CLASSES)


class WaferCNN(nn.Module):
    def __init__(self, num_classes: int = NUM_CLASSES):
        super().__init__()
        self.block1 = nn.Sequential(nn.Conv2d(3, 16, 3, padding=1), nn.BatchNorm2d(16), nn.ReLU(), nn.MaxPool2d(2))
        self.block2 = nn.Sequential(nn.Conv2d(16, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(), nn.MaxPool2d(2))
        # Kept separate (not pooled yet) so Grad-CAM has a spatial feature map to hook.
        self.block3_conv = nn.Sequential(nn.Conv2d(32, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU())
        self.pool3 = nn.MaxPool2d(2)
        self.global_pool = nn.AdaptiveAvgPool2d(1)
        self.dropout = nn.Dropout(0.2)
        self.fc = nn.Linear(64, num_classes)

    def forward(self, x: torch.Tensor):
        x = self.block1(x)
        x = self.block2(x)
        features = self.block3_conv(x)  # (B, 64, 8, 8) for a 64x64 input
        x = self.pool3(features)
        x = self.global_pool(x)
        x = torch.flatten(x, 1)
        x = self.dropout(x)
        logits = self.fc(x)
        return logits, features


class WaferMapTensorDataset(Dataset):
    """Wraps pre-processed (N, 3, H, W) float arrays + integer labels."""

    def __init__(self, X: np.ndarray, y: np.ndarray):
        self.X = torch.from_numpy(X).float()
        self.y = torch.from_numpy(y).long()

    def __len__(self):
        return len(self.y)

    def __getitem__(self, idx):
        return self.X[idx], self.y[idx]


def train_wafer_cnn(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    epochs: int = 15,
    batch_size: int = 32,
    lr: float = 1e-3,
    device: str = "cpu",
    model: Optional[nn.Module] = None,
) -> tuple[nn.Module, dict]:
    """model: pass any backbone with the WaferCNN forward contract (logits, features);
    defaults to the production WaferCNN. Used by the Phase 2C architecture comparison."""
    if model is None:
        model = WaferCNN()
    model = model.to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.CrossEntropyLoss()

    train_loader = DataLoader(WaferMapTensorDataset(X_train, y_train), batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(WaferMapTensorDataset(X_val, y_val), batch_size=batch_size, shuffle=False)

    history = {"train_loss": [], "val_loss": [], "val_accuracy": []}
    for epoch in range(epochs):
        model.train()
        running_loss = 0.0
        for xb, yb in train_loader:
            xb, yb = xb.to(device), yb.to(device)
            optimizer.zero_grad()
            logits, _ = model(xb)
            loss = criterion(logits, yb)
            loss.backward()
            optimizer.step()
            running_loss += loss.item() * xb.size(0)
        train_loss = running_loss / len(train_loader.dataset)

        model.eval()
        val_loss, correct = 0.0, 0
        with torch.no_grad():
            for xb, yb in val_loader:
                xb, yb = xb.to(device), yb.to(device)
                logits, _ = model(xb)
                loss = criterion(logits, yb)
                val_loss += loss.item() * xb.size(0)
                correct += (logits.argmax(dim=1) == yb).sum().item()
        val_loss /= len(val_loader.dataset)
        val_accuracy = correct / len(val_loader.dataset)

        history["train_loss"].append(round(train_loss, 4))
        history["val_loss"].append(round(val_loss, 4))
        history["val_accuracy"].append(round(val_accuracy, 4))
        print(f"  epoch {epoch + 1:2d}/{epochs} | train_loss={train_loss:.4f} val_loss={val_loss:.4f} val_acc={val_accuracy:.4f}")

    return model, history


@torch.no_grad()
def predict_batch(model: WaferCNN, X: np.ndarray, device: str = "cpu") -> tuple[np.ndarray, np.ndarray]:
    """Returns (predicted_class_indices, class_probabilities)."""
    model.eval()
    x = torch.from_numpy(X).float().to(device)
    logits, _ = model(x)
    probs = F.softmax(logits, dim=1).cpu().numpy()
    preds = probs.argmax(axis=1)
    return preds, probs


def grad_cam(model: WaferCNN, x: torch.Tensor, target_class: Optional[int] = None) -> np.ndarray:
    """
    Manual gradient-weighted class activation map (no external library):
    weight the last conv block's feature maps by the gradient of the target
    class's logit with respect to them, then ReLU + normalize to [0, 1].
    `x` must be a single-sample batch, shape (1, 3, H, W).
    """
    model.eval()
    logits, features = model(x)
    if target_class is None:
        target_class = int(logits.argmax(dim=1).item())
    features.retain_grad()
    model.zero_grad()
    logits[0, target_class].backward()

    gradients = features.grad[0]  # (C, h, w)
    weights = gradients.mean(dim=(1, 2))  # (C,)
    cam = F.relu((weights[:, None, None] * features[0]).sum(0))  # (h, w)
    cam = cam / (cam.max() + 1e-8)
    cam_upsampled = F.interpolate(cam[None, None], size=x.shape[-2:], mode="bilinear", align_corners=False)
    return cam_upsampled[0, 0].detach().cpu().numpy()


# ---------------------------------------------------------------------------
# Persistence — mirrors app.ml.yield_model's versioning scheme.
# ---------------------------------------------------------------------------
@dataclass
class WaferModelBundle:
    model_name: str
    version: str
    metrics: dict = field(default_factory=dict)
    dataset: str = ""
    classes: list = field(default_factory=lambda: list(WAFER_PATTERN_CLASSES))
    trained_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def save_wafer_model(model: WaferCNN, bundle: WaferModelBundle, model_dir: Path) -> Path:
    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = model_dir / f"{bundle.model_name}_v{bundle.version}.pt"
    torch.save(model.state_dict(), artifact_path)

    metadata = {
        "model_name": bundle.model_name,
        "version": bundle.version,
        "task": "vision_classification",
        "dataset": bundle.dataset,
        "trained_at": bundle.trained_at,
        "classes": bundle.classes,
        "metrics": bundle.metrics,
        "artifact_file": artifact_path.name,
    }
    with open(model_dir / f"{bundle.model_name}_v{bundle.version}.metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)
    with open(model_dir / f"{bundle.model_name}.latest.json", "w", encoding="utf-8") as f:
        json.dump({"version": bundle.version, "artifact_file": artifact_path.name}, f, indent=2)

    return artifact_path


def load_latest_wafer_model(model_name: str, model_dir: Path, device: str = "cpu") -> Optional[dict]:
    model_dir = Path(model_dir)
    pointer_path = model_dir / f"{model_name}.latest.json"
    if not pointer_path.exists():
        return None
    with open(pointer_path, encoding="utf-8") as f:
        pointer = json.load(f)
    artifact_path = model_dir / pointer["artifact_file"]
    if not artifact_path.exists():
        return None

    model = WaferCNN()
    model.load_state_dict(torch.load(artifact_path, map_location=device))
    model.eval()

    metadata_path = model_dir / f"{model_name}_v{pointer['version']}.metadata.json"
    metadata = {}
    if metadata_path.exists():
        with open(metadata_path, encoding="utf-8") as f:
            metadata = json.load(f)

    return {"model": model, "version": pointer["version"], "metadata": metadata}
