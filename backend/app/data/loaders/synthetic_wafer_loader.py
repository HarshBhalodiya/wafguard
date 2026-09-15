"""
Procedural synthetic wafer-map generator.

Stands in for WM-811K (spec section 4-5: "if the Kaggle dataset is not
downloaded yet, the application must still start"). Every record this
module produces carries `data_source="wm811k_synthetic"` and must be
rendered in the UI with the DEMO / SIMULATED DATA label from
`app.data.schemas.dataset_schema`.

Each of the 9 WM-811K taxonomy classes (Center, Donut, Edge-Loc,
Edge-Ring, Loc, Random, Scratch, Near-full, None) is generated from a
distinct spatial rule with per-sample randomization, so the resulting
set is actually learnable (and actually mis-classifiable) by a CNN —
not a repeated template. This is a deliberate substitute for real
fab imagery, not a claim about real defect physics.

Die-grid encoding: 0 = outside wafer (blank), 1 = pass, 2 = fail.
"""
from dataclasses import dataclass
from typing import Iterator

import numpy as np

from app.config import WAFER_PATTERN_CLASSES

BLANK, PASS, FAIL = 0, 1, 2


@dataclass
class WaferMapSample:
    wafer_id: str
    die_grid: list
    pattern_label: str
    data_source: str = "wm811k_synthetic"


def _wafer_mask(grid_size: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """Returns (row_idx, col_idx, dist_from_center, radius) grids for a circular wafer."""
    center = (grid_size - 1) / 2
    radius = grid_size / 2 * 0.94
    yy, xx = np.mgrid[0:grid_size, 0:grid_size]
    dist = np.sqrt((yy - center) ** 2 + (xx - center) ** 2)
    return yy, xx, dist, radius, center


def _base_grid(grid_size: int, rng: np.random.Generator, background_fail_prob: float = 0.012) -> tuple[np.ndarray, np.ndarray, float, float]:
    yy, xx, dist, radius, center = _wafer_mask(grid_size)
    inside = dist <= radius
    grid = np.where(inside, PASS, BLANK).astype(np.int8)
    noise = inside & (rng.random((grid_size, grid_size)) < background_fail_prob)
    grid[noise] = FAIL
    return grid, dist, radius, center


def _generate_one(label: str, grid_size: int, rng: np.random.Generator) -> np.ndarray:
    grid, dist, radius, center = _base_grid(grid_size, rng)
    inside = grid != BLANK

    if label == "None":
        return grid  # background noise only

    if label == "Random":
        extra = inside & (rng.random((grid_size, grid_size)) < rng.uniform(0.05, 0.10))
        grid[extra] = FAIL
        return grid

    if label == "Center":
        blob_r = radius * rng.uniform(0.22, 0.34)
        prob = np.clip(1 - dist / blob_r, 0, 1) ** 1.5
        hit = inside & (rng.random((grid_size, grid_size)) < prob * rng.uniform(0.6, 0.9))
        grid[hit] = FAIL
        return grid

    if label == "Donut":
        r_in, r_out = radius * rng.uniform(0.32, 0.42), radius * rng.uniform(0.55, 0.68)
        band = inside & (dist >= r_in) & (dist <= r_out)
        hit = band & (rng.random((grid_size, grid_size)) < rng.uniform(0.55, 0.8))
        grid[hit] = FAIL
        return grid

    if label == "Edge-Ring":
        r_in = radius * rng.uniform(0.80, 0.88)
        band = inside & (dist >= r_in)
        hit = band & (rng.random((grid_size, grid_size)) < rng.uniform(0.6, 0.85))
        grid[hit] = FAIL
        return grid

    if label == "Edge-Loc":
        angle0 = rng.uniform(0, 2 * np.pi)
        angle_width = rng.uniform(np.pi / 5, np.pi / 3)
        yy, xx = np.mgrid[0:grid_size, 0:grid_size]
        theta = np.arctan2(yy - center, xx - center)
        delta = (theta - angle0 + np.pi) % (2 * np.pi) - np.pi
        arc = inside & (dist >= radius * 0.72) & (np.abs(delta) <= angle_width / 2)
        hit = arc & (rng.random((grid_size, grid_size)) < rng.uniform(0.6, 0.9))
        grid[hit] = FAIL
        return grid

    if label == "Loc":
        blob_angle = rng.uniform(0, 2 * np.pi)
        blob_dist = radius * rng.uniform(0.32, 0.58)  # kept clear of Center's radius so the two classes don't overlap
        blob_cy = center + blob_dist * np.sin(blob_angle)
        blob_cx = center + blob_dist * np.cos(blob_angle)
        blob_r = radius * rng.uniform(0.12, 0.22)
        yy, xx = np.mgrid[0:grid_size, 0:grid_size]
        local_dist = np.sqrt((yy - blob_cy) ** 2 + (xx - blob_cx) ** 2)
        prob = np.clip(1 - local_dist / blob_r, 0, 1) ** 1.3
        hit = inside & (rng.random((grid_size, grid_size)) < prob * rng.uniform(0.6, 0.9))
        grid[hit] = FAIL
        return grid

    if label == "Scratch":
        angle = rng.uniform(0, np.pi)
        offset = rng.uniform(-radius * 0.5, radius * 0.5)
        length_frac = rng.uniform(0.5, 0.95)
        yy, xx = np.mgrid[0:grid_size, 0:grid_size]
        # signed perpendicular distance from the line through (center+offset*normal) at `angle`
        nx, ny = -np.sin(angle), np.cos(angle)
        px = center + offset * nx
        py = center + offset * ny
        perp_dist = np.abs((xx - px) * nx + (yy - py) * ny)
        along = (xx - px) * np.cos(angle) + (yy - py) * np.sin(angle)
        within_length = np.abs(along) <= radius * length_frac
        band = inside & within_length & (perp_dist <= rng.uniform(0.9, 1.6))
        hit = band & (rng.random((grid_size, grid_size)) < rng.uniform(0.55, 0.8))
        grid[hit] = FAIL
        return grid

    if label == "Near-full":
        hit = inside & (rng.random((grid_size, grid_size)) < rng.uniform(0.82, 0.94))
        grid[hit] = FAIL
        return grid

    raise ValueError(f"Unknown wafer pattern label: {label}")


def generate_synthetic_wafer_maps(
    n_per_class: int = 300,
    grid_size: int = 48,
    seed: int = 42,
) -> Iterator[WaferMapSample]:
    rng = np.random.default_rng(seed)
    counter = 0
    for label in WAFER_PATTERN_CLASSES:
        for _ in range(n_per_class):
            grid = _generate_one(label, grid_size, rng)
            counter += 1
            yield WaferMapSample(
                wafer_id=f"SYN_WAFER_{counter:06d}",
                die_grid=grid.tolist(),
                pattern_label=label,
            )
