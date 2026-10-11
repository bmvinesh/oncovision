"""3D reaction-diffusion tumour growth with time-varying treatment.

    du/dt = D lap(u) + rho * g(t) * u (1 - u) - k(t) * u          (continuous terms)
    u    <- u * s(t)                                              (radiation fractions)

``u`` is tumour cell density in [0, 1] on a (Z, Y, X) grid with isotropic voxel
spacing ``dx`` mm.  ``rho`` is per day, ``D`` mm^2/day.  ``g(t)`` is a growth scale
(1 = untreated), ``k(t)`` a per-day kill rate and ``s(t)`` a per-step survival
factor; all three are built from a treatment schedule by
``oncovision.treatment.schedule.step_arrays``.

Two backends with the same contract: ``numpy`` (reference, CPU) and ``torch``
(batched, CPU or CUDA).  Explicit Euler, 7-point Laplacian, replicate boundary.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import laplace

try:  # torch is optional; the numpy backend never imports it
    import torch
    import torch.nn.functional as F
except Exception:  # pragma: no cover
    torch = None  # type: ignore


@dataclass
class SolveResult:
    burden: np.ndarray          # (B, T+1) sum of density * voxel volume, mm^3 ("cell-equivalent volume")
    volume_mm3: np.ndarray      # (B, T+1) volume of voxels with u >= threshold
    frames: np.ndarray | None   # (B, F, Z, Y, X) float32 or None
    frame_steps: list[int]
    dt: float


def cfl_max_dt(D: float, dx: float) -> float:
    return float("inf") if D <= 0 else dx * dx / (6.0 * D)


def n_steps_for(days: float, D: float, dx: float, dt: float | None = None) -> tuple[int, float]:
    """Pick (n_steps, dt) covering ``days`` at or under the CFL limit (default dt <= 1 day)."""
    dt_max = min(1.0 if dt is None else dt, 0.95 * cfl_max_dt(D, dx))
    n = max(1, int(np.ceil(days / dt_max)))
    return n, days / n


def _frame_steps(n_steps: int, n_frames: int | None) -> list[int]:
    if n_frames is None or n_frames <= 0:
        return []
    n_frames = min(n_frames, n_steps + 1)
    return sorted({int(round(x)) for x in np.linspace(0, n_steps, n_frames)})


def _prep(u0, rho, D, dx, kill, growth, surv, n_steps):
    u0 = np.asarray(u0, dtype=np.float32)
    if u0.ndim == 3:
        u0 = u0[None]
    B = u0.shape[0]
    def per_item(v):
        """scalar or (B,) -> (B, 1) float32"""
        return np.broadcast_to(np.asarray(v, dtype=np.float32).reshape(-1, 1), (B, 1)).copy()

    rho, D, dx = per_item(rho), per_item(D), per_item(dx)

    def series(a, fill):
        if a is None:
            return np.full((B, n_steps), fill, dtype=np.float32)
        a = np.asarray(a, dtype=np.float32)
        if a.ndim == 1:
            a = np.broadcast_to(a[None], (B, a.shape[0]))
        if a.shape != (B, n_steps):
            raise ValueError(f"expected schedule array of shape {(B, n_steps)}, got {a.shape}")
        return np.ascontiguousarray(a)

    return u0, rho, D, dx, series(kill, 0.0), series(growth, 1.0), series(surv, 1.0)


def solve_numpy(u0, *, rho, D, dx=1.0, n_steps, dt=1.0, kill=None, growth=None, surv=None,
                n_frames=None, threshold=0.5) -> SolveResult:
    u0, rho, D, dx, kill, growth, surv = _prep(u0, rho, D, dx, kill, growth, surv, n_steps)
    B = u0.shape[0]
    if np.any(D * dt / (dx * dx) > 1.0 / 6.0 + 1e-9):
        raise ValueError("dt exceeds the CFL limit for the given D and dx")
    steps = set(_frame_steps(n_steps, n_frames))
    frames = {}
    burden = np.zeros((B, n_steps + 1))
    vol = np.zeros((B, n_steps + 1))
    out = []
    for b in range(B):
        u = np.clip(u0[b], 0.0, 1.0)
        vv = float(dx[b, 0]) ** 3
        burden[b, 0] = u.sum() * vv
        vol[b, 0] = (u >= threshold).sum() * vv
        fr = {0: u.copy()} if 0 in steps else {}
        c = float(D[b, 0]) / float(dx[b, 0]) ** 2
        for i in range(n_steps):
            lap = laplace(u, mode="nearest")
            u = u + dt * (c * lap + float(rho[b, 0]) * growth[b, i] * u * (1.0 - u) - kill[b, i] * u)
            u = np.clip(u * surv[b, i], 0.0, 1.0).astype(np.float32)
            burden[b, i + 1] = u.sum() * vv
            vol[b, i + 1] = (u >= threshold).sum() * vv
            if (i + 1) in steps:
                fr[i + 1] = u.copy()
        out.append(fr)
    fs = sorted(steps)
    frames_arr = np.stack([np.stack([o[s] for s in fs]) for o in out]) if fs else None
    return SolveResult(burden, vol, frames_arr, fs, dt)


def _lap_torch(u):
    p = F.pad(u, (1, 1, 1, 1, 1, 1), mode="replicate")
    return (p[:, :, 2:, 1:-1, 1:-1] + p[:, :, :-2, 1:-1, 1:-1]
            + p[:, :, 1:-1, 2:, 1:-1] + p[:, :, 1:-1, :-2, 1:-1]
            + p[:, :, 1:-1, 1:-1, 2:] + p[:, :, 1:-1, 1:-1, :-2] - 6.0 * u)


def solve_torch(u0, *, rho, D, dx=1.0, n_steps, dt=1.0, kill=None, growth=None, surv=None,
                n_frames=None, threshold=0.5, device="cpu") -> SolveResult:
    if torch is None:
        raise RuntimeError("torch is not installed")
    u0n, rhon, Dn, dxn, killn, growthn, survn = _prep(u0, rho, D, dx, kill, growth, surv, n_steps)
    if np.any(Dn * dt / (dxn * dxn) > 1.0 / 6.0 + 1e-9):
        raise ValueError("dt exceeds the CFL limit for the given D and dx")
    dev = torch.device(device)
    B = u0n.shape[0]
    T = lambda a: torch.as_tensor(a, device=dev)
    u = T(np.clip(u0n, 0.0, 1.0)).unsqueeze(1)                       # (B,1,Z,Y,X)
    rho_t = T(rhon).view(B, 1, 1, 1, 1)
    c_t = (T(Dn) / (T(dxn) ** 2)).view(B, 1, 1, 1, 1)
    vox = (T(dxn) ** 3).view(B)
    kill_t, growth_t, surv_t = T(killn), T(growthn), T(survn)
    steps = set(_frame_steps(n_steps, n_frames))
    burden = torch.zeros(B, n_steps + 1, device=dev)
    vol = torch.zeros(B, n_steps + 1, device=dev)

    def record(i, uu):
        burden[:, i] = uu.sum(dim=(1, 2, 3, 4)) * vox
        vol[:, i] = (uu >= threshold).sum(dim=(1, 2, 3, 4)).float() * vox

    record(0, u)
    fr = {0: u[:, 0].cpu().numpy()} if 0 in steps else {}
    with torch.no_grad():
        for i in range(n_steps):
            g = growth_t[:, i].view(B, 1, 1, 1, 1)
            k = kill_t[:, i].view(B, 1, 1, 1, 1)
            s = surv_t[:, i].view(B, 1, 1, 1, 1)
            u = u + dt * (c_t * _lap_torch(u) + rho_t * g * u * (1.0 - u) - k * u)
            u = torch.clamp(u * s, 0.0, 1.0)
            record(i + 1, u)
            if (i + 1) in steps:
                fr[i + 1] = u[:, 0].cpu().numpy()
    fs = sorted(steps)
    frames = np.stack([fr[s] for s in fs], axis=1) if fs else None
    return SolveResult(burden.cpu().numpy().astype(np.float64), vol.cpu().numpy().astype(np.float64), frames, fs, dt)


def solve(u0, *, backend="numpy", **kw) -> SolveResult:
    if backend == "numpy":
        kw.pop("device", None)
        return solve_numpy(u0, **kw)
    if backend == "torch":
        return solve_torch(u0, **kw)
    raise ValueError(backend)


def simulate_patient(u0, *, rho, D, dx, days, schedule=None, effects=None, dt=1.0, n_frames=26,
                     t_start=0.0, backend="numpy", device="cpu", threshold=0.5) -> dict:
    """Convenience wrapper: one patient, optional treatment schedule, returns plain arrays."""
    from ..treatment.schedule import Schedule, step_arrays

    n, step = n_steps_for(days, D, dx, dt)
    sched = schedule or Schedule()
    kill, growth, surv = step_arrays(sched, n, step, effects or {}, t_start=t_start)
    res = solve(np.asarray(u0)[None], backend=backend, rho=rho, D=D, dx=dx, n_steps=n, dt=step,
                kill=kill, growth=growth, surv=surv, n_frames=n_frames, threshold=threshold, device=device)
    t = np.arange(n + 1) * step
    return {"t": t, "burden_mm3": res.burden[0], "volume_mm3": res.volume_mm3[0],
            "frames": None if res.frames is None else res.frames[0],
            "frame_days": [s * step for s in res.frame_steps]}
