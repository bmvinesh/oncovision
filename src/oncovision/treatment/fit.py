"""Estimate treatment effect sizes from observed tumour-volume trajectories.

For every pair of consecutive scans of a patient (an *interval*):

    ln((V1 + c) / (V0 + c)) = r_i * dt  -  sum_a kappa_a * X_a  -  k_rt * Gy  + noise

with  X_a  the effective exposure-days of agent a inside the interval (dosing curve
convolved with the agent's persistence), ``Gy`` the radiation dose delivered in the
interval, and ``r_i`` a patient-specific untreated growth rate.  ``kappa_a`` and
``k_rt`` are constrained to be >= 0; ``r_i`` is shrunk toward the cohort mean (ridge)
so that patients with a single interval do not absorb the treatment effect.

What this is and is not: an association estimated from observational, mostly
post-operative scans with confounding by indication and by surgery.  Intervals that
contain a dated surgery or a large jump of the resection cavity are excluded.  The
output is used as the *model-fitted effect* in the simulation, with the bootstrap
interval shown next to it; it is not a causal drug-efficacy estimate.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import lsq_linear

from .agents import ALL_KEYS, DRUG_KEYS
from .schedule import Schedule

FLOOR_MM3 = 1000.0       # c in the log ratio: damps tiny-volume noise


def build_intervals(tp: pd.DataFrame, schedules: dict, measure: str = "core_mm3") -> pd.DataFrame:
    """One row per consecutive scan pair with exposures.  ``tp`` needs patient_uid, day, <measure>, rc_mm3."""
    rows = []
    for uid, g in tp.sort_values(["patient_uid", "day"]).groupby("patient_uid"):
        if uid not in schedules:
            continue
        sch = Schedule.from_dict(schedules[uid]["schedule"])
        surg = schedules[uid].get("surgery_days", []) or []
        g = g.reset_index(drop=True)
        for i in range(len(g) - 1):
            a, b = g.iloc[i], g.iloc[i + 1]
            t0, t1 = float(a.day), float(b.day)
            if t1 - t0 < 7:
                continue
            rc0, rc1 = float(a.rc_mm3), float(b.rc_mm3)
            flags = []
            if any(t0 <= s <= t1 for s in surg):
                flags.append("surgery_in_interval")
            if rc1 > max(2.0 * rc0, rc0 + 3000.0):
                flags.append("cavity_grew")
            row = {"patient_uid": uid, "tp_from": int(a.tp_index), "tp_to": int(b.tp_index),
                   "t0": t0, "t1": t1, "dt": t1 - t0, "v0": float(a[measure]), "v1": float(b[measure]),
                   "y": float(np.log((b[measure] + FLOOR_MM3) / (a[measure] + FLOOR_MM3))),
                   "rt_gy": sch.radiation_dose_between(t0, t1), "qc": ",".join(flags) or "ok"}
            for k in DRUG_KEYS:
                row[f"x_{k}"] = sch.exposure_days_between(k, t0, t1)
            rows.append(row)
    return pd.DataFrame(rows)


@dataclass
class FitResult:
    kill: dict[str, float]              # per day at full exposure (drug agents)
    k_per_gy: float
    r_patient: dict[str, float]         # untreated growth rate per patient (per day)
    r_mean: float
    n_intervals: int
    n_patients: int
    exposed_intervals: dict[str, int]


def _design(df: pd.DataFrame, agents: list[str], include_rt: bool):
    uids = sorted(df.patient_uid.unique())
    pidx = {u: i for i, u in enumerate(uids)}
    n = len(df)
    Xp = np.zeros((n, len(uids)))
    for r, (u, dt) in enumerate(zip(df.patient_uid, df.dt)):
        Xp[r, pidx[u]] = dt
    cols = [df[f"x_{a}"].to_numpy() for a in agents]
    if include_rt:
        cols.append(df.rt_gy.to_numpy())
    Xt = -np.stack(cols, axis=1) if cols else np.zeros((n, 0))
    return uids, Xp, Xt


def fit_effects(iv: pd.DataFrame, *, min_exposed: int = 8, sigma: float = 0.5, tau_r: float = 0.01,
                tau_k: float = 0.02, r_bounds=(-0.05, 0.08)) -> FitResult:
    """Gaussian-prior (ridge) least squares with sign constraints.

    sigma: noise sd of the log ratio; tau_r: prior sd of a patient's growth rate around the cohort mean
    (per day); tau_k: prior sd of an effect size (per exposure-day, or per Gy)."""
    df = iv[iv.qc == "ok"].copy()
    exposed = {a: int((df[f"x_{a}"] > 1.0).sum()) for a in DRUG_KEYS}
    agents = [a for a in DRUG_KEYS if exposed[a] >= min_exposed]
    include_rt = int((df.rt_gy > 0).sum()) >= min_exposed
    uids, Xp, Xt = _design(df, agents, include_rt)
    y = df.y.to_numpy()
    P = Xp.shape[1]
    # columns: [cohort untreated growth rate (free)] + [patient deviations (ridge)] + [exposures]
    dt = df.dt.to_numpy()
    X = np.hstack([dt[:, None], Xp, Xt])
    pen = np.concatenate([[0.0], np.full(P, sigma / tau_r), np.full(Xt.shape[1], sigma / tau_k)])
    A = np.vstack([X, np.diag(pen)])
    b = np.concatenate([y, np.zeros(len(pen))])
    lb = np.concatenate([[r_bounds[0]], np.full(P, -0.05), np.zeros(Xt.shape[1])])
    ub = np.concatenate([[r_bounds[1]], np.full(P, 0.05), np.full(Xt.shape[1], np.inf)])
    sol = lsq_linear(A, b, bounds=(lb, ub), method="bvls", max_iter=5000)
    coef = sol.x
    r0 = float(coef[0])
    r_i = {u: float(r0 + c) for u, c in zip(uids, coef[1:1 + P])}
    k = coef[1 + P:]
    kill = {a: float(k[j]) for j, a in enumerate(agents)}
    k_gy = float(k[len(agents)]) if include_rt else 0.0
    return FitResult(kill, k_gy, r_i, r0, len(df), len(uids), exposed)


def bootstrap(iv: pd.DataFrame, n: int = 100, seed: int = 0, **kw) -> dict:
    """Patient-cluster bootstrap percentile intervals for kill and k_per_gy."""
    rng = np.random.default_rng(seed)
    ok = iv[iv.qc == "ok"]
    uids = ok.patient_uid.unique()
    groups = {u: g for u, g in ok.groupby("patient_uid")}
    draws = {a: [] for a in DRUG_KEYS}; draws["radiation"] = []
    for _ in range(n):
        pick = rng.choice(uids, size=len(uids), replace=True)
        parts = []
        for j, u in enumerate(pick):
            g = groups[u].copy(); g["patient_uid"] = f"{u}#{j}"; parts.append(g)
        r = fit_effects(pd.concat(parts), **kw)
        for a in DRUG_KEYS:
            draws[a].append(r.kill.get(a, np.nan))
        draws["radiation"].append(r.k_per_gy)
    out = {}
    for a, v in draws.items():
        v = np.array(v, dtype=float)
        v = v[~np.isnan(v)]
        out[a] = None if len(v) < n * 0.5 else [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))]
    return out


def cv_score(iv: pd.DataFrame, folds: int = 5, seed: int = 0, **kw) -> dict:
    """Leave-patients-out: predict ln-ratio for held-out intervals using population effects and the
    cohort mean growth rate (held-out patients' own rate is unknown).  Compared with a no-treatment
    baseline (mean rate only)."""
    ok = iv[iv.qc == "ok"].reset_index(drop=True)
    uids = ok.patient_uid.unique()
    rng = np.random.default_rng(seed)
    uids = np.array(sorted(uids)); rng.shuffle(uids)
    fold_of = {u: i % folds for i, u in enumerate(uids)}
    f = ok.patient_uid.map(fold_of).to_numpy()
    err_t, err_0, ys = [], [], []
    for k in range(folds):
        tr, te = ok[f != k], ok[f == k]
        if len(te) == 0 or tr.patient_uid.nunique() < 10:
            continue
        r = fit_effects(tr, **kw)
        pred = r.r_mean * te.dt.to_numpy()
        pred = pred - sum(r.kill.get(a, 0.0) * te[f"x_{a}"].to_numpy() for a in DRUG_KEYS) - r.k_per_gy * te.rt_gy.to_numpy()
        base = (tr.y.sum() / tr.dt.sum()) * te.dt.to_numpy()
        err_t.append(np.abs(te.y.to_numpy() - pred)); err_0.append(np.abs(te.y.to_numpy() - base)); ys.append(te.y.to_numpy())
    et, e0 = np.concatenate(err_t), np.concatenate(err_0)
    return {"n_test_intervals": int(len(et)), "mae_with_treatment": float(et.mean()),
            "mae_no_treatment_baseline": float(e0.mean()),
            "relative_improvement": float(1 - et.mean() / e0.mean())}


def run(tp: pd.DataFrame, schedules: dict, out_json: str, measure: str = "core_mm3", n_boot: int = 100) -> dict:
    iv = build_intervals(tp, schedules, measure)
    res = fit_effects(iv)
    ci = bootstrap(iv, n_boot)
    cv = cv_score(iv)
    out = {
        "measure": measure,
        "n_intervals_total": int(len(iv)), "n_intervals_used": res.n_intervals, "n_patients": res.n_patients,
        "excluded": iv.qc.value_counts().to_dict(),
        "cohort_mean_growth_per_day": res.r_mean,
        "kill_per_day": res.kill, "k_per_gy": res.k_per_gy,
        "ci95": ci, "exposed_intervals": res.exposed_intervals,
        "radiation_exposed_intervals": int((iv[iv.qc == "ok"].rt_gy > 0).sum()), "cross_validation": cv,
        "caveat": "Association from observational post-operative scans (confounding by indication, "
                  "surgery and steroids). Not a causal drug-efficacy estimate.",
    }
    with open(out_json, "w") as f:
        json.dump(out, f, indent=2)
    iv.to_parquet(out_json.replace(".json", "_intervals.parquet"), index=False)
    return out
