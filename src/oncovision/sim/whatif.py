"""Per-patient what-if runs: calibrate on the first interval, forecast later scans, vary treatment.

    python -m oncovision.sim.whatif --dataset mu_glioma_post --processed ~/data/oncovision/processed \\
        --out ~/data/oncovision/processed/whatif --device cuda

For each patient with >= 2 usable scans:

1. seed the PDE from the first scan's tumour labels on a patient-wide crop (``seeding``);
2. calibrate ``rho`` so the burden after the first interval matches the follow-up scan, with the
   *recorded* schedule and the literature-prior effects (``priors``), and again with no treatment
   effects at all;
3. forecast every later scan from the previous observed scan with that ``rho`` and compare with
   what was observed, against baselines (no change, cohort median rate, treatment-blind rho);
4. run named scenarios forward from the last observed scan.

The third step is the honest test of whether including the treatment schedule helps.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import nibabel as nib
import numpy as np
import pandas as pd

from ..treatment import priors, scenarios
from ..treatment.schedule import Schedule
from . import seeding
from .calibrate import CalItem, calibrate_batch, counterfactual

D_DEFAULT = 0.1      # mm^2/day, typical glioma diffusion; not fitted per patient here


_HARMONISED = {"ncr": 1, "edema": 2, "et": 3, "rc": 4}


def _load(path, dataset: str | None = None):
    """Load a label mask and map the dataset's own label values to the harmonised ones
    (1 ncr, 2 edema, 3 et, 4 rc) so the seeding is dataset-agnostic."""
    a = np.asarray(nib.load(path).dataobj).astype(np.int16)
    if dataset is None:
        return a
    from ..imaging.volumes import LABEL_MAPS
    lm = LABEL_MAPS[dataset]
    if all(lm.get(k) and _HARMONISED[lm[k]] == k for k in lm):
        return a
    out = np.zeros_like(a)
    for raw, name in lm.items():
        out[a == raw] = _HARMONISED[name]
    return out


def prepare_patient(g: pd.DataFrame, sched: dict, grid: int = 64):
    g = g.sort_values("day").reset_index(drop=True)
    if len(g) < 2:
        return None
    ds = g.dataset.iloc[0] if "dataset" in g.columns else None
    masks = [_load(p, ds) for p in g.mask_path]
    spacing = json.loads(g.spacing.iloc[0]) if isinstance(g.spacing.iloc[0], str) else list(g.spacing.iloc[0])
    pg = seeding.make_grid(masks, voxel_mm=float(spacing[0]), grid=grid)
    if pg is None:
        return None
    seeds = [seeding.seed_density(m, pg) for m in masks]
    vox = pg.spacing_mm ** 3
    burdens = [float(s.sum() * vox) for s in seeds]
    return {"grid": pg, "seeds": seeds, "burden": burdens, "days": g.day.to_numpy(float), "g": g,
            "schedule": Schedule.from_dict(sched["schedule"]) if sched else Schedule(),
            "assumptions": (sched or {}).get("assumptions", [])}


def run(dataset: str, processed: str, out_dir: str, device: str = "cpu", backend: str | None = None,
        max_patients: int | None = None, horizon: float = 180.0, min_burden_mm3: float = 500.0):
    backend = backend or ("torch" if device != "cpu" else "numpy")
    t_0 = time.time()
    log = lambda m: print(f"[{time.time() - t_0:7.1f}s] {m}", flush=True)
    P = Path(processed) / dataset
    tp = pd.read_parquet(P / "timepoints.parquet")
    sched = json.loads((P / "treatments.json").read_text())
    out = Path(out_dir) / dataset
    out.mkdir(parents=True, exist_ok=True)

    pats = []
    for uid, g in tp.groupby("patient_uid"):
        if max_patients and len(pats) >= max_patients:
            break
        p = prepare_patient(g, sched.get(uid))
        if p and p["burden"][0] >= min_burden_mm3 and p["burden"][1] >= min_burden_mm3 * 0.2:
            p["uid"] = uid
            pats.append(p)

    log(f"{len(pats)} patients prepared")
    eff_prior, eff_none = priors.prior_effects(), priors.no_effects()
    items = [CalItem(p["seeds"][0], float(p["days"][1] - p["days"][0]), float(p["days"][0]), p["schedule"],
                     p["burden"][1] / p["burden"][0], p["grid"].spacing_mm) for p in pats]
    cal_p = calibrate_batch(items, eff_prior, D=D_DEFAULT, backend=backend, device=device)
    log("calibrated with prior effects")
    cal_n = calibrate_batch(items, eff_none, D=D_DEFAULT, backend=backend, device=device)
    log("calibrated treatment-blind")

    # forecasts of later scans, one batch per model
    fc_rows, fc_items, meta = [], [], []
    for i, p in enumerate(pats):
        for k in range(1, len(p["days"]) - 1):
            dt = float(p["days"][k + 1] - p["days"][k])
            if dt < 7 or p["burden"][k] < min_burden_mm3 * 0.2:
                continue
            fc_items.append(CalItem(p["seeds"][k], dt, float(p["days"][k]), p["schedule"], 1.0, p["grid"].spacing_mm))
            meta.append((i, k))

    cal = {"prior": cal_p, "none": cal_n}
    W = [0.0, 0.25, 0.5, 0.75, 1.0]
    ok_mask = {k: np.array([st == "ok" for st in cal[k]["status"]]) for k in cal}
    rho_med = {k: float(np.median(cal[k]["rho"][ok_mask[k]])) if ok_mask[k].any() else 0.0 for k in cal}
    pred = {}            # (model, w) -> predicted ratios for every forecast item
    for key, eff in (("prior", eff_prior), ("none", eff_none)):
        for w in W:
            if not fc_items:
                continue
            rho_i = w * cal[key]["rho"] + (1 - w) * rho_med[key]
            rho = np.array([rho_i[i] for (i, _) in meta])
            delta = np.array([cal[key]["delta"][i] * w for (i, _) in meta])
            pred[(key, w)] = _forward_ratio(fc_items, rho, delta, eff, backend, device)

    log(f"forecasts done ({len(fc_items)} intervals)")
    rows = []
    for j, (i, k) in enumerate(meta):
        p = pats[i]
        obs = p["burden"][k + 1] / p["burden"][k]
        r = {"patient_uid": p["uid"], "from_tp": k, "to_tp": k + 1, "dt": float(p["days"][k + 1] - p["days"][k]),
             "obs_log_ratio": float(np.log(max(obs, 1e-6)))}
        for (key, w), v in pred.items():
            r[f"pred_{key}_w{w:g}"] = float(np.log(max(v[j], 1e-6)))
        rows.append(r)
    fc = pd.DataFrame(rows)
    metrics: dict = {}
    w_best = {"prior": 1.0, "none": 1.0}
    if len(fc):
        rate = float((fc.obs_log_ratio / fc.dt).median())
        fc["pred_cohort_median_rate"] = rate * fc.dt
        fc["pred_no_change"] = 0.0
        err = lambda c, d=fc: float((d[c] - d.obs_log_ratio).abs().mean())
        by_w = {k: {w: err(f"pred_{k}_w{w:g}") for w in W} for k in ("prior", "none")}
        for k in by_w:
            w_best[k] = min(by_w[k], key=by_w[k].get)
        # patient-grouped CV choice of the shrinkage weight, then evaluate on the held-out fold
        uids = np.array(sorted(fc.patient_uid.unique()))
        rng = np.random.default_rng(0); rng.shuffle(uids)
        fold = {u: i % 5 for i, u in enumerate(uids)}
        f = fc.patient_uid.map(fold).to_numpy()
        for k in ("prior", "none"):
            cv_err = []
            for q in range(5):
                tr, te = fc[f != q], fc[f == q]
                if len(te) == 0 or len(tr) == 0:
                    continue
                wq = min(W, key=lambda w: (tr[f"pred_{k}_w{w:g}"] - tr.obs_log_ratio).abs().mean())
                cv_err.append((te[f"pred_{k}_w{wq:g}"] - te.obs_log_ratio).abs())
            metrics[f"{k}_shrunk_cv"] = {"mae_log_ratio": float(pd.concat(cv_err).mean()),
                                         "weight_on_patient_rho_all_data": w_best[k]}
        for c in [c for c in fc.columns if c.startswith("pred_")]:
            metrics[c[5:]] = {"mae_log_ratio": err(c), "bias": float((fc[c] - fc.obs_log_ratio).mean()),
                              "direction_accuracy": float((np.sign(fc[c]) == np.sign(fc.obs_log_ratio)).mean())}
        metrics["n_forecasts"] = int(len(fc)); metrics["n_patients"] = int(fc.patient_uid.nunique())
        metrics["note"] = ("ratio forecasts of the next scan's seed burden; w = weight on the patient's own "
                           "calibrated rho vs the cohort median")
        fc.to_parquet(out / "forecasts.parquet", index=False)

    # scenarios from the last observed scan
    cases = []
    for i, p in enumerate(pats):
        last = len(p["days"]) - 1
        seed = p["seeds"][last]
        t_last = float(p["days"][last])
        w = w_best["prior"]
        rho = float(w * cal_p["rho"][i] + (1 - w) * rho_med["prior"]); delta = float(w * cal_p["delta"][i])
        sc = scenarios.scenario_set(p["schedule"], t_last, horizon)
        curves = {}
        for name, s in sc.items():
            cf = counterfactual(seed, rho=rho, delta=delta, D=D_DEFAULT, dx=p["grid"].spacing_mm, days=horizon,
                                t_start=t_last, schedule=s, effects=eff_prior, backend=backend, device=device)
            curves[name] = {"day": [round(float(x), 2) for x in cf["t"][::5]],
                            "burden_mm3": [round(float(x), 1) for x in cf["burden"][::5]],
                            "volume_mm3": [round(float(x), 1) for x in cf["volume"][::5]]}
        g = p["g"]
        if i % 20 == 0:
            log(f"scenarios {i}/{len(pats)}")
        cases.append({
            "uid": p["uid"], "status": cal_p["status"][i], "rho": rho, "delta": delta, "D": D_DEFAULT,
            "grid": {"cells": p["grid"].grid, "spacing_mm": p["grid"].spacing_mm},
            "observed": [{"tp_index": int(r.tp_index), "day": float(r.day), "tumor_mm3": float(r.tumor_mm3),
                          "core_mm3": float(r.core_mm3), "rc_mm3": float(r.rc_mm3),
                          "seed_burden_mm3": float(b)} for r, b in zip(g.itertuples(), p["burden"])],
            "calibrated_on_first_interval": {"ratio_target": items[i].ratio, "ratio_achieved": float(cal_p["achieved_ratio"][i]),
                                             "rho_treatment_blind": float(cal_n["rho"][i])},
            "recorded_schedule": p["schedule"].to_dict(), "assumptions": p["assumptions"],
            "scenarios": curves,
        })
    (out / "whatif_cases.json").write_text(json.dumps({
        "dataset": dataset, "D_mm2_per_day": D_DEFAULT, "horizon_days": horizon,
        "effects": {"source": "literature priors (see treatment/priors.py)", "values": eff_prior,
                    "evidence": {k: v["evidence"] for k, v in priors.PRIORS.items()}, "radiation": priors.RT_EVIDENCE},
        "forecast_metrics": metrics, "cases": cases, "illustrative": True}))
    return metrics, len(cases)


def _forward_ratio(items, rho, delta, effects, backend, device):
    from .calibrate import _end_burden
    # rho/delta are per-item here; reuse the batched end-burden helper
    end, b0 = _end_burden(items, rho, delta, effects, D_DEFAULT, backend, device)
    return end / np.maximum(b0, 1e-9)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--processed", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--max-patients", type=int, default=None)
    a = ap.parse_args(argv)
    m, n = run(a.dataset, a.processed, a.out, device=a.device, max_patients=a.max_patients)
    print(json.dumps({"patients": n, "forecast_metrics": m}, indent=2))


if __name__ == "__main__":
    main()
