"""3D frame stacks for the viewer: observed seeds and every what-if scenario.

    python -m oncovision.sim.export_frames --dataset mu_glioma_post \\
        --processed ~/data/oncovision/processed --whatif ~/data/oncovision/processed/whatif \\
        --out ~/data/oncovision/processed/viewer --patients 24 --device cuda

For each selected patient writes (density * 255, uint8, order TZYX, 32^3 display grid):

    <out>/whatif/<patient_id>/observed.u8            one frame per observed scan
    <out>/whatif/<patient_id>/<scenario>.u8          26 frames from the last scan to the horizon

and ``<out>/whatif_cases.json``: the what-if cases for the exported patients with a ``frames`` block
added to ``observed`` (as ``observed_frames``) and to every scenario.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..treatment import priors, scenarios
from ..treatment.schedule import Schedule
from .calibrate import counterfactual
from .whatif import D_DEFAULT, prepare_patient

N_FRAMES = 26
DISPLAY = 32


def build_model_card(treatment_effects: dict, risk: dict, forecast: dict) -> dict:
    """Numbers shown on the model card, taken from saved measurement files (never typed by hand)."""
    ci = treatment_effects.get("ci95", {})
    kill = {}
    for a, v in treatment_effects.get("kill_per_day", {}).items():
        n_exp = treatment_effects.get("exposed_intervals", {}).get(a, 0)
        if n_exp < 8:
            continue      # agent was not fitted (too few exposed intervals)
        kill[a] = {"value": v, "ci95": ci.get(a), "exposed_intervals": n_exp}
    cv = treatment_effects.get("cross_validation", {})
    tf = {"measure": treatment_effects.get("measure"), "n_intervals": treatment_effects.get("n_intervals_used"),
          "n_patients": treatment_effects.get("n_patients"), "kill_per_day": kill,
          "radiation_k_per_gy": {"value": treatment_effects.get("k_per_gy"), "ci95": ci.get("radiation")},
          "cv_vs_no_treatment_baseline_pct": None if "relative_improvement" not in cv
          else round(100 * cv["relative_improvement"], 1),
          "ci_level": 0.95, "caveat": treatment_effects.get("caveat")}
    rk = {"n": risk.get("n"), "n_by_dataset": risk.get("n_by_dataset"),
          "deceased_rate_by_dataset": risk.get("deceased_rate_by_dataset"),
          "pooled": risk.get("pooled"), "within_dataset": risk.get("within_dataset"),
          "leave_one_dataset_out": risk.get("leave_one_dataset_out"),
          "idh_wild_type_alone_auc": risk.get("idh_wild_type_alone_auc")}
    return {"treatment_fit": tf, "risk": rk, "forecast": forecast}


def legacy_forecast_metrics(fm: dict) -> dict:
    """Collapse the full what-if metrics into the four rows the viewer shows.

    Model rows use the cross-validated shrinkage choice for MAE (the honest number) and the
    all-data best weight for bias and direction accuracy."""
    out = {}
    for row, key in (("with_treatment", "prior"), ("treatment_blind", "none")):
        cv = fm.get(f"{key}_shrunk_cv")
        if not cv:
            continue
        w = cv["weight_on_patient_rho_all_data"]
        full = fm.get(f"{key}_w{w:g}", {})
        out[row] = {"mae_log_ratio": cv["mae_log_ratio"], "bias": full.get("bias"),
                    "direction_accuracy": full.get("direction_accuracy"),
                    "weight_on_patient_rho": w}
    for k in ("cohort_median_rate", "no_change"):
        if k in fm:
            out[k] = fm[k]
    out["n_forecasts"] = fm.get("n_forecasts"); out["n_patients"] = fm.get("n_patients")
    out["note"] = ("mean absolute error of the log burden ratio for the next scan; model rows are "
                   "cross-validated by patient")
    return out


def _clinical_lookup(dataset: str, clinical_xlsx: str | None) -> dict:
    if not clinical_xlsx or dataset != "mu_glioma_post":
        return {}
    d = pd.read_excel(clinical_xlsx, sheet_name="MU Glioma Post")
    d.columns = [c.strip() for c in d.columns]
    out = {}
    for _, r in d.iterrows():
        idh = {0: "wt", 1: "mut"}.get(r["IDH1 mutation"])
        mg = {0: "unmethylated", 1: "methylated"}.get(r["MGMT methylation"])
        g = pd.to_numeric(r["Grade of Primary Brain Tumor"], errors="coerce")
        age = pd.to_numeric(r["Age at diagnosis"], errors="coerce")
        sex = {"male": "M", "female": "F"}.get(str(r["Sex at Birth"]).strip().lower())
        out[f"{dataset}:{r['Patient_ID']}"] = {
            "age_years": None if pd.isna(age) else float(age), "sex": sex,
            "who_grade": None if pd.isna(g) else float(g), "idh": idh, "mgmt": mg,
            "diagnosis": None if pd.isna(r["Primary Diagnosis"]) else str(r["Primary Diagnosis"])}
    return out


def _display(a: np.ndarray) -> np.ndarray:
    """(…,64,64,64) float density -> (…,32,32,32) uint8 by 2x block mean."""
    g = a.shape[-1]
    f = g // DISPLAY
    shp = a.shape[:-3] + (DISPLAY, f, DISPLAY, f, DISPLAY, f)
    d = a.reshape(shp).mean(axis=(-5, -3, -1))
    return np.clip(np.rint(d * 255.0), 0, 255).astype(np.uint8)


def export(dataset: str, processed: str, whatif_json: str, out: str, n_patients: int = 24,
           device: str = "cpu", backend: str | None = None, horizon: float | None = None,
           clinical_xlsx: str | None = None, treatment_effects_json: str | None = None,
           risk_json: str | None = None):
    backend = backend or ("torch" if device != "cpu" else "numpy")
    cases_doc = json.loads(Path(whatif_json).read_text())
    horizon = horizon or cases_doc["horizon_days"]
    tp = pd.read_parquet(Path(processed) / dataset / "timepoints.parquet")
    sched = json.loads((Path(processed) / dataset / "treatments.json").read_text())
    eff = priors.prior_effects()
    # prefer patients with >=3 scans and a cleanly calibrated rho
    cand = [c for c in cases_doc["cases"] if c["status"] == "ok"]
    cand.sort(key=lambda c: (-len(c["observed"]), c["uid"]))
    chosen = cand[:n_patients]
    clin = _clinical_lookup(dataset, clinical_xlsx)
    outdir = Path(out)
    kept = []
    for c in chosen:
        uid = c["uid"]
        g = tp[tp.patient_uid == uid]
        p = prepare_patient(g, sched.get(uid))
        if p is None:
            continue
        pid = uid.split(":")[1]
        c["clinical"] = clin.get(uid, {})
        c["frames_box_mm"] = [float(p["grid"].spacing_mm * p["grid"].grid)] * 3   # same box as the 64^3 grid
        d = outdir / "whatif" / pid
        d.mkdir(parents=True, exist_ok=True)
        obs = _display(np.stack(p["seeds"]))
        obs.tofile(d / "observed.u8")
        c["observed_frames"] = {"url": f"whatif/{pid}/observed.u8", "shape": list(obs.shape), "order": "TZYX",
                                "dtype": "uint8", "max_value": 255, "days": [float(x) for x in p["days"]]}
        last = len(p["days"]) - 1
        t_last = float(p["days"][last])
        sc = scenarios.scenario_set(p["schedule"], t_last, horizon)
        for name, s in sc.items():
            cf = counterfactual(p["seeds"][last], rho=c["rho"], delta=c["delta"], D=D_DEFAULT,
                                dx=p["grid"].spacing_mm, days=horizon, t_start=t_last, schedule=s, effects=eff,
                                n_frames=N_FRAMES, backend=backend, device=device)
            fr = _display(cf["frames"])
            fr.tofile(d / f"{name}.u8")
            c["scenarios"][name]["frames"] = {
                "url": f"whatif/{pid}/{name}.u8", "shape": list(fr.shape), "order": "TZYX", "dtype": "uint8",
                "max_value": 255, "days": [round(float(x), 2) for x in cf["frame_t"]]}
        kept.append(c)
    cases_doc["cases"] = kept
    fm = cases_doc.get("forecast_metrics", {})
    cases_doc["forecast_metrics"] = legacy_forecast_metrics(fm)
    if treatment_effects_json and risk_json:
        cases_doc["model_card"] = build_model_card(json.loads(Path(treatment_effects_json).read_text()),
                                                   json.loads(Path(risk_json).read_text()),
                                                   cases_doc["forecast_metrics"])
    cases_doc["frames"] = {"display_grid": DISPLAY, "n_frames": N_FRAMES, "note":
                           "density seed x 255; illustrative, MRI does not measure cell density"}
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "whatif_cases.json").write_text(json.dumps(cases_doc))
    return len(kept)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--processed", required=True)
    ap.add_argument("--whatif", required=True, help="dir containing <dataset>/whatif_cases.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--patients", type=int, default=24)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--clinical", help="MU clinical xlsx (adds age/sex/grade/IDH/MGMT per case)")
    ap.add_argument("--treatment-effects", help="treatment_effects*.json from fit_treatment.py")
    ap.add_argument("--risk", help="risk_pooled_metrics.json")
    a = ap.parse_args(argv)
    n = export(a.dataset, a.processed, str(Path(a.whatif) / a.dataset / "whatif_cases.json"), a.out,
               a.patients, a.device, clinical_xlsx=a.clinical, treatment_effects_json=a.treatment_effects,
               risk_json=a.risk)
    print(f"exported {n} patients -> {a.out}")


if __name__ == "__main__":
    main()
