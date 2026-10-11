"""Tumour masks -> PDE density seeds, one shared crop and grid per patient.

Fixes two defects of the OncoPulse pipeline:

* the resection cavity (label 4) is *not* tumour: it is excluded from the seed;
* every timepoint of a patient uses the same crop box and spacing, so the seeds
  line up in space and follow-up burden is directly comparable to the baseline.

Spatial extent comes from the expert/automatic mask.  The density assigned inside
each region is a modelling choice, not a measurement (MRI does not measure cell
density); the values below follow the OncoPulse seeding.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# harmonised labels (1 ncr, 2 edema, 3 et, 4 rc) -> initial density; the cavity is 0
LABEL_DENSITY = {1: 0.95, 2: 0.20, 3: 0.50, 4: 0.0}
TUMOUR_LABELS = (1, 2, 3)


@dataclass
class PatientGrid:
    origin: tuple[int, int, int]      # crop start in source voxels
    extent: tuple[int, int, int]      # crop size in source voxels (multiple of ``factor``)
    factor: int                       # source voxels per grid cell (isotropic)
    grid: int
    spacing_mm: float                 # mm per grid cell


def union_bbox(masks: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray] | None:
    lo = hi = None
    for m in masks:
        idx = np.nonzero(np.isin(m, TUMOUR_LABELS))
        if len(idx[0]) == 0:
            continue
        l = np.array([i.min() for i in idx]); h = np.array([i.max() for i in idx])
        lo = l if lo is None else np.minimum(lo, l)
        hi = h if hi is None else np.maximum(hi, h)
    return None if lo is None else (lo, hi)


def make_grid(masks: list[np.ndarray], voxel_mm: float = 1.0, grid: int = 64, pad_mm: float = 12.0) -> PatientGrid | None:
    bb = union_bbox(masks)
    if bb is None:
        return None
    lo, hi = bb
    pad = int(math.ceil(pad_mm / voxel_mm))
    ext = (hi - lo + 1) + 2 * pad
    factor = max(1, int(math.ceil(ext.max() / grid)))
    size = grid * factor
    centre = (lo + hi + 1) / 2.0
    origin = np.rint(centre - size / 2.0).astype(int)
    return PatientGrid(tuple(int(o) for o in origin), (size, size, size), factor, grid, factor * voxel_mm)


def _crop(a: np.ndarray, origin, size, fill=0):
    out = np.full(size, fill, dtype=a.dtype)
    src, dst = [], []
    for o, s, n in zip(origin, size, a.shape):
        s0 = max(o, 0); s1 = min(o + s, n)
        if s1 <= s0:
            return out
        src.append(slice(s0, s1)); dst.append(slice(s0 - o, s1 - o))
    out[tuple(dst)] = a[tuple(src)]
    return out


def seed_density(labels: np.ndarray, g: PatientGrid) -> np.ndarray:
    """Density seed on the patient grid (block-mean over ``factor``^3 source voxels)."""
    crop = _crop(labels.astype(np.int16), g.origin, g.extent)
    dens = np.zeros(crop.shape, dtype=np.float32)
    for lab, v in LABEL_DENSITY.items():
        if v:
            dens[crop == lab] = v
    f, n = g.factor, g.grid
    dens = dens.reshape(n, f, n, f, n, f).mean(axis=(1, 3, 5))
    return np.clip(dens, 0.0, 0.999).astype(np.float32)


def label_grid(labels: np.ndarray, g: PatientGrid) -> np.ndarray:
    """Majority-free label cube on the grid (for Dice): tumour (1-3) fraction >= 0.5 -> 1."""
    crop = _crop(labels.astype(np.int16), g.origin, g.extent)
    t = np.isin(crop, TUMOUR_LABELS).astype(np.float32)
    f, n = g.factor, g.grid
    return (t.reshape(n, f, n, f, n, f).mean(axis=(1, 3, 5)) >= 0.5)
