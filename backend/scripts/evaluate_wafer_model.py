"""
Standalone re-evaluation of the saved wafer-pattern CNN (spec section 37),
using the exact same seeded generation + split as training so results are
reproducible from the artifact alone. Also sanity-checks that Grad-CAM
produces a same-shape heatmap for a sample prediction.

Usage:
    cd backend
    .venv\\Scripts\\activate
    python scripts/evaluate_wafer_model.py
"""
import sys
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import classification_report, confusion_matrix, f1_score
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import WAFER_PATTERN_CLASSES, get_settings  # noqa: E402
from app.ml.wafer_model import grad_cam, load_latest_wafer_model, predict_batch  # noqa: E402
from train_wafer_model import GRID_SIZE, N_PER_CLASS, SEED, build_dataset  # noqa: E402


def main() -> None:
    settings = get_settings()
    model_dir = settings.resolve_path(settings.model_dir)

    bundle = load_latest_wafer_model("wafer_pattern_cnn", model_dir)
    if bundle is None:
        print("No trained wafer model found — run scripts/train_wafer_model.py first.")
        return
    model = bundle["model"]

    print(f"Regenerating the same seeded dataset ({N_PER_CLASS}/class, grid={GRID_SIZE}, seed={SEED})...")
    X, y = build_dataset()
    _, X_temp, _, y_temp = train_test_split(X, y, test_size=0.30, stratify=y, random_state=SEED)
    _, X_test, _, y_test = train_test_split(X_temp, y_temp, test_size=0.50, stratify=y_temp, random_state=SEED)

    preds, probs = predict_batch(model, X_test)
    report = classification_report(y_test, preds, target_names=WAFER_PATTERN_CLASSES, zero_division=0)
    macro_f1 = f1_score(y_test, preds, average="macro", zero_division=0)
    cm = confusion_matrix(y_test, preds)

    print(f"\n=== Re-evaluated wafer_pattern_cnn v{bundle['version']} ===")
    print(report)
    print(f"Macro-F1: {macro_f1:.4f}")
    print("Confusion matrix (rows=true, cols=pred):")
    print(cm)

    print("\nGrad-CAM sanity check on one test sample...")
    sample = torch.from_numpy(X_test[:1]).float()
    cam = grad_cam(model, sample)
    print(f"  input shape={tuple(sample.shape)} -> CAM shape={cam.shape}, range=[{cam.min():.3f}, {cam.max():.3f}]")
    assert cam.shape == (X_test.shape[-2], X_test.shape[-1]), "Grad-CAM output should match the input's spatial size"
    print("  OK")


if __name__ == "__main__":
    main()
