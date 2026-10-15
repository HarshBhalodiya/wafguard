"""
Trains the wafer-map pattern classification CNN (spec section 16, 19, 35).

Uses the procedural synthetic wafer-map generator (app.data.loaders.
synthetic_wafer_loader) since the real WM-811K file isn't present locally
yet — every sample here is DEMO / SIMULATED DATA. Swapping in the real
Kaggle file later means updating this script to source from
app.data.loaders.wm811k_loader instead; the model/training code doesn't
change.

A stratified (not chronological) split is used here — unlike the tabular
lot-yield model, these are independently generated synthetic images with
no meaningful time ordering, so spec section 11's temporal-leakage
concern doesn't apply the same way. What does apply: class imbalance is
reported honestly (spec section 19), never hidden behind overall accuracy.

Usage:
    cd backend
    .venv\\Scripts\\activate
    python scripts/train_wafer_model.py
"""
import sys
from pathlib import Path

import numpy as np
from sklearn.metrics import classification_report, confusion_matrix, f1_score
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import WAFER_PATTERN_CLASSES, get_settings  # noqa: E402
from app.data.loaders.synthetic_wafer_loader import generate_synthetic_wafer_maps  # noqa: E402
from app.data.preprocessing.wafer_preprocessor import normalize_for_cnn  # noqa: E402
from app.ml.wafer_model import WaferModelBundle, predict_batch, save_wafer_model, train_wafer_cnn  # noqa: E402

SEED = 99
N_PER_CLASS = 300
GRID_SIZE = 48
MODEL_VERSION = "1.0.0"
CLASS_TO_IDX = {name: i for i, name in enumerate(WAFER_PATTERN_CLASSES)}


def build_dataset() -> tuple[np.ndarray, np.ndarray]:
    X, y = [], []
    for sample in generate_synthetic_wafer_maps(n_per_class=N_PER_CLASS, grid_size=GRID_SIZE, seed=SEED):
        X.append(normalize_for_cnn(np.array(sample.die_grid), target_size=64))
        y.append(CLASS_TO_IDX[sample.pattern_label])
    return np.stack(X), np.array(y, dtype=np.int64)


def stratified_split(X: np.ndarray, y: np.ndarray, seed: int = SEED):
    """60/20/20 stratified split, shared with the Phase 2C comparison script
    so every architecture sees exactly the same data."""
    from sklearn.model_selection import train_test_split

    X_train, X_temp, y_train, y_temp = train_test_split(X, y, test_size=0.30, stratify=y, random_state=seed)
    X_val, X_test, y_val, y_test = train_test_split(X_temp, y_temp, test_size=0.50, stratify=y_temp, random_state=seed)
    return X_train, X_val, X_test, y_train, y_val, y_test


def main() -> None:
    settings = get_settings()

    print(f"Generating {N_PER_CLASS} synthetic wafer maps per class ({len(WAFER_PATTERN_CLASSES)} classes)...")
    X, y = build_dataset()
    print(f"Dataset: {X.shape[0]} samples, shape {X.shape[1:]}")

    X_train, X_val, X_test, y_train, y_val, y_test = stratified_split(X, y)
    print(f"train={len(X_train)} val={len(X_val)} test={len(X_test)}")

    print("Training WaferCNN...")
    model, history = train_wafer_cnn(X_train, y_train, X_val, y_val, epochs=15)

    preds, probs = predict_batch(model, X_test)
    report = classification_report(y_test, preds, target_names=WAFER_PATTERN_CLASSES, output_dict=True, zero_division=0)
    cm = confusion_matrix(y_test, preds).tolist()
    macro_f1 = f1_score(y_test, preds, average="macro", zero_division=0)

    print("\n=== Test set results ===")
    print(f"Accuracy: {report['accuracy']:.4f}  Macro-F1: {macro_f1:.4f}")
    for cls in WAFER_PATTERN_CLASSES:
        stats = report[cls]
        print(f"  {cls:10s} precision={stats['precision']:.3f} recall={stats['recall']:.3f} f1={stats['f1-score']:.3f} support={int(stats['support'])}")

    metrics = {
        "accuracy": report["accuracy"],
        "macro_f1": macro_f1,
        "per_class": {cls: report[cls] for cls in WAFER_PATTERN_CLASSES},
        "confusion_matrix": cm,
        "classes_order": WAFER_PATTERN_CLASSES,
        "training_history": history,
        "n_train": len(X_train),
        "n_val": len(X_val),
        "n_test": len(X_test),
    }

    bundle = WaferModelBundle(
        model_name="wafer_pattern_cnn",
        version=MODEL_VERSION,
        metrics=metrics,
        dataset="Procedurally generated synthetic wafer maps (DEMO / SIMULATED DATA, mimicking WM-811K taxonomy)",
    )
    artifact_path = save_wafer_model(model, bundle, settings.resolve_path(settings.model_dir))
    print(f"\nSaved -> {artifact_path}")

    # Phase 2C: export the 64-d embedding (penultimate layer) for every
    # val+test map, so similarity search never needs to re-run the CNN.
    from app.ml.vision.embeddings import extract_embeddings, save_embeddings

    X_pool = np.concatenate([X_val, X_test])
    y_pool = np.concatenate([y_val, y_test])
    emb = extract_embeddings(model, X_pool)
    save_embeddings(
        settings.resolve_path(settings.model_dir) / "wafer_embeddings.npz",
        emb,
        y_pool,
        lot_ids=[f"synthetic_map_{i:05d}" for i in range(len(y_pool))],
    )
    print(f"Saved {emb.shape} embeddings -> backend/models/artifacts/wafer_embeddings.npz")

    from app.database import mongodb  # noqa: E402
    from app.database.repositories.model_version_repository import ModelVersionRepository  # noqa: E402
    from app.utils.exceptions import DatabaseUnavailableError  # noqa: E402

    mongodb.connect()
    try:
        db = mongodb.get_database()
    except DatabaseUnavailableError as exc:
        print(f"[Mongo] {exc}\nModel Performance page will read this once MongoDB is reachable and this script is re-run.")
        return

    ModelVersionRepository(db).insert_one(
        {
            "model_name": bundle.model_name,
            "version": bundle.version,
            "task": "vision_classification",
            "dataset": bundle.dataset,
            "trained_at": bundle.trained_at,
            "features": None,
            "metrics": metrics,
        }
    )
    print("[Mongo] recorded wafer_pattern_cnn in model_versions.")


if __name__ == "__main__":
    main()
