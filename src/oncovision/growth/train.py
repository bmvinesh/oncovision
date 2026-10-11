"""Growth-rate model: predict a patient's tumour growth rate over the next interval from what is
known at the start of the interval.

    python -m oncovision.growth.train --processed $R/processed --raw $R/raw --out $R/processed/growth

One row per scan interval across UCSF-LPTDG (one interval per patient, from the committed OncoPulse
table), MU-Glioma-Post, UCSD-PTGBM and LUMIERE.  Target: ``ln((V1+c)/(V0+c))/dt`` of the whole-tumour
volume (resection cavity excluded).  Features: age, sex, grade, IDH, MGMT, region volumes at the
start of the interval, time since the first scan, interval length, and (MU, UCSD only) the planned
exposure to temozolomide, bevacizumab, TTFields and radiation inside the interval.

Evaluation (all grouped by patient, so no patient appears in both train and test):

* 5-fold grouped CV, repeated;
* leave-one-dataset-out;
* the same model without treatment features;
* baselines: cohort median rate, no change.

The model is judged against the cohort-median baseline.  If it does not beat it, ``growth_metrics.json``
says so and ``usable`` is false; nothing downstream should present the prediction as skilled.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..risk import pooled as P
from ..treatment.agents import DRUG_KEYS
from ..treatment.schedule import Schedule

FLOOR = 1000.0
MIN_DT = 7.0
BASE_FEATURES = P.FEATURES_NUM + ["sex_F", "sex_M", "idh_mut", "idh_wt", "mgmt_methylated", "mgmt_unmethylated"]
TIME_FEATURES = ["t_start", "dt", "interval_index"]
TREATMENT_FEATURES = [f"x_{a}" for a in DRUG_KEYS] + ["rt_gy"]


def _lumiere_clinical(csv: str) -> pd.DataFrame:
    d = pd.read_csv(csv)
    c = {k: next(x for x in d.columns if x.lower().startswith(k)) for k in ("patient", "sex", "age", "idh", "mgmt qual")}
    out = pd.DataFrame({"patient_id": d[c["patient"]].astype(str),
                        "age": pd.to_numeric(d[c["age"]], errors="coerce"),
                        "sex": d[c["sex"]].astype(str).str.lower().map({"male": "M", "female": "F"}),
                        "grade": 4.0,   # glioblastoma cohort
                        "idh": d[c["idh"]].astype(str).map(lambda v: "wt" if v.strip().upper() == "WT" else None),
                        "mgmt": d[c["mgmt qual"]].astype(str).str.lower().map(
                            {"methylated": "methylated", "not methylated": "unmethylated"})})
    return out


def _intervals_from_timepoints(tp: pd.DataFrame, sched: dict, clin: pd.DataFrame, dataset: str) -> pd.DataFrame:
    rows = []
    cl = clin.set_index("patient_uid") if "patient_uid" in clin else clin.set_index("patient_id")
    for uid, g in tp.sort_values(["patient_uid", "day"]).groupby("patient_uid"):
        g = g.reset_index(drop=True)
        s = Schedule.from_dict(sched[uid]["schedule"]) if uid in sched else None
        surg = (sched.get(uid) or {}).get("surgery_days", []) or []
        key = uid if "patient_uid" in clin else g.patient_id.iloc[0]
        if key not in cl.index:
            continue
        c = cl.loc[key]
        for i in range(len(g) - 1):
            a, b = g.iloc[i], g.iloc[i + 1]
            dt = float(b.day - a.day)
            if dt < MIN_DT:
                continue
            if any(a.day <= x <= b.day for x in surg):
                continue
            if b.rc_mm3 > max(2.0 * a.rc_mm3, a.rc_mm3 + 3000.0):
                continue                      # probably a resection in between
            r = {"patient_uid": uid, "dataset": dataset, "interval_index": i, "t_start": float(a.day), "dt": dt,
                 "age": c.get("age"), "sex": c.get("sex"), "grade": c.get("grade"), "idh": c.get("idh"),
                 "mgmt": c.get("mgmt"), "ncr_mm3": a.ncr_mm3, "edema_mm3": a.edema_mm3, "et_mm3": a.et_mm3,
                 "rc_mm3": a.rc_mm3, "tumor_mm3": a.tumor_mm3,
                 "rate": float(np.log((b.tumor_mm3 + FLOOR) / (a.tumor_mm3 + FLOOR)) / dt)}
            if s is not None:
                for k in DRUG_KEYS:
                    r[f"x_{k}"] = s.exposure_days_between(k, float(a.day), float(b.day))
                r["rt_gy"] = s.radiation_dose_between(float(a.day), float(b.day))
            rows.append(r)
    return pd.DataFrame(rows)


def build_table(processed: str, raw: str, ucsf_master: str, ucsf_long: str) -> pd.DataFrame:
    Pp, R = Path(processed), Path(raw)
    parts = []
    # UCSF: the committed OncoPulse table has one interval per patient
    u = P.load_ucsf(ucsf_master, ucsf_long)
    m = pd.read_csv(ucsf_master)
    m["patient_uid"] = "ucsf_lptdg:" + m.subjectid.astype(str)
    m = m.rename(columns={"days_from_1st_scan_to_2nd_scan": "dt"})
    m = m[(m.dt > MIN_DT)]
    a = m.wt_volume_label1_plus_2_plus_3_t1; b = m.wt_volume_label1_plus_2_plus_3_t2
    m = m.assign(rate=np.log((b + FLOOR) / (a + FLOOR)) / m.dt)
    uc = u.merge(m[["patient_uid", "dt", "rate"]], on="patient_uid")
    uc["t_start"] = 0.0; uc["interval_index"] = 0
    parts.append(uc.drop(columns=["deceased"]))
    for ds, xlsx, loader in (
        ("mu_glioma_post", R / "mu_glioma_post/_metadata/MU-Glioma-Post_ClinicalData-July2025.xlsx", P.load_mu),
        ("ucsd_ptgbm", R / "ucsd_ptgbm/_metadata/UCSD_PTGBM-clinical-information_v3_2026-12-Mar.xlsx", P.load_ucsd),
    ):
        tpf = str(Pp / ds / "timepoints.parquet")
        clin = loader(str(xlsx), tpf)
        tp = pd.read_parquet(tpf)
        sched = json.loads((Pp / ds / "treatments.json").read_text())
        parts.append(_intervals_from_timepoints(tp, sched, clin, ds))
    tp = pd.read_parquet(Pp / "lumiere" / "timepoints.parquet")
    tp = tp[~tp.qc_flags.str.contains("pre_op", na=False)]
    lc = _lumiere_clinical(str(R / "lumiere/LUMIERE-Demographics_Pathology.csv"))
    parts.append(_intervals_from_timepoints(tp, {}, lc, "lumiere"))
    df = pd.concat(parts, ignore_index=True)
    return df[df.rate.notna()].reset_index(drop=True)


def design(df: pd.DataFrame, treatment: bool = True) -> pd.DataFrame:
    X = P.design(df)[BASE_FEATURES].copy()
    for c in TIME_FEATURES:
        X[c] = pd.to_numeric(df[c], errors="coerce").to_numpy()
    if treatment:
        for c in TREATMENT_FEATURES:
            X[c] = pd.to_numeric(df[c], errors="coerce").to_numpy() if c in df else np.nan
    return X


def _reg(seed=17):
    import xgboost as xgb
    return xgb.XGBRegressor(objective="reg:pseudohubererror", huber_slope=0.01, max_depth=2, n_estimators=200,
                            learning_rate=0.04, subsample=0.9, colsample_bytree=0.8, reg_lambda=5.0,
                            min_child_weight=5, tree_method="hist", random_state=seed, n_jobs=8)


def _metrics(y, pred, dt, base_rate):
    from scipy.stats import spearmanr
    lr_true, lr_pred = y * dt, pred * dt
    dead = 0.05
    moved = np.abs(lr_true) > dead
    return {
        "n": int(len(y)),
        "mae_log_ratio": float(np.mean(np.abs(lr_true - lr_pred))),
        "baseline_cohort_median_mae": float(np.mean(np.abs(lr_true - base_rate * dt))),
        "baseline_no_change_mae": float(np.mean(np.abs(lr_true))),
        "spearman": float(spearmanr(pred, y).statistic) if len(y) > 5 else None,
        "direction_accuracy": float(np.mean(np.sign(lr_pred[moved]) == np.sign(lr_true[moved]))) if moved.any() else None,
        "direction_accuracy_cohort_median": float(np.mean(np.sign(base_rate * dt[moved]) == np.sign(lr_true[moved])))
        if moved.any() else None,
    }


def evaluate(df: pd.DataFrame, repeats: int = 5, seed: int = 17) -> tuple[dict, np.ndarray]:
    from sklearn.model_selection import GroupKFold

    y = df.rate.to_numpy(); dt = df.dt.to_numpy(); g = df.patient_uid.to_numpy()
    uniq = np.array(sorted(set(g)))
    out: dict = {}
    oof_all = {}
    for name, treat in (("with_treatment_features", True), ("without_treatment_features", False)):
        X = design(df, treat)
        oofs, base = [], []
        for r in range(repeats):
            rng = np.random.default_rng(seed + r)
            perm = rng.permutation(len(uniq)); fold_of = {u: perm[i] % 5 for i, u in enumerate(uniq)}
            f = np.array([fold_of[u] for u in g])
            pred = np.zeros(len(y)); b = np.zeros(len(y))
            for k in range(5):
                tr, te = f != k, f == k
                m = _reg(seed + r).fit(X[tr], y[tr])
                pred[te] = m.predict(X[te]); b[te] = np.median(y[tr])
            oofs.append(pred); base.append(b)
        pred = np.mean(oofs, axis=0); b = np.mean(base, axis=0)
        mm = _metrics(y, pred, dt, b)
        mm["by_dataset"] = {}
        for ds in df.dataset.unique():
            k = (df.dataset == ds).to_numpy()
            if k.sum() >= 20:
                mm["by_dataset"][ds] = _metrics(y[k], pred[k], dt[k], b[k])
        out[name] = mm
        oof_all[name] = pred
    # leave-one-dataset-out (with treatment features)
    X = design(df, True)
    lodo = {}
    for ds in df.dataset.unique():
        te = (df.dataset == ds).to_numpy()
        if te.sum() < 30:
            continue
        m = _reg(seed).fit(X[~te], y[~te])
        lodo[ds] = _metrics(y[te], m.predict(X[te]), dt[te], np.median(y[~te]))
    out["leave_one_dataset_out"] = lodo
    w = out["with_treatment_features"]
    out["usable"] = bool(w["mae_log_ratio"] < w["baseline_cohort_median_mae"])
    out["verdict"] = ("model beats the cohort-median baseline" if out["usable"] else
                      "model does NOT beat the cohort-median baseline: do not present its predictions as skilled")
    return out, oof_all["with_treatment_features"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--processed", required=True)
    ap.add_argument("--raw", required=True)
    ap.add_argument("--ucsf-master", required=True)
    ap.add_argument("--ucsf-long", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    df = build_table(a.processed, a.raw, a.ucsf_master, a.ucsf_long)
    metrics, oof = evaluate(df)
    metrics["n_intervals"] = int(len(df)); metrics["n_patients"] = int(df.patient_uid.nunique())
    metrics["n_by_dataset"] = df.dataset.value_counts().to_dict()
    metrics["target"] = "ln((V1+1000)/(V0+1000))/dt, whole tumour without cavity, per day"
    X = design(df, True)
    final = _reg().fit(X, df.rate.to_numpy())
    final.save_model(str(out / "growth_model.json"))
    metrics["features"] = list(X.columns)
    (out / "growth_metrics.json").write_text(json.dumps(metrics, indent=2))
    df.assign(rate_pred_oof=oof)[["patient_uid", "dataset", "interval_index", "t_start", "dt", "rate", "rate_pred_oof"]] \
        .to_parquet(out / "growth_params.parquet", index=False)
    print(json.dumps({k: v for k, v in metrics.items() if k != "features"}, indent=2))


if __name__ == "__main__":
    main()
