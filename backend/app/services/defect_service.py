"""
Ties the vision pipeline together (spec section 13/18-20): OpenCV
preprocessing -> CNN inference -> Grad-CAM -> spatial analysis. Used both
for classifying an existing wafer already in the database and for an
ad-hoc uploaded image on the Defect Analyzer page.
"""
import io
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import numpy as np
import torch
from PIL import Image, UnidentifiedImageError

from app.config import WAFER_PATTERN_CLASSES, get_settings
from app.data.preprocessing.wafer_preprocessor import compute_spatial_stats, image_to_die_grid, normalize_for_cnn
from app.database.repositories.wafer_repository import WaferRepository
from app.ml.wafer_model import grad_cam, load_latest_wafer_model, predict_batch
from app.utils.exceptions import ModelUnavailableError, NotFoundError, UnsupportedFileError

_BUNDLE_CACHE: dict = {}
WAFER_MODEL_NAME = "wafer_pattern_cnn"
SUPPORTED_IMAGE_FORMATS = {"PNG", "JPEG", "BMP", "TIFF"}


def _model_dir() -> Path:
    settings = get_settings()
    return settings.resolve_path(settings.model_dir)


def get_wafer_bundle(force_reload: bool = False) -> dict:
    if force_reload:
        _BUNDLE_CACHE.pop(WAFER_MODEL_NAME, None)
    if WAFER_MODEL_NAME not in _BUNDLE_CACHE:
        bundle = load_latest_wafer_model(WAFER_MODEL_NAME, _model_dir())
        if bundle is None:
            raise ModelUnavailableError(
                "Wafer pattern model has not been trained yet. Run "
                "'python scripts/train_wafer_model.py' from backend/ first."
            )
        _BUNDLE_CACHE[WAFER_MODEL_NAME] = bundle
    return _BUNDLE_CACHE[WAFER_MODEL_NAME]


def classify_die_grid(die_grid: np.ndarray) -> dict:
    bundle = get_wafer_bundle()
    model = bundle["model"]

    tensor_np = normalize_for_cnn(die_grid, target_size=64)
    preds, probs = predict_batch(model, tensor_np[None, ...])
    predicted_idx = int(preds[0])
    predicted_label = WAFER_PATTERN_CLASSES[predicted_idx]

    ranked = sorted(
        (
            {"pattern": cls, "probability": round(float(p), 4)}
            for cls, p in zip(WAFER_PATTERN_CLASSES, probs[0])
        ),
        key=lambda d: d["probability"],
        reverse=True,
    )

    x_tensor = torch.from_numpy(tensor_np).float().unsqueeze(0)
    cam = grad_cam(model, x_tensor, target_class=predicted_idx)
    spatial_stats = compute_spatial_stats(die_grid)

    return {
        "predicted_pattern": predicted_label,
        "confidence": ranked[0]["probability"],
        "alternatives": ranked[1:4],
        "class_probabilities": ranked,
        "spatial_stats": asdict(spatial_stats),
        "grad_cam": np.round(cam, 4).tolist(),
        "die_grid": np.asarray(die_grid).tolist(),
        "model_name": WAFER_MODEL_NAME,
        "model_version": bundle["version"],
    }


def classify_wafer_by_id(db, wafer_id: str) -> dict:
    wafer_repo = WaferRepository(db)
    wafer = wafer_repo.get_by_wafer_id(wafer_id)
    if not wafer:
        raise NotFoundError(f"Wafer {wafer_id} was not found.")
    if not wafer.get("die_grid"):
        raise NotFoundError(f"Wafer {wafer_id} has no stored wafer map to classify.")

    result = classify_die_grid(np.array(wafer["die_grid"]))
    wafer_repo.update_one(
        {"wafer_id": wafer_id},
        {"wafer_map_pattern": result["predicted_pattern"], "wafer_map_confidence": result["confidence"]},
    )
    result.update(
        {
            "wafer_id": wafer_id,
            "lot_id": wafer["lot_id"],
            "data_source": wafer.get("wafer_map_source", "wm811k_synthetic"),
        }
    )
    return result


def analyze_uploaded_image(image_bytes: bytes, filename: Optional[str] = None) -> dict:
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            if img.format not in SUPPORTED_IMAGE_FORMATS:
                raise UnsupportedFileError(
                    f"Unsupported image format '{img.format}'. Supported: {sorted(SUPPORTED_IMAGE_FORMATS)}."
                )
            array = np.array(img.convert("RGB"))
    except UnidentifiedImageError as exc:
        raise UnsupportedFileError(f"Could not read '{filename or 'uploaded file'}' as an image.") from exc

    die_grid = image_to_die_grid(array, grid_size=64)
    result = classify_die_grid(die_grid)
    result.update(
        {
            "analysis_type": "Wafer Map Pattern Analysis",
            "note": (
                "Classification is derived from the uploaded image via grayscale + circular-mask + "
                "Otsu thresholding, not a fab-specific imaging standard. This is wafer-map pattern "
                "analysis, not microscope defect-image analysis (spec section 18)."
            ),
            "data_source": "wm811k_synthetic",
        }
    )
    return result
