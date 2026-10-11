# Praneeth: GX10, data, training

You are the only person on the GX10, so everything that needs the GPU, the big disk, or the downloads runs through you. Your own code is the risk model and the growth-parameter model. The rest of your time is running teammates' pipelines at scale and reporting what happened.

Read [the overview](00-overview.md) and [the data contract](../contracts/data-contract.md) first.

**Paths you own:** `src/oncovision/risk/**`, `src/oncovision/growth/**`, `scripts/gx10/**`, `docs/runbooks/**`, `pyproject.toml`.

## What the machine has (checked 19:20 Saturday)

- NVIDIA GB10, 121 GB unified memory, 20 cores, 607 GB free disk, Ubuntu 24.04 aarch64.
- Ollama with `nemotron3:33b`, `nemotron-3-super`, `qwen3.6:35b`, `gemma4:26b`.
- A working CUDA PyTorch (`torch 2.14.1+cu130`, sees the GB10) in `~/UCSC-AI-HACKATHON-V-P-N-C/.venv`, with pins in that repo's `scripts/gx10/requirements-gx10.txt`. Reuse those pins; do not rediscover them.
- System Python has no torch. Docker needs sudo; we do not need Docker.

## Step 1. Environment (30 min, before G0)

1. Clone to a durable location: `~/oncovision`. Data root outside git: `/home/asus/data/oncovision/{raw,processed}`, symlinked to `data/raw` and `data/processed`.
2. `python3 -m venv .venv`, install torch from the existing pins, then `pip install -e ".[train,api,viz,dev]"`.
3. Check XGBoost: try `device="cuda"`; if the aarch64 wheel has no CUDA, use `tree_method="hist"` on 20 cores. The tables are small, so CPU is fine. Do not spend time building XGBoost from source.
4. Write `scripts/gx10/probe.py` that prints torch/CUDA/XGBoost versions and a 128³ Laplacian timing on CPU and GPU. Commit the output under `docs/runbooks/gx10.md`. That timing is the first "why the GX10" number for the demo.

## Step 2. Downloads (start immediately, they are the critical path)

Order matters: masks and clinical sheets first. The simulation and both models need only segmentation masks and clinical tables. MRI intensity volumes are needed only for the slice panel of demo patients.

| Dataset | What | How | Notes |
| --- | --- | --- | --- |
| **UCSF-LPTDG** | 298 patients, 2 timepoints, masks, clinical workbook | UCSF imaging portal, `imagingdatasets.ucsf.edu/dataset/2` | Needs your portal login and acceptance of the data use agreement. You must do that part yourself. If the portal lets you pick files, take segmentations and the workbook first. |
| **MU-Glioma-Post** | Postoperative glioma, multiple timepoints per patient, masks, clinical sheet | TCIA; old script `philip-chandan/download_mu_glioma_post.py` | Clinical sheets are plain HTTPS: run `--metadata-only` first and send the files to Chaandan. Imaging is about 11 GB over Aspera; the old script notes the CLI route often fails with HTTP 500, so be ready to use the browser plugin. |
| **LUMIERE** | Glioblastoma, many timepoints per patient, automatic segmentations, RANO labels | Figshare | Largest by scan count. Start it third. |

Record for each: URL, date, file count, total bytes, and licence in `docs/DATASETS.md` (Chaandan owns the file; send him the facts).

**Data use agreements.** This repo is public. The old repo committed UCSF masks and per-patient clinical tables. Before anything patient-level from a DUA dataset is committed or published to Pages, read the agreement. Until then: aggregates and metrics in git, patient-level baked data only on the GX10. If the agreement forbids redistribution, the public Pages build shows only the 20 patients that are already public in QBIHack, and the full cohort is shown live from the GX10.

As soon as five UCSF patients are on disk, send Chaandan the directory listing and one patient's files so his adapter is tested on real naming.

## Step 3. Risk model v2 (`src/oncovision/risk/`)

Port `brain-cancer-sim/models-praneeth/train_brain_risk.py` and `neuropulse.py`. Drop `_macos_omp_fix.py`.

1. **Baseline first.** Run the old script unchanged on the old CSVs on the GX10 and confirm AUC ≈ 0.79. That is the number to compare against.
2. Switch the input to `clinical.parquet` + baseline rows of `timepoints.parquet`. Features, baseline only: age, sex, grade, IDH, MGMT, 1p19q, extent of resection, treatment at baseline, the four region volumes, `tumor_mm3`, dataset indicator off by default. XGBoost handles missing values; do not impute.
3. Target: `deceased`. If a dataset supplies `survival_days` with censoring, also fit `objective="survival:cox"` and report the C-index. Keep the binary model as the one that feeds the viewer unless Cox is clearly better.
4. Report three evaluations in `risk_metrics.json`:
   - UCSF only, same repeated 5-fold protocol as the old model (apples to apples).
   - Pooled, 5-fold grouped by patient, stratified by dataset.
   - Leave one dataset out: train on the others, test on the held-out one. This is the external-validation number.
5. Outputs: `risk_scores.parquet` (`patient_uid`, out-of-fold `risk`), SHAP values per patient (top 6 drivers go to the baker), `risk_model.json` (XGBoost native format, not pickle).

Be ready for the pooled AUC to be no higher than 0.79. Different cohorts have different follow-up lengths, so "deceased" does not mean the same thing in each. If that happens, the honest story is the external-validation result and the bigger N, not a higher headline number.

## Step 4. Run the pipeline at scale

Run each teammate's command as soon as it passes on fixtures. Send back the wall time, row counts, and the error file.

```bash
python -m oncovision.imaging.ingest --dataset ucsf_lptdg --raw data/raw/ucsf_lptdg --out data/processed --workers 16
python -m oncovision.sim.calibrate --processed data/processed --out data/processed/calibration.parquet --device cuda --grid 64
```

Then repeat calibration at `--grid 128` for a subset and record the timing. Compare with the old MacBook benchmark in `brain-cancer-sim/data/qc/pde-prep-vinesh/grid_benchmark.json`; measure, do not assume the speed-up.

Sanity checks before trusting the output: `tumor_mm3` for the 14 legacy patients matches `raw-extract-philip-chandan/wt_volume_report.json` to within 1%; count of `status = ok` calibrations; distribution of `rho` by IDH (wild-type should be higher).

## Step 5. Growth-parameter model (`src/oncovision/growth/`)

This is the new science and the main use of the full cohort.

1. Training table: one row per calibrated interval with `status` in (`ok`, `shrinking`), joined to baseline features and the out-of-fold risk score. Targets: `log(rho)` and net rate `rho - delta`. Start with XGBoost regressors.
2. Forecast for a patient: predict parameters from baseline only, run the solver forward for the real interval, compare predicted `tumor_mm3` with the observed follow-up.
3. Evaluation, 5-fold grouped by patient, saved to `growth_metrics.json`:
   - median absolute percentage error of follow-up volume;
   - direction accuracy (grew vs shrank, with a ±5% dead band);
   - Dice between predicted and observed follow-up mask;
   - the same three for the baselines: no change, cohort-median rate, and the old `0.8 + risk` rule.
4. **Third-timepoint test** on MU-Glioma-Post and LUMIERE patients with three or more scans: calibrate on scans 1→2, forecast scan 3. Report separately; this is the cleanest evidence we can produce.
5. Outputs: `growth_params.parquet` (`patient_uid`, predicted `rho`, `D`, `delta`, all out-of-fold), `forecasts.parquet`, the metrics file.

Known confounder: these are postoperative scans, and treatment between scans changes volume. Include treatment status as a feature, state the limitation in the model card, and do not describe the output as natural-history growth.

If the model does not beat the cohort-median baseline, say so in the viewer. Calibrated playback for every patient is still a large step up from 20 patients.

Stretch, only after G3: a small 3D CNN on the baseline label volume (64³, four channels) predicting the same targets, compared against the tabular model on the same folds.

## Step 6. Bake and serve

1. Choose demo patients: about 40 with frames, spread across IDH status, grade, dataset, and forecast quality. Include some where the forecast is poor.
2. Run `tools/bake_viewer_data.py` (Nisheeth's) to fill `web/data/`.
3. `ollama pull` nothing new; confirm `nemotron3:33b` answers, then start Vinesh's API: `uvicorn oncovision.api.app:app --host 127.0.0.1 --port 8000`.
4. Pre-generate narratives for the demo patients so the demo does not wait on a cold model.
5. G4 rehearsal: disconnect the network, reboot, start from the runbook, open a fresh browser profile, run the whole demo. Write down anything that tried to reach the internet.

## Gates

| Gate | Your evidence |
| --- | --- |
| G0 Sat 20:30 | Environment probe committed. UCSF download running. MU clinical sheets sent to Chaandan. |
| G1 Sat 23:30 | Old risk model reproduced on GX10. UCSF masks and workbook on disk. Sample patient sent to Chaandan. |
| G2 Sun 03:00 | Full UCSF ingest and GPU calibration done, counts and timings recorded. Risk v2 metrics saved. |
| G3 Sun 07:00 | Growth model metrics saved, including baselines. Viewer baked from real outputs. |
| G4 Sun 10:00 | Offline rehearsal passed, or its failures listed. |

## Done means

- [ ] `docs/runbooks/gx10.md`: setup, exact commands, measured timings.
- [ ] `risk_metrics.json` with all three evaluations and the reproduced baseline.
- [ ] `growth_metrics.json` with model and baseline rows, and the third-timepoint test if data allowed.
- [ ] Row counts per dataset and the ingest error count.
- [ ] `web/data/` baked on the GX10; DUA question answered before anything patient-level is published.
- [ ] Offline rehearsal result written down.
- [ ] Hardware returned and ASUS feedback survey done (confirm with Chaandan who submits it).
