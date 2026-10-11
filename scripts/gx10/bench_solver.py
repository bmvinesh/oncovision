"""Micro-benchmark of the PDE solver on this machine: NumPy/SciPy (CPU), Torch CPU, Torch GPU.

    python scripts/gx10/bench_solver.py --out bench.json

Shapes follow the real pipeline: batched calibration at 64^3 (the cohort run) and a 128^3 case
(the higher-resolution grid).  Items not run at full batch size are timed at a smaller batch and
scaled linearly (the work is linear in batch size); the output marks every such row as extrapolated.
"""
import argparse
import json
import platform
import time

import numpy as np

from oncovision.sim.solver import solve_numpy, solve_torch


def seeds(b, g):
    rng = np.random.default_rng(0)
    z, y, x = np.indices((g, g, g)).astype(np.float32)
    out = []
    for i in range(b):
        c = g / 2 + rng.uniform(-g / 8, g / 8, 3)
        r = g / 8 + rng.uniform(0, g / 16)
        d2 = (z - c[0]) ** 2 + (y - c[1]) ** 2 + (x - c[2]) ** 2
        out.append((0.9 * np.exp(-d2 / (2 * r * r))).astype(np.float32))
    return np.stack(out)


def time_it(fn, repeats=2):
    fn()                                  # warm-up (allocation, kernel load)
    best = 1e9
    for _ in range(repeats):
        t = time.perf_counter(); fn(); best = min(best, time.perf_counter() - t)
    return best


def row(name, backend, device, g, b_full, b_run, steps, fn):
    t = time_it(fn)
    t_full = t * (b_full / b_run)
    return {"backend": name, "grid": g, "batch_requested": b_full, "batch_timed": b_run, "steps": steps,
            "seconds_timed": round(t, 3), "seconds_for_requested_batch": round(t_full, 3),
            "extrapolated": b_run != b_full,
            "gvoxel_steps_per_s": round(b_run * g ** 3 * steps / t / 1e9, 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--steps", type=int, default=60)
    a = ap.parse_args()
    import torch
    devs = [("torch-cpu", "cpu")]
    if torch.cuda.is_available():
        devs.append(("torch-cuda", "cuda"))
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        devs.append(("torch-mps", "mps"))
    rows = []
    for g, b_full in ((64, 64), (128, 8)):
        S = seeds(b_full, g)
        kw = dict(rho=0.03, D=0.1, dx=1.0, n_steps=a.steps, dt=1.0)
        b_np = 2 if g == 64 else 1
        rows.append(row("numpy-scipy (1 thread)", "numpy", "cpu", g, b_full, b_np, a.steps,
                        lambda S=S, b=b_np: solve_numpy(S[:b], **kw)))
        for name, dev in devs:
            b_run = b_full if dev != "cpu" else min(b_full, 8 if g == 64 else 2)
            try:
                rows.append(row(name, "torch", dev, g, b_full, b_run, a.steps,
                                lambda S=S, b=b_run, d=dev: solve_torch(S[:b], device=d, **kw)))
            except Exception as e:                                     # e.g. out of memory on small devices
                rows.append({"backend": name, "grid": g, "error": f"{type(e).__name__}: {str(e)[:120]}"})
    info = {"machine": platform.platform(), "processor": platform.processor(), "torch": torch.__version__,
            "cuda": torch.cuda.is_available(),
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}
    out = {"info": info, "rows": rows}
    print(json.dumps(out, indent=1))
    if a.out:
        open(a.out, "w").write(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
