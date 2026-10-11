"""Fit treatment effect sizes from processed volumes.

    python scripts/gx10/fit_treatment.py --processed ~/data/oncovision/processed \
        --datasets mu_glioma_post --out ~/data/oncovision/processed/treatment_effects.json
"""
import argparse
import json
from pathlib import Path

import pandas as pd

from oncovision.treatment import fit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--processed", required=True)
    ap.add_argument("--datasets", nargs="+", default=["mu_glioma_post"])
    ap.add_argument("--measure", default="core_mm3", choices=["core_mm3", "tumor_mm3", "cellular_mm3"])
    ap.add_argument("--n-boot", type=int, default=200)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    tps, sched = [], {}
    for ds in a.datasets:
        d = Path(a.processed) / ds
        tps.append(pd.read_parquet(d / "timepoints.parquet"))
        sched.update(json.loads((d / "treatments.json").read_text()))
    tp = pd.concat(tps, ignore_index=True)
    out = fit.run(tp, sched, a.out, measure=a.measure, n_boot=a.n_boot)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
