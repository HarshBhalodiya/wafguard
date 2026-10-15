"""
Deterministic wafer-map materialization.

Schema v2 does not store 48x48 die grids in Mongo (24k+ bulky arrays).
Instead, the lot's causal record (spatial_signature + defect_density) is
the seed: a wafer's grid is derived deterministically from
(wafer_id, signature, density) with a fixed hash seed, so
 - the same wafer always renders the same map (no flicker between loads),
 - faulty lots actually show their mechanism's signature (Edge-Ring lots
   have edge rings), because the pattern comes from the generator's
   causal chain rather than a random pool,
 - storage stays tiny.

This is still DEMO / SIMULATED DATA: grids are synthetic renders of the
generator's pattern labels, not real fab inspection images.
"""
import hashlib

import numpy as np

BLANK, PASS, FAIL = 0, 1, 2
DEFAULT_GRID_SIZE = 48


def _wafer_geometry(grid_size: int):
    center = (grid_size - 1) / 2
    radius = grid_size / 2 * 0.94
    yy, xx = np.mgrid[0:grid_size, 0:grid_size]
    dist = np.sqrt((yy - center) ** 2 + (xx - center) ** 2)
    inside = dist <= radius
    return center, radius, dist, inside


def _seeded_rng(wafer_id: str) -> np.random.Generator:
    digest = hashlib.sha256(wafer_id.encode()).hexdigest()
    seed = int(digest[:16], 16)
    return np.random.default_rng(seed)


def _paint_edge_ring(grid, inside, dist, radius, rng, intensity):
    band = inside & (dist >= radius * 0.80)
    hit = band & (rng.random(grid.shape) < intensity)
    grid[hit] = FAIL


def _paint_donut(grid, inside, dist, radius, rng, intensity):
    band = inside & (dist >= radius * 0.32) & (dist <= radius * 0.68)
    hit = band & (rng.random(grid.shape) < intensity)
    grid[hit] = FAIL


def _paint_center(grid, inside, dist, radius, rng, intensity):
    prob = np.clip(1 - dist / (radius * 0.30), 0, 1) ** 1.5
    hit = inside & (rng.random(grid.shape) < prob * intensity)
    grid[hit] = FAIL


def _paint_loc(grid, inside, dist, radius, rng, intensity):
    angle = rng.uniform(0, 2 * np.pi)
    blob_dist = radius * rng.uniform(0.32, 0.55)
    cy = (grid.shape[0] - 1) / 2 + blob_dist * np.sin(angle)
    cx = (grid.shape[0] - 1) / 2 + blob_dist * np.cos(angle)
    blob_r = radius * 0.18
    yy, xx = np.mgrid[0 : grid.shape[0], 0 : grid.shape[1]]
    local = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
    prob = np.clip(1 - local / blob_r, 0, 1) ** 1.3
    hit = inside & (rng.random(grid.shape) < prob * intensity)
    grid[hit] = FAIL


def _paint_scratch(grid, inside, dist, radius, rng, intensity):
    size = grid.shape[0]
    center = (size - 1) / 2
    angle = rng.uniform(0, np.pi)
    offset = rng.uniform(-radius * 0.5, radius * 0.5)
    nx, ny = -np.sin(angle), np.cos(angle)
    px, py = center + offset * nx, center + offset * ny
    yy, xx = np.mgrid[0:size, 0:size]
    perp = np.abs((xx - px) * nx + (yy - py) * ny)
    band = inside & (perp <= 1.4)
    hit = band & (rng.random(grid.shape) < intensity)
    grid[hit] = FAIL


def _paint_random(grid, inside, rng, intensity):
    hit = inside & (rng.random(grid.shape) < intensity)
    grid[hit] = FAIL


_PAINTERS = {
    "Edge-Ring": _paint_edge_ring,
    "Donut": _paint_donut,
    "Center": _paint_center,
    "Loc": _paint_loc,
    "Scratch": _paint_scratch,
    "Random": _paint_random,
}


def materialize_die_grid(
    wafer_id: str,
    pattern: str,
    defect_density: float,
    grid_size: int = DEFAULT_GRID_SIZE,
) -> np.ndarray:
    """
    Deterministic die grid (0=blank, 1=pass, 2=fail) for a wafer.

    `pattern` is the generator's spatial signature for the wafer's lot;
    `defect_density` scales how many dies fail. Same inputs always give
    the same grid.
    """
    center, radius, dist, inside = _wafer_geometry(grid_size)
    grid = np.where(inside, PASS, BLANK).astype(np.int8)
    rng = _seeded_rng(wafer_id)

    # Background noise
    noise = inside & (rng.random((grid_size, grid_size)) < 0.012)
    grid[noise] = FAIL

    density = float(np.clip(defect_density if defect_density is not None else 0.01, 0.0, 1.0))
    if pattern and pattern in _PAINTERS and density > 0:
        intensity = float(np.clip(0.35 + density * 3.0, 0.35, 0.92))
        _PAINTERS[pattern](grid, inside, dist, radius, rng, intensity) if pattern != "Random" else _paint_random(grid, inside, rng, intensity)

    return grid
