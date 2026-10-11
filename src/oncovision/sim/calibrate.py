"""Batched calibration of a patient's proliferation rate under their treatment schedule.

For each (patient, interval) the seed at the first scan is simulated forward with
the patient's *actual* schedule and the fitted treatment effects, and ``rho`` is
bisected so the simulated burden ratio matches the observed volume ratio.  The same
rho then drives counterfactual runs with the schedule changed or removed.

If even rho = 0 over-shoots the observed volume (the tumour shrank more than the
fitted kill explains), an extra loss rate ``delta`` is bisected instead and the row
is marked ``shrinking``.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..treatment.schedule import Schedule, step_arrays
from .solver import n_steps_for, solve


@dataclass
class CalItem:
    seed: np.ndarray            # (g,g,g) density at the interval start
    days: float                 # interval length
    t_start: float              # interval start relative to the patient's first scan
    schedule: Schedule
    ratio: float                # observed (V1+c)/(V0+c)
    dx: float                   # mm per grid cell


def _arrays(items: list[CalItem], effects: dict, D: float, dt_cap: float = 1.0):
    # one common step size: the smallest CFL-safe dt among items, bounded by 1 day
    dts = [n_steps_for(it.days, D, it.dx, dt_cap)[1] for it in items]
    dt = min(dts)
    n = max(int(np.ceil(it.days / dt)) for it in items)
    B = len(items)
    kill = np.zeros((B, n), np.float32); growth = np.ones((B, n), np.float32); surv = np.ones((B, n), np.float32)
    active = np.zeros((B, n), bool)
    for b, it in enumerate(items):
        nb = int(round(it.days / dt))
        k, g, s = step_arrays(it.schedule, nb, dt, effects, t_start=it.t_start)
        kill[b, :nb], growth[b, :nb], surv[b, :nb] = k, g, s
        active[b, :nb] = True
    return n, dt, kill, growth, surv, active


def pack(items: list[CalItem], effects: dict, D: float) -> dict:
    """Build the per-step schedule arrays once; reuse across bisection iterations."""
    n, dt, kill, growth, surv, active = _arrays(items, effects, D)
    return {"n": n, "dt": dt, "kill": kill, "surv": surv, "active": active,
            "growth": np.where(active, growth, 0.0).astype(np.float32),
            "seeds": np.stack([it.seed for it in items]),
            "dx": np.array([it.dx for it in items], np.float32),
            "ends": np.array([int(round(it.days / dt)) for it in items]), "D": D}


def _end_burden(items, rho, delta, effects, D, backend, device, packed: dict | None = None):
    """Burden at each item's own end (and at its start) for per-item rho / extra loss delta."""
    pk = packed or pack(items, effects, D)
    # items with different lengths: all rates are zero after an item's own end
    kill = np.where(pk["active"], pk["kill"] + np.asarray(delta, np.float32)[:, None], 0.0).astype(np.float32)
    res = solve(pk["seeds"], backend=backend, device=device, rho=rho, D=pk["D"], dx=pk["dx"], n_steps=pk["n"],
                dt=pk["dt"], kill=kill, growth=pk["growth"], surv=pk["surv"], n_frames=0)
    return res.burden[np.arange(len(items)), pk["ends"]], res.burden[:, 0]


def calibrate_batch(items: list[CalItem], effects: dict, *, D: float = 0.1, rho_max: float = 0.5,
                    delta_max: float = 0.5, iters: int = 22, backend: str = "numpy", device: str = "cpu"):
    """Returns dict of arrays: rho, delta, achieved_ratio, status (list)."""
    B = len(items)
    zero = np.zeros(B)
    target_ratio = np.array([it.ratio for it in items])

    pk = pack(items, effects, D)

    def ratio(rho, delta):
        end, b0 = _end_burden(items, rho, delta, effects, D, backend, device, pk)
        return end / np.maximum(b0, 1e-9)

    lo, hi = np.zeros(B), np.full(B, rho_max)
    r_lo = ratio(lo, zero)
    status = np.array(["ok"] * B, dtype=object)
    shrink = r_lo > target_ratio             # even rho=0 is too large -> need extra loss
    for _ in range(iters):
        mid = (lo + hi) / 2
        r = ratio(mid, zero)
        up = r < target_ratio
        lo = np.where(up, mid, lo); hi = np.where(up, hi, mid)
    rho = (lo + hi) / 2
    delta = np.zeros(B)
    if shrink.any():
        dlo, dhi = np.zeros(B), np.full(B, delta_max)
        for _ in range(iters):
            mid = (dlo + dhi) / 2
            r = ratio(np.zeros(B), mid)
            more = r > target_ratio          # still too big -> more loss
            dlo = np.where(more, mid, dlo); dhi = np.where(more, dhi, mid)
        delta = np.where(shrink, (dlo + dhi) / 2, 0.0)
        rho = np.where(shrink, 0.0, rho)
        status[shrink] = "shrinking"
    achieved = ratio(rho, delta)
    unreach = (~shrink) & (np.abs(achieved - target_ratio) / np.maximum(target_ratio, 1e-9) > 0.05)
    status[unreach] = "no_convergence"
    return {"rho": rho, "delta": delta, "achieved_ratio": achieved, "target_ratio": target_ratio,
            "status": status.tolist()}


def counterfactual(seed, *, rho, delta, D, dx, days, t_start, schedule: Schedule, effects: dict,
                   n_frames: int = 0, backend: str = "numpy", device: str = "cpu"):
    """One patient forward run -> dict(t, burden, volume, frames)."""
    n, dt = n_steps_for(days, D, dx, 1.0)
    k, g, s = step_arrays(schedule, n, dt, effects, t_start=t_start)
    res = solve(seed[None], backend=backend, device=device, rho=rho, D=D, dx=dx, n_steps=n, dt=dt,
                kill=(k + delta)[None], growth=g[None], surv=s[None], n_frames=n_frames)
    return {"t": np.arange(n + 1) * dt + t_start, "burden": res.burden[0], "volume": res.volume_mm3[0],
            "frames": None if res.frames is None else res.frames[0],
            "frame_t": [x * dt + t_start for x in res.frame_steps]}
