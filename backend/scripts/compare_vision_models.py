"""
Phase 2C: wafer-map vision architecture comparison.

Trains WaferCNN (Phase 1 production), SmallResNet and SmallViT on the
IDENTICAL procedural synthetic dataset + stratified split (reusing
scripts/train_wafer_model.build_dataset so the comparison is apples-to-
apples), and reports per-class precision/recall/F1 + confusion matrices
for each.

Outputs:
  - reports/vision_comparison.md (+ confusion-matrix PNGs)
  - models/artifacts/wafer_embeddings.npz — embeddings from the BEST
    architecture for val+test maps, for similarity search

SYNTHETIC-ONLY WARNING: all maps are procedurally generated mimicking the
WM-811K taxonomy. The per-class numbers describe the generator's pattern
physics, NOT real fab data. No claim about real-world performance may be
made from this report.

Note: the production artifact `wafer_pattern_cnn` remains a WaferCNN unless
a comparison architecture wins by a clear margin — the runtime loader
(defect_service) constructs a WaferCNN, so swapping architectures requires
a loader change, recorded in docs/DECISIONS.md if it happens.

Usage:
    cd backend
    .venv/Scripts/python.exe scripts/compare_vision_models.py
"""
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import torch
from sklearn.metrics import classification_report, confusion_matrix, f1_score

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from app.config import WAFER_PATTERN_CLASSES, get_settings  # noqa: E402
from app.ml.vision.architectures import SmallResNet, SmallViT  # noqa: E402
from app.ml.vision.embeddings import extract_embeddings, save_embeddings  # noqa: E402
from app.ml.wafer_model import WaferCNN, predict_batch, train_wafer_cnn  # noqa: E402

SEED = 99
EPOCHS = 15
REPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "reports"


def per_class_recall_f1(y_true, y_pred, classes) -> dict:
    report = classification_report(y_true, y_pred, target_names=classes, output_dict=True, zero_division=0)
    return {
        cls: {
            "precision": report[cls]["precision"],
            "recall": report[cls]["recall"],
            "f1": report[cls]["f1-score"],
            "support": int(report[cls]["support"]),
        }
        for cls in classes
    }


def plot_confusion(cm, classes, title, out_path):
    fig, ax = plt.subplots(figsize=(7, 6))
    im = ax.imshow(cm, cmap="Blues")
    ax.set_xticks(range(len(classes)), labels=classes, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(len(classes)), labels=classes, fontsize=8)
    for (i, j), v in np.ndenumerate(cm):
        ax.text(j, i, str(v), ha="center", va="center", fontsize=7)
    ax.set_title(title)
    fig.colorbar(im, fraction=0.046)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main() -> None:
    torch.manual_seed(SEED)
    np.random.seed(SEED)
    settings = get_settings()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    model_dir = settings.resolve_path(settings.model_dir)

    import train_wafer_model as twm

    X, y = twm.build_dataset()
    X_train, X_val, X_test, y_train, y_val, y_test = twm.stratified_split(X, y)
    print(f"[Vision] dataset={X.shape} train={len(X_train)} val={len(X_val)} test={len(X_test)}")
    # --- WaferCNN uses its own trainer (returns model + history) ---
    print("[Vision] training WaferCNN...")
    cnn, cnn_hist = train_wafer_cnn(X_train, y_train, X_val, y_val, epochs=EPOCHS)

    print("[Vision] training SmallResNet...")
    resnet = SmallResNet(num_classes=len(WAFER_PATTERN_CLASSES))
    resnet, res_hist = train_wafer_cnn(X_train, y_train, X_val, y_val, epochs=EPOCHS, model=resnet)

    print("[Vision] training SmallViT...")
    vit = SmallViT(num_classes=len(WAFER_PATTERN_CLASSES))
    vit, vit_hist = train_wafer_cnn(X_train, y_train, X_val, y_val, epochs=EPOCHS, model=vit)

    models = {"WaferCNN": cnn, "SmallResNet": resnet, "SmallViT": vit}
    histories = {"WaferCNN": cnn_hist, "SmallResNet": res_hist, "SmallViT": vit_hist}

    results = {}
    for name, model in models.items():
        preds, _ = predict_batch(model, X_test)
        results[name] = {
            "accuracy": float((preds == y_test).mean()),
            "macro_f1": float(f1_score(y_test, preds, average="macro", zero_division=0)),
            "per_class": per_class_recall_f1(y_test, preds, WAFER_PATTERN_CLASSES),
            "confusion_matrix": confusion_matrix(y_test, preds).tolist(),
            "best_val_epoch": int(np.argmax(histories[name]["val_accuracy"])),
            "final_val_accuracy": histories[name]["val_accuracy"][-1],
        }
        plot_confusion(
            np.array(results[name]["confusion_matrix"]),
            WAFER_PATTERN_CLASSES,
            f"{name} — test confusion matrix (SYNTHETIC DATA)",
            REPORTS_DIR / f"confusion_matrix_{name.lower()}.png",
        )
        print(f"  {name:12s} acc={results[name]['accuracy']:.4f} macro_f1={results[name]['macro_f1']:.4f}")

    best_name = max(results, key=lambda k: results[k]["macro_f1"])
    best_model = models[best_name]
    print(f"[Vision] best macro-F1: {best_name} ({results[best_name]['macro_f1']:.4f})")

    # Embeddings from the best encoder for val+test maps (similarity search).
    X_pool = np.concatenate([X_val, X_test])
    y_pool = np.concatenate([y_val, y_test])
    emb = extract_embeddings(best_model, X_pool)
    save_embeddings(
        model_dir / "wafer_embeddings.npz",
        emb,
        y_pool,
        lot_ids=[f"synthetic_map_{i:05d}" for i in range(len(y_pool))],
    )
    print(f"[Vision] saved {emb.shape} embeddings -> {model_dir / 'wafer_embeddings.npz'}")

    # ---------------- Report ----------------
    lines = [
        "# Wafer-map vision model comparison (Phase 2C)",
        "",
        "> **SYNTHETIC-ONLY DATA.** Every wafer map here is procedurally generated "
        "(`app.data.loaders.synthetic_wafer_loader`, WM-811K-like taxonomy). These numbers describe "
        "the generator's pattern physics — they say nothing about performance on a real fab's maps.",
        "",
        f"Identical procedural dataset + stratified split for all models (train={len(X_train)} / "
        f"val={len(X_val)} / test={len(X_test)}, {len(WAFER_PATTERN_CLASSES)} classes, seed={SEED}).",
        "",
        "## Test-split results",
        "",
        "| Model | Accuracy | Macro-F1 | Best val epoch |",
        "|---|---|---|---|",
    ]
    for name in ("WaferCNN", "SmallResNet", "SmallViT"):
        r = results[name]
        lines.append(f"| {name} | {r['accuracy']:.4f} | {r['macro_f1']:.4f} | {r['best_val_epoch']} |")
    lines += ["", "## Per-class recall / F1 — test split", ""]
    for name in ("WaferCNN", "SmallResNet", "SmallViT"):
        lines += [f"### {name}", "", "| Class | Precision | Recall | F1 | Support |", "|---|---|---|---|---|"]
        for cls, s in results[name]["per_class"].items():
            lines.append(f"| {cls} | {s['precision']:.3f} | {s['recall']:.3f} | {s['f1']:.3f} | {s['support']} |")
        lines.append("")
    lines += [
        "Confusion matrices: [WaferCNN](confusion_matrix_wafercnn.png) · "
        "[SmallResNet](confusion_matrix_smallresnet.png) · [SmallViT](confusion_matrix_smallvit.png)",
        "",
        f"**Best on macro-F1: {best_name}.** The shipped production artifact stays `wafer_pattern_cnn` "
        "(the runtime loader constructs a WaferCNN); switching architectures would require a loader "
        "change and is logged as a decision, not done silently.",
        "",
        "Embeddings from the best encoder for all val+test maps: "
        "`backend/models/artifacts/wafer_embeddings.npz` (embeddings + labels + ids), "
        "consumable by lot-similarity search without re-running the backbone per query.",
    ]
    (REPORTS_DIR / "vision_comparison.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"[Report] wrote {REPORTS_DIR / 'vision_comparison.md'}")


if __name__ == "__main__":
    main()
