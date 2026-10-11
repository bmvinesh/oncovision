# Trained models: what exists, how it was evaluated, and whether to trust it

Trained 2026-10-11 on the GX10. No deep-learning model is involved; the GPU is used for the batched PDE
calibration. Aggregate results are in `docs/results/`; fitted artefacts stay on the machine under
`~/data/oncovision/processed/` (they derive from CC BY datasets and the UCSF clinical tables).

| Model | Artefact (on the GX10) | Code | Status |
| --- | --- | --- | --- |
| Pooled mortality risk (XGBoost) | `risk/risk_model.json`, `risk/risk_drivers.json`, `risk/risk_model_card.json`, out-of-fold scores in `risk_pooled_metrics_scores.parquet` | `src/oncovision/risk/pooled.py` | trained and saved |
| Growth-rate model (XGBoost, pseudo-Huber) | `growth/growth_model.json`, `growth/growth_metrics.json`, `growth/growth_params.parquet` (out-of-fold) | `src/oncovision/growth/train.py` | trained and saved |
| Treatment-effect fit (ridge, sign-constrained) | `treatment_effects.json` | `src/oncovision/treatment/fit.py` | fitted; effects not identifiable (see `treatment-effects.md`) |
| PDE per-patient growth-rate calibration | `whatif/<dataset>/whatif_cases.json` | `src/oncovision/sim/whatif.py` | MU (150 patients), UCSD (48); LUMIERE see below |

## Mortality risk (676 patients: UCSF-LPTDG 298, MU-Glioma-Post 202, UCSD-PTGBM 176; 371 deceased)

Final model fitted on all 676 patients. Scores shown to users must be the out-of-fold ones.
Evaluation (see `docs/results/risk_pooled_metrics.json`): pooled grouped 5-fold AUC 0.74; within UCSF 0.78, MU 0.66,
UCSD 0.69; leave-one-dataset-out UCSF 0.72, MU 0.56, UCSD 0.46. Mean absolute SHAP: enhancing volume 0.63, grade 0.30,
whole-tumour volume 0.27, cavity volume 0.23, IDH mutant 0.21. The volume features differ systematically between
cohorts (different segmentation tools, different follow-up), which is a likely reason for the weak transfer. Treat the
score as a within-cohort prognostic rank, not as a calibrated probability.

## Growth rate (1112 intervals, 565 patients: LUMIERE 429, MU 333, UCSF 298, UCSD 52)

Target: ln((V1+1000)/(V0+1000))/dt of the whole-tumour volume, cavity excluded. Features at interval start (age, sex,
grade, IDH, MGMT, region volumes), time since first scan, interval length, planned drug and radiation exposure
(MU, UCSD).

| | MAE of log volume ratio | Spearman | Direction accuracy (dead band ±5%) |
| --- | --- | --- | --- |
| model, grouped 5-fold CV (5 repeats) | 0.475 | 0.41 | 0.67 |
| cohort-median rate | 0.487 | n/a | 0.61 |
| no change | 0.487 | n/a | n/a |
| model without treatment features | 0.475 | 0.41 | 0.67 |

Within a cohort the model ranks patients modestly better than chance (Spearman 0.25 UCSF, 0.38 MU, 0.28 UCSD, 0.49
LUMIERE) and beats the median baseline in MU and LUMIERE, not in UCSF or UCSD. Leave-one-dataset-out it beats the
median baseline in **1 of 4** datasets, so `usable_across_cohorts` is false: the signal does not transfer. Treatment
features add nothing. Most of the signal is plausibly regression to the mean (large starting volumes shrink, small
ones grow) together with grade; mean gain by feature is led by grade 0.14, whole-tumour volume 0.12, necrotic volume 0.09.

## PDE what-if calibration

| Dataset | Patients calibrated | Status | Forecasts | Model MAE (CV, with treatment / blind) | Cohort-median MAE | No-change MAE |
| --- | --- | --- | --- | --- | --- | --- |
| MU-Glioma-Post | 150 | 75 ok, 71 shrinking, 4 no convergence | 226 | 0.88 / 0.83 | 0.55 | 0.56 |
| UCSD-PTGBM | 48 | 32 ok, 16 shrinking | 16 | 0.94 / 0.85 | 0.74 | 0.66 |

The PDE forecast does not beat trivial baselines in either cohort (UCSD has only 16 forecasts). Including the
recorded treatment schedule does not help. Use the what-if as an illustration of mechanism, as labelled in the viewer.

## Not done

* A deep-learning growth model (3D CNN on the label volume) was not attempted; tabular features did not beat the
  baseline across cohorts, and 565 patients with noisy automated masks are unlikely to support one.
* Calibrated uncertainty, and any clinical validation.
