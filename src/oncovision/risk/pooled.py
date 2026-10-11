"""Mortality-risk model pooled over UCSF-LPTDG, MU-Glioma-Post and UCSD-PTGBM.

Same task as the OncoPulse model (``deceased`` from baseline clinical + molecular + tumour-volume
features) with three datasets instead of one, evaluated three ways:

* ``within_dataset``  : repeated 5-fold CV inside each dataset (the OncoPulse protocol);
* ``pooled``          : 5-fold CV over all patients, folds grouped by patient;
* ``leave_one_dataset_out`` : train on two datasets, test on the third (external validation).

Baseline features only.  ``deceased`` means a death was recorded during follow-up, so it is
not comparable across cohorts with different follow-up; read the leave-one-dataset-out numbers
as the honest ones.  UCSF is read from the committed OncoPulse tables (``ucsf_*`` CSVs); MU and
UCSD from the ingest outputs and the clinical sheets.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

FEATURES_NUM = ["age", "grade", "ncr_mm3", "edema_mm3", "et_mm3", "rc_mm3", "tumor_mm3"]
FEATURES_CAT = ["sex", "idh", "mgmt"]


def _norm_idh(v):
    s = str(v).strip().lower()
    if s in ("wt", "wild type", "wildtype", "0"):
        return "wt"
    if s in ("mut", "mutant", "1", "mutated"):
        return "mut"
    return None


def _norm_mgmt(v):
    s = str(v).strip().lower()
    if s in ("positive", "methylated", "1"):
        return "methylated"
    if s in ("negative", "unmethylated", "not methylated", "0"):
        return "unmethylated"
    return None


def load_ucsf(master_csv: str, long_csv: str) -> pd.DataFrame:
    m = pd.read_csv(master_csv)
    il = pd.read_csv(long_csv)
    base = il[il["timepoint"] == 1][["subjectid", "patient_age", "patient_sex"]]
    m = m.merge(base, on="subjectid", how="left")
    return pd.DataFrame({
        "dataset": "ucsf_lptdg", "patient_uid": "ucsf_lptdg:" + m.subjectid.astype(str),
        "age": pd.to_numeric(m.patient_age, errors="coerce"),
        "sex": m.patient_sex.astype(str).str.upper().str[0].where(lambda s: s.isin(["M", "F"])),
        "grade": pd.to_numeric(m.grade, errors="coerce"),
        "idh": m.idh.map(_norm_idh), "mgmt": m.mgmt.map(_norm_mgmt),
        "ncr_mm3": m.ncr_volume_label1_t1, "edema_mm3": m.snfh_volume_label2_t1, "et_mm3": m.et_volume_label3_t1,
        "rc_mm3": m.rc_volume_label4_t1, "tumor_mm3": m.wt_volume_label1_plus_2_plus_3_t1,
        "deceased": m.days_from_death_to_1st_scan.notna().astype(int),
    })


def _baseline(tp_parquet: str) -> pd.DataFrame:
    tp = pd.read_parquet(tp_parquet)
    b = tp.sort_values(["patient_uid", "day"]).groupby("patient_uid").first().reset_index()
    return b[["patient_uid", "patient_id", "ncr_mm3", "edema_mm3", "et_mm3", "rc_mm3", "tumor_mm3"]]


def load_mu(clinical_xlsx: str, tp_parquet: str) -> pd.DataFrame:
    d = pd.read_excel(clinical_xlsx, sheet_name="MU Glioma Post")
    d.columns = [c.strip() for c in d.columns]
    b = _baseline(tp_parquet)
    d["patient_id"] = d.Patient_ID.astype(str)
    m = d.merge(b, on="patient_id", how="inner")
    idh = m["IDH1 mutation"].map({0: "wt", 1: "mut"})
    mg = m["MGMT methylation"].map({0: "unmethylated", 1: "methylated"})
    return pd.DataFrame({
        "dataset": "mu_glioma_post", "patient_uid": m.patient_uid,
        "age": pd.to_numeric(m["Age at diagnosis"], errors="coerce"),
        "sex": m["Sex at Birth"].astype(str).str.upper().str[0].where(lambda s: s.isin(["M", "F"])),
        "grade": pd.to_numeric(m["Grade of Primary Brain Tumor"], errors="coerce"),
        "idh": idh, "mgmt": mg,
        **{c: m[c] for c in ["ncr_mm3", "edema_mm3", "et_mm3", "rc_mm3", "tumor_mm3"]},
        "deceased": pd.to_numeric(m["Overall Survival (Death)"], errors="coerce"),
    })


def load_ucsd(clinical_xlsx: str, tp_parquet: str) -> pd.DataFrame:
    d = pd.read_excel(clinical_xlsx, sheet_name="Cinical info")
    d.columns = [c.strip() for c in d.columns]
    d["patient_id"] = d.ID.astype(str).str.extract(r"UCSD-PTGBM-(\d+)_")[0]
    first = d.sort_values("ID").groupby("patient_id").first().reset_index()
    b = _baseline(tp_parquet)
    m = first.merge(b, on="patient_id", how="inner")
    death = pd.to_numeric(m["Days from Acquisition to Date of death"], errors="coerce")
    follow = pd.to_numeric(m["Days from Acquisition to Date of last follow-up"], errors="coerce")
    dec = np.where(death.notna(), 1.0, np.where(follow.notna(), 0.0, np.nan))
    return pd.DataFrame({
        "dataset": "ucsd_ptgbm", "patient_uid": m.patient_uid,
        "age": pd.to_numeric(m["Patient's Age"], errors="coerce"),
        "sex": m["Sex at birth"].astype(str).str.upper().str[0].where(lambda s: s.isin(["M", "F"])),
        "grade": pd.to_numeric(m["Grade"], errors="coerce"),
        "idh": m["IDH"].map(_norm_idh), "mgmt": m["MGMT"].map(_norm_mgmt),
        **{c: m[c] for c in ["ncr_mm3", "edema_mm3", "et_mm3", "rc_mm3", "tumor_mm3"]},
        "deceased": dec,
    })


def design(df: pd.DataFrame) -> pd.DataFrame:
    X = df[FEATURES_NUM].apply(pd.to_numeric, errors="coerce").copy()
    for c in FEATURES_CAT:
        X = pd.concat([X, pd.get_dummies(df[c].astype("object"), prefix=c, dtype=float)], axis=1)
    # fixed column set so train/test line up
    for c in ["sex_F", "sex_M", "idh_mut", "idh_wt", "mgmt_methylated", "mgmt_unmethylated"]:
        if c not in X:
            X[c] = 0.0
    return X[FEATURES_NUM + ["sex_F", "sex_M", "idh_mut", "idh_wt", "mgmt_methylated", "mgmt_unmethylated"]]


def _model(seed=17):
    import xgboost as xgb
    return xgb.XGBClassifier(objective="binary:logistic", max_depth=3, learning_rate=0.05, subsample=0.9,
                             colsample_bytree=0.8, n_estimators=300, reg_lambda=2.0, tree_method="hist",
                             random_state=seed, n_jobs=8)


def evaluate(df: pd.DataFrame, repeats: int = 5, seed: int = 17) -> dict:
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import StratifiedKFold, cross_val_predict

    df = df[df.deceased.notna()].reset_index(drop=True)
    y = df.deceased.astype(int).to_numpy()
    X = design(df)
    out: dict = {"n": int(len(df)), "n_by_dataset": df.dataset.value_counts().to_dict(),
                 "deceased_rate_by_dataset": df.groupby("dataset").deceased.mean().round(3).to_dict()}

    def cv_auc(Xs, ys, strat):
        aucs, oof = [], []
        for s in range(repeats):
            cv = StratifiedKFold(5, shuffle=True, random_state=seed + s)
            p = cross_val_predict(_model(seed), Xs, ys, cv=cv.split(Xs, strat), method="predict_proba")[:, 1]
            aucs.append(roc_auc_score(ys, p)); oof.append(p)
        return float(np.mean(aucs)), float(np.std(aucs)), np.mean(oof, axis=0)

    within = {}
    for ds, g in df.groupby("dataset"):
        idx = g.index.to_numpy()
        if len(np.unique(y[idx])) < 2 or len(idx) < 40:
            continue
        a, s, _ = cv_auc(X.iloc[idx], y[idx], y[idx])
        within[ds] = {"auc": a, "std": s, "n": int(len(idx))}
    out["within_dataset"] = within
    strat = df.dataset + "_" + df.deceased.astype(int).astype(str)
    a, s, oof = cv_auc(X, y, strat)
    out["pooled"] = {"auc": a, "std": s}
    df["risk_oof"] = oof
    loo = {}
    for ds in df.dataset.unique():
        te = df.dataset == ds
        if te.sum() < 30 or len(np.unique(y[te])) < 2:
            continue
        m = _model(seed).fit(X[~te], y[~te])
        p = m.predict_proba(X[te])[:, 1]
        loo[ds] = {"auc": float(roc_auc_score(y[te], p)), "n_test": int(te.sum()), "n_train": int((~te).sum())}
    out["leave_one_dataset_out"] = loo
    # simple reference: IDH status alone
    ref = {}
    for ds, g in df.groupby("dataset"):
        s = (g.idh == "wt").astype(float)
        if g.idh.notna().sum() > 30 and len(np.unique(g.deceased)) > 1:
            ref[ds] = float(roc_auc_score(g.deceased.astype(int)[g.idh.notna()], s[g.idh.notna()]))
    out["idh_wild_type_alone_auc"] = ref
    return out, df


def fit_final(df: pd.DataFrame, out_dir: str, oof: pd.DataFrame | None = None, top_k: int = 6) -> dict:
    """Fit the pooled model on every patient with a known outcome and save it.

    Writes ``risk_model.json`` (XGBoost native format, not pickle), ``risk_drivers.json`` (top SHAP
    drivers per patient; explanations of the final model on its own training patients) and
    ``risk_model_card.json``.  Per-patient scores shown to users must be the out-of-fold ones
    (``oof``), not the in-sample predictions of the final model.
    """
    import shap

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    d = df[df.deceased.notna()].reset_index(drop=True)
    X, y = design(d), d.deceased.astype(int).to_numpy()
    model = _model().fit(X, y)
    model.save_model(str(out / "risk_model.json"))
    sv = shap.TreeExplainer(model).shap_values(X)
    names = list(X.columns)
    drivers = {}
    for i, uid in enumerate(d.patient_uid):
        idx = np.argsort(-np.abs(sv[i]))[:top_k]
        drivers[uid] = [{"feature": names[j], "value": None if pd.isna(X.iloc[i, j]) else float(X.iloc[i, j]),
                         "shap": float(sv[i][j])} for j in idx]
    (out / "risk_drivers.json").write_text(json.dumps(drivers))
    imp = pd.Series(np.abs(sv).mean(axis=0), index=names).sort_values(ascending=False)
    card = {
        "model": "xgboost binary:logistic, max_depth 3, 300 trees", "target": "deceased (death recorded during follow-up)",
        "features": names, "n_train": int(len(d)), "n_deceased": int(y.sum()),
        "trained_on": d.dataset.value_counts().to_dict(),
        "mean_abs_shap": {k: float(v) for k, v in imp.items()},
        "scores": "use out-of-fold risk (risk_pooled_metrics_scores.parquet) for display; "
                  "the saved model is for scoring new patients",
        "caveat": "relative prognostic rank; follow-up differs by cohort; weak external transfer "
                  "(see risk_pooled_metrics.json leave_one_dataset_out)",
    }
    (out / "risk_model_card.json").write_text(json.dumps(card, indent=2))
    return card


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--processed", required=True)
    ap.add_argument("--raw", required=True)
    ap.add_argument("--ucsf-master", required=True)
    ap.add_argument("--ucsf-long", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model-out", help="directory for the final fitted model, SHAP drivers and model card")
    a = ap.parse_args(argv)
    P, R = Path(a.processed), Path(a.raw)
    df = pd.concat([
        load_ucsf(a.ucsf_master, a.ucsf_long),
        load_mu(str(R / "mu_glioma_post/_metadata/MU-Glioma-Post_ClinicalData-July2025.xlsx"),
                str(P / "mu_glioma_post/timepoints.parquet")),
        load_ucsd(str(R / "ucsd_ptgbm/_metadata/UCSD_PTGBM-clinical-information_v3_2026-12-Mar.xlsx"),
                  str(P / "ucsd_ptgbm/timepoints.parquet")),
    ], ignore_index=True)
    res, scored = evaluate(df)
    if a.model_out:
        res["final_model"] = fit_final(df, a.model_out, scored)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=2))
    scored[["patient_uid", "dataset", "deceased", "risk_oof"]].to_parquet(str(a.out).replace(".json", "_scores.parquet"))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
