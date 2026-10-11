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


def _display(a: np.ndarray) -> np.ndarray:
    """(…,64,64,64) float density -> (…,32,32,32) uint8 by 2x block mean."""
    g = a.shape[-1]
    f = g // DISPLAY
    shp = a.shape[:-3] + (DISPLAY, f, DISPLAY, f, DISPLAY, f)
    d = a.reshape(shp).mean(axis=(-5, -3, -1))
    return np.clip(np.rint(d * 255.0), 0, 255).astype(np.uint8)


def export(dataset: str, processed: str, whatif_json: str, out: str, n_patients: int = 24,
           device: str = "cpu", backend: str | None = None, horizon: float | None = None):
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
    outdir = Path(out)
    kept = []
    for c in chosen:
        uid = c["uid"]
        g = tp[tp.patient_uid == uid]
        p = prepare_patient(g, sched.get(uid))
        if p is None:
            continue
        pid = uid.split(":")[1]
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
    a = ap.parse_args(argv)
    n = export(a.dataset, a.processed, str(Path(a.whatif) / a.dataset / "whatif_cases.json"), a.out,
               a.patients, a.device)
    print(f"exported {n} patients -> {a.out}")


if __name__ == "__main__":
    main()
