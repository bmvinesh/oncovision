# Treatment effects and the what-if: method and measured results

Measured 2026-10-11 on the GX10. Aggregates only; patient-level tables stay on the machine.
Raw numbers: `docs/results/`.

## What was built

| Piece | Code |
| --- | --- |
| Solver with time-varying kill, growth suppression and radiation fractions (NumPy reference, batched Torch/CUDA) | `src/oncovision/sim/solver.py` |
| Treatment schedules with persistence; MU-Glioma-Post and UCSD-PTGBM clinical parsers | `src/oncovision/treatment/{agents,schedule,clinical}.py` |
| Effect-size fit from volume changes between scans (ridge, sign-constrained, patient-cluster bootstrap, leave-patients-out CV) | `src/oncovision/treatment/fit.py` |
| Literature priors used by the what-if | `src/oncovision/treatment/priors.py` |
| Seeding (cavity excluded, one crop per patient), batched calibration, scenarios, frame export | `src/oncovision/sim/{seeding,calibrate,whatif,export_frames}.py` |

Tests: `PYTHONPATH=src pytest tests` (16; the fit is tested for recovery of known effects on synthetic data).

## Data actually used

| Dataset | Scans | Patients | With >= 2 scans | Treatment dates |
| --- | --- | --- | --- | --- |
| MU-Glioma-Post | 591 | 202 | 154 | recorded (start/end, cycles, RT dose) |
| UCSD-PTGBM | 243 | 178 | 49 | partly; doses and end dates assumed |
| LUMIERE | 525 (pre-op scans excluded) | 91 | 79 | not recorded |
| UCSF-LPTDG | clinical tables only (298 patients) | | | not downloaded (login + DUA) |

## Finding 1: drug effects are not identifiable from these cohorts

MU-Glioma-Post, 333 usable intervals from 144 patients (intervals containing a dated surgery or a cavity jump are excluded), measure = enhancing + necrotic core volume:

* fitted kill rate per day at full exposure: temozolomide 0.000 (95% CI 0 to 0.006), TTFields 0.000 (0 to 0.003), bevacizumab 0.003 (0 to 0.009); radiation 0.000 per Gy (0 to 0.013);
* leave-patients-out prediction of the volume change is **6.6% worse** than a model that ignores treatment;
* crude on- vs off-treatment median growth rates (per day, whole-cohort, no adjustment): temozolomide 0.0010 vs 0.0018 (slightly slower on drug), but TTFields 0.0035 vs 0.0006, bevacizumab 0.0052 vs 0.0006, radiation in the interval 0.0043 vs 0.0015 (faster on treatment). TTFields and bevacizumab are started at progression (confounding by indication), early post-operative intervals are noisy, and pseudo-progression follows chemoradiation, so these crude contrasts do not measure drug effects.
* UCSD-PTGBM (52 intervals, assumed schedules) gives temozolomide 0.0035/day and 0.007 per Gy; too few intervals to confirm anything.

Consequence: the what-if does **not** use fitted drug effects. It uses growth suppression derived from randomised-trial progression-free-survival ratios (`priors.py`, each with its source and whether it was verified) and is labelled illustrative everywhere. The fit stays in the repo as a check that can be re-run when better data (dated doses, response-adapted designs) arrive.

## Finding 2: patient-specific PDE forecasts do not beat trivial baselines

Calibrate rho on a patient's first interval (with the recorded schedule and the prior effects), then forecast the next scan from the previous one. 226 forecasts, 104 patients (MU-Glioma-Post). Error is the mean absolute error of the log burden ratio:

| Model | MAE | Direction accuracy |
| --- | --- | --- |
| cohort-median growth rate | 0.545 | 0.65 |
| no change | 0.558 | n/a |
| PDE, cohort-median rho (w = 0) with treatment | 0.828 | 0.65 |
| PDE, own rho shrunk w = 0.25, with treatment | 0.823 (CV-selected weight: 0.882) | 0.64 |
| PDE, treatment-blind, CV-selected weight | 0.833 | |
| PDE, own rho (w = 1) | 1.080 | 0.47 |

Including the treatment schedule does not improve the forecast (0.882 with, 0.833 without, both cross-validated). The reason is in the data, not the solver: **the correlation between a patient's growth rate in one interval and the next is -0.02** (221 consecutive interval pairs), so no method that extrapolates a patient's own history can have skill here. 71 of 150 first-interval calibrations needed the shrinking branch.

## Finding 3: baseline features carry a weak signal about growth rate, not about magnitude

Pooled over UCSF, MU-Glioma-Post and UCSD (496 first intervals), grouped 5-fold CV: predicted vs observed log-rate correlation 0.20 (UCSF 0.27, MU 0.21, UCSD 0.12); mean absolute error 8% worse than the cohort median. The ordering of patients is weakly predictable; the size of the change is not.

## Mortality risk model (pooled, 676 patients)

| Evaluation | AUC |
| --- | --- |
| within UCSF-LPTDG (OncoPulse protocol; reproduces the old 0.79 roughly) | 0.78 |
| within MU-Glioma-Post / UCSD-PTGBM | 0.66 / 0.69 |
| pooled, 5-fold grouped by patient | 0.74 |
| leave-one-dataset-out: test on UCSF / MU / UCSD | 0.72 / 0.56 / 0.46 |
| IDH wild-type status alone (UCSF / MU / UCSD) | 0.75 / 0.61 / 0.65 |

"Deceased" is defined differently by follow-up in each cohort (UCSD: 84% deceased, UCSF 42%), so the external numbers are the honest ones: the model transfers poorly, and most of its UCSF performance is IDH status.

## What this means for the demo

Say: the simulation shows how a tumour *could* respond under literature-based effect sizes, seeded from real segmentations and calibrated to the patient's own scans; it is not validated as a predictor of individual response, and our retrospective data cannot establish drug effects. Do not say: that it forecasts growth better than a simple average, or that the treatment effects were learned from the cohort.

## Reproduce

```bash
python -m oncovision.imaging.volumes --dataset mu_glioma_post --raw $R/raw/mu_glioma_post --clinical <xlsx> --out $R/processed
python scripts/gx10/fit_treatment.py --processed $R/processed --datasets mu_glioma_post --out $R/processed/treatment_effects.json
python -m oncovision.sim.whatif --dataset mu_glioma_post --processed $R/processed --out $R/processed/whatif --device cuda   # about 20 min for 150 patients
python -m oncovision.sim.export_frames --dataset mu_glioma_post --processed $R/processed --whatif $R/processed/whatif --out $R/processed/viewer --patients 24 --device cuda
python -m oncovision.risk.pooled --processed $R/processed --raw $R/raw --ucsf-master <csv> --ucsf-long <csv> --out $R/processed/risk_pooled_metrics.json
python scripts/gx10/growth_probe.py
```
