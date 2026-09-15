"""
OpenCV-based wafer die-grid preprocessing (spec section 13/16/20).

A WM-811K "wafer map" is a categorical die-status grid (blank / pass /
fail per die), not a photographic image — so "preprocessing" here means
resizing without blending categories, denoising the fail mask, and
extracting the spatial statistics the root-cause engine and the wafer
map UI need. Segmentation uses connected-component labeling on the fail
mask to find defect clusters.
"""
from dataclasses import dataclass, field
from typing import List

import cv2
import numpy as np

BLANK, PASS, FAIL = 0, 1, 2


def image_to_die_grid(image: np.ndarray, grid_size: int = 64) -> np.ndarray:
    """
    Converts an arbitrary uploaded image into our categorical die-grid
    encoding (0=blank, 1=pass, 2=fail): grayscale -> resize -> circular
    wafer mask -> Otsu threshold (darker-than-threshold, inside the mask,
    counts as a failed die).

    This is a heuristic for user-uploaded images of unknown origin and
    color convention — it assumes a roughly circular wafer map centered
    in the frame, not a specific fab's imaging standard. Spec section 18:
    this powers "Wafer Map Pattern Analysis", not a claim of microscope
    defect-image analysis.
    """
    if image.ndim == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY if image.shape[-1] == 3 else cv2.COLOR_RGBA2GRAY)
    else:
        gray = image
    resized = cv2.resize(gray, (grid_size, grid_size), interpolation=cv2.INTER_AREA).astype(np.uint8)

    cy, cx = (grid_size - 1) / 2, (grid_size - 1) / 2
    radius = grid_size / 2 * 0.94
    yy, xx = np.mgrid[0:grid_size, 0:grid_size]
    inside = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2) <= radius

    grid = np.where(inside, PASS, BLANK).astype(np.uint8)
    if inside.sum() > 0 and resized[inside].std() > 1e-6:
        # Use cv2's own binary output rather than re-comparing against the
        # returned threshold value: THRESH_BINARY maps x -> 0 for x <= thresh
        # and x -> 255 otherwise, so redoing it as `resized < threshold` is an
        # off-by-one that silently drops any pixel sitting exactly at the
        # threshold (which a clean/high-contrast upload can easily do).
        _, binary = cv2.threshold(resized, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        grid[inside & (binary == 0)] = FAIL
    return grid


def resize_die_grid(grid: np.ndarray, target_size: int = 64) -> np.ndarray:
    """Nearest-neighbor resize so category values (0/1/2) are never blended."""
    grid = np.asarray(grid, dtype=np.uint8)
    return cv2.resize(grid, (target_size, target_size), interpolation=cv2.INTER_NEAREST)


def denoise_fail_mask(grid: np.ndarray) -> np.ndarray:
    """Median-blur the fail mask to drop isolated single-die noise, re-composited onto the grid."""
    fail_mask = (grid == FAIL).astype(np.uint8) * 255
    denoised = cv2.medianBlur(fail_mask, 3)
    out = grid.copy()
    was_fail_now_not = (fail_mask == 255) & (denoised == 0)
    out[was_fail_now_not] = PASS
    return out


def normalize_for_cnn(grid: np.ndarray, target_size: int = 64) -> np.ndarray:
    """
    One-hot encodes {blank, pass, fail} into a (3, H, W) float32 tensor in
    [0, 1], channel-first for PyTorch.
    """
    resized = resize_die_grid(grid, target_size)
    resized = denoise_fail_mask(resized)
    channels = np.stack(
        [(resized == BLANK), (resized == PASS), (resized == FAIL)],
        axis=0,
    ).astype(np.float32)
    return channels


@dataclass
class SpatialStats:
    defect_density: float
    center_fail_ratio: float
    edge_fail_ratio: float
    ring_fail_ratio: float
    cluster_count: int
    largest_cluster_fraction: float
    dominant_zone: str  # CENTER | EDGE | RING | CLUSTER | RANDOM
    zone_scores: dict = field(default_factory=dict)


def compute_spatial_stats(grid: np.ndarray) -> SpatialStats:
    """
    Computes defect density/radial distribution/clustering (spec section
    20). Returns a *descriptive* spatial category — never claims a
    manufacturing cause, only a "potential process/equipment association"
    per spec's terminology rules (surfaced by the caller, not here).
    """
    grid = np.asarray(grid)
    h, w = grid.shape
    cy, cx = (h - 1) / 2, (w - 1) / 2
    radius = min(h, w) / 2 * 0.94

    yy, xx = np.mgrid[0:h, 0:w]
    dist = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
    inside = grid != BLANK
    fail = grid == FAIL

    total_dies = int(inside.sum()) or 1
    total_fail = int(fail.sum())
    defect_density = total_fail / total_dies

    center_zone = inside & (dist <= radius * 0.35)
    edge_zone = inside & (dist >= radius * 0.85)
    ring_zone = inside & (dist >= radius * 0.55) & (dist < radius * 0.85)

    def _ratio(zone_mask: np.ndarray) -> float:
        zone_total = int(zone_mask.sum()) or 1
        zone_fail = int((zone_mask & fail).sum())
        return zone_fail / zone_total

    center_ratio = _ratio(center_zone)
    edge_ratio = _ratio(edge_zone)
    ring_ratio = _ratio(ring_zone)

    fail_mask_u8 = fail.astype(np.uint8)
    num_labels, labels = cv2.connectedComponents(fail_mask_u8, connectivity=8)
    cluster_sizes = [int((labels == i).sum()) for i in range(1, num_labels)]
    cluster_count = len(cluster_sizes)
    largest_cluster_fraction = (max(cluster_sizes) / total_fail) if cluster_sizes and total_fail else 0.0

    zone_scores = {
        "CENTER": center_ratio,
        "EDGE": edge_ratio,
        "RING": ring_ratio,
        "CLUSTER": largest_cluster_fraction if cluster_count <= 3 else 0.0,
        "RANDOM": 1.0 - max(center_ratio, edge_ratio, ring_ratio) if defect_density > 0.02 else 0.0,
    }
    dominant_zone = max(zone_scores, key=zone_scores.get) if defect_density > 0.01 else "RANDOM"

    return SpatialStats(
        defect_density=round(defect_density, 4),
        center_fail_ratio=round(center_ratio, 4),
        edge_fail_ratio=round(edge_ratio, 4),
        ring_fail_ratio=round(ring_ratio, 4),
        cluster_count=cluster_count,
        largest_cluster_fraction=round(largest_cluster_fraction, 4),
        dominant_zone=dominant_zone,
        zone_scores={k: round(v, 4) for k, v in zone_scores.items()},
    )
