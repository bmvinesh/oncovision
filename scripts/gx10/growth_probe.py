import json, warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from oncovision.risk import pooled as P
from sklearn.model_selection import GroupKFold
import xgboost as xgb
R="/home/asus/data/oncovision/"
df=pd.concat([
    P.load_ucsf(R+"raw/ucsf_lptdg_tables/ucsf_longitudinal_master.csv",R+"raw/ucsf_lptdg_tables/ucsf_imaging_long_clean.csv"),
    P.load_mu(R+"raw/mu_glioma_post/_metadata/MU-Glioma-Post_ClinicalData-July2025.xlsx",R+"processed/mu_glioma_post/timepoints.parquet"),
    P.load_ucsd(R+"raw/ucsd_ptgbm/_metadata/UCSD_PTGBM-clinical-information_v3_2026-12-Mar.xlsx",R+"processed/ucsd_ptgbm/timepoints.parquet")],ignore_index=True)
# first-interval log growth rate of whole-tumour volume (cavity excluded), per day
rate={}
m=pd.read_csv(R+"raw/ucsf_lptdg_tables/ucsf_longitudinal_master.csv")
for _,r in m.iterrows():
    a,b,dt=r.wt_volume_label1_plus_2_plus_3_t1,r.wt_volume_label1_plus_2_plus_3_t2,r.days_from_1st_scan_to_2nd_scan
    if dt and dt>7 and pd.notna(a) and pd.notna(b): rate["ucsf_lptdg:%d"%r.subjectid]=np.log((b+1000)/(a+1000))/dt
for ds in ["mu_glioma_post","ucsd_ptgbm"]:
    tp=pd.read_parquet(R+f"processed/{ds}/timepoints.parquet").sort_values(["patient_uid","day"])
    for uid,g in tp.groupby("patient_uid"):
        if len(g)>=2:
            dt=g.day.iloc[1]-g.day.iloc[0]
            if dt>7: rate[uid]=np.log((g.tumor_mm3.iloc[1]+1000)/(g.tumor_mm3.iloc[0]+1000))/dt
df["rate"]=df.patient_uid.map(rate); d=df[df.rate.notna()].reset_index(drop=True)
X=P.design(d); y=d.rate.to_numpy(); g=d.patient_uid.to_numpy()
print("n intervals",len(d),d.dataset.value_counts().to_dict())
pred=np.zeros(len(d)); base=np.zeros(len(d))
for tr,te in GroupKFold(5).split(X,y,g):
    m=xgb.XGBRegressor(max_depth=2,n_estimators=150,learning_rate=0.05,subsample=0.9,reg_lambda=5,n_jobs=8).fit(X.iloc[tr],y[tr])
    pred[te]=m.predict(X.iloc[te]); base[te]=np.median(y[tr])
r2=1-((y-pred)**2).sum()/((y-base)**2).sum()
print("grouped-CV: MAE model %.5f vs cohort-median %.5f  (relative improvement %.1f%%); R2 vs median-baseline %.3f; corr %.3f"%(
  np.abs(y-pred).mean(),np.abs(y-base).mean(),100*(1-np.abs(y-pred).mean()/np.abs(y-base).mean()),r2,np.corrcoef(pred,y)[0,1]))
for ds in d.dataset.unique():
    k=(d.dataset==ds).to_numpy(); print("  ",ds,"corr",np.corrcoef(pred[k],y[k])[0,1].round(3),"n",k.sum())
