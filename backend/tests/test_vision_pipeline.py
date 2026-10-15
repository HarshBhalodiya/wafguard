"""
Tests for the wafer-map vision pipeline: CNN classification, Grad-CAM,
spatial analysis, and the image-upload path used by the Defect Analyzer.
"""
import io

import numpy as np
import pytest
from PIL import Image

from app.config import WAFER_PATTERN_CLASSES
from app.data.loaders.synthetic_wafer_loader import generate_synthetic_wafer_maps
from app.data.preprocessing.wafer_preprocessor import compute_spatial_stats, image_to_die_grid
from app.services import defect_service
from app.utils.exceptions import NotFoundError, UnsupportedFileError


@pytest.fixture(scope="module")
def edge_ring_die_grid():
    for sample in generate_synthetic_wafer_maps(n_per_class=1, grid_size=48, seed=123):
        if sample.pattern_label == "Edge-Ring":
            return np.array(sample.die_grid)
    raise AssertionError("generator did not yield an Edge-Ring sample")


def test_wafer_model_is_trained_and_loadable():
    bundle = defect_service.get_wafer_bundle()
    assert bundle["model"] is not None
    assert bundle["version"]


def test_classify_die_grid_returns_full_contract(edge_ring_die_grid):
    result = defect_service.classify_die_grid(edge_ring_die_grid)
    assert result["predicted_pattern"] in WAFER_PATTERN_CLASSES
    assert 0.0 <= result["confidence"] <= 1.0
    assert len(result["alternatives"]) == 3
    assert len(result["class_probabilities"]) == len(WAFER_PATTERN_CLASSES)
    probs_sum = sum(c["probability"] for c in result["class_probabilities"])
    assert abs(probs_sum - 1.0) < 0.01
    assert np.array(result["grad_cam"]).shape == (64, 64)
    assert "spatial_stats" in result and "dominant_zone" in result["spatial_stats"]


def test_classify_wafer_by_id_updates_the_document(db):
    wafer = db.wafers.find_one({"defect_density": {"$gt": 0.01}})
    assert wafer is not None, "expected at least one wafer with a materializable defect density"

    result = defect_service.classify_wafer_by_id(db, wafer["wafer_id"])
    assert result["wafer_id"] == wafer["wafer_id"]
    assert result["lot_id"] == wafer["lot_id"]

    updated = db.wafers.find_one({"wafer_id": wafer["wafer_id"]})
    assert updated["wafer_map_pattern"] == result["predicted_pattern"]
    assert updated["wafer_map_confidence"] == result["confidence"]


def test_classify_wafer_by_id_unknown_raises(db):
    with pytest.raises(NotFoundError):
        defect_service.classify_wafer_by_id(db, "WAFER_DOES_NOT_EXIST")


def test_image_to_die_grid_roundtrip_recovers_pattern(edge_ring_die_grid):
    """Renders a die grid as a grayscale 'photo' and confirms the
    upload-analysis path still recognizes the same pattern family."""
    grid = edge_ring_die_grid
    gray = np.zeros(grid.shape, dtype=np.uint8)
    gray[grid == 1] = 220  # pass -> light
    gray[grid == 2] = 30  # fail -> dark
    gray[grid == 0] = 0  # blank -> dark background too, but outside the circular mask

    recovered = image_to_die_grid(gray, grid_size=48)
    original_stats = compute_spatial_stats(grid)
    recovered_stats = compute_spatial_stats(recovered)
    # a faithful rendering should preserve which zone dominates
    assert recovered_stats.dominant_zone == original_stats.dominant_zone


def test_analyze_uploaded_image_end_to_end(edge_ring_die_grid):
    grid = edge_ring_die_grid
    rgb = np.zeros((*grid.shape, 3), dtype=np.uint8)
    rgb[grid == 1] = [220, 220, 220]
    rgb[grid == 2] = [30, 30, 30]

    buffer = io.BytesIO()
    Image.fromarray(rgb, mode="RGB").save(buffer, format="PNG")

    result = defect_service.analyze_uploaded_image(buffer.getvalue(), filename="test_wafer.png")
    assert result["analysis_type"] == "Wafer Map Pattern Analysis"
    assert result["predicted_pattern"] in WAFER_PATTERN_CLASSES
    assert "note" in result


def test_analyze_uploaded_image_rejects_garbage_bytes():
    with pytest.raises(UnsupportedFileError):
        defect_service.analyze_uploaded_image(b"this is not an image", filename="not_an_image.txt")
