# OncoVision: overview and team plan

Written Saturday 2026-10-10, 19:30 PDT. Source of migrated code: QBIHack `main` at `4a77efc`. Paths under "old" below are relative to that repo.

## Direction

Carry OncoPulse over (3D growth simulation, risk model, viewer design) and make it bigger and local:

1. **More data.** The old brain pipeline scored 298 UCSF patients with the risk model but only simulated 20 of them, and calibrated 14. On the GX10 we process the whole UCSF-LPTDG cohort and add MU-Glioma-Post and LUMIERE.
2. **Training that needs the GX10.** GPU batch calibration of the PDE for every patient, then a model that predicts each patient's growth parameters from baseline features. That lets us report a held-out forecast (predict the follow-up scan without seeing it), which the old project could not do.
3. **Local AI.** Narrative explanation from the Nemotron model already installed in Ollama, and live what-if simulation on the GPU. Patient imaging never leaves the device.

This replaces the two earlier plans from today (histology workstation, breast/paclitaxel). Their repos stay untouched; nothing here depends on them except the GX10 Python pins noted in Praneeth's plan.

## Who does what

Only Praneeth needs the GX10. Everyone else develops on a laptop against fixtures and hands Praneeth a command to run.

| Person | Owns | Writable paths |
| --- | --- | --- |
| **Praneeth** (on GX10) | Environment, all downloads, full-cohort pipeline runs, risk model v2, growth-parameter model, serving, final bake | `src/oncovision/{risk,growth}/**`, `scripts/gx10/**`, `docs/runbooks/**`, `pyproject.toml` |
| **Vinesh** | PDE solver migration and GPU backend, batch calibration, frame export, local API (what-if simulation, LLM narrative) | `src/oncovision/{sim,llm,api}/**`, `tests/{sim,api}/**` |
| **Nisheeth** | Viewer migration, offline assets, data loader, cohort picker, new panels, the baker | `web/**`, `tools/**`, `src/oncovision/viz/**`, `tests/web/**` |
| **Chaandan** | Imaging pipeline migration, dataset adapters, clinical harmonisation, QC, demo script and submission | `src/oncovision/imaging/**`, `tests/imaging/**`, `docs/{demo,DATASETS.md}`, `README.md` |

`docs/contracts/**` is shared: change it only by agreement in the team chat, in one commit that also updates producer and consumer.

## Pipeline

```
raw NIfTI + clinical sheets          (Praneeth downloads; lives outside git)
   │  oncovision.imaging.ingest      (Chaandan writes, Praneeth runs)
   ▼
clinical.parquet, timepoints.parquet, masks/, pde/
   │                    │
   │                    │  oncovision.sim.calibrate --device cuda   (Vinesh writes, Praneeth runs)
   │                    ▼
   │              calibration.parquet
   ▼                    │
oncovision.risk.train   │            (Praneeth)
   ▼                    ▼
risk scores ──► oncovision.growth.train ──► forecasts + validation metrics   (Praneeth)
                        │
                        ▼
              tools/bake_viewer_data.py     (Nisheeth writes, Praneeth runs)
                        ▼
              web/data/  ──►  web/ viewer  ◄── /api on GX10 (narrative, what-if)
```

The three hand-off formats (ingest outputs, calibration table, viewer data) are specified in the [data contract](../contracts/data-contract.md). Everything parallel depends on that file, so it is frozen first.

## Known defects in the old code to fix during migration

These are not optional polish; each one blocks scaling.

| Defect | Where (old) | Fix | Owner |
| --- | --- | --- | --- |
| PDE seed uses `mask > 0`, so resection cavity (label 4) counts as tumour. 5 of 20 patients were excluded for it; most postoperative patients have a cavity. | `vinesh/prepare_pde_input.py`, `pde_burden_compare.py` | Seed from tumour labels only; keep cavity as a separate channel | Chaandan |
| Each timepoint is cropped independently, so follow-up burden cannot be compared spatially (noted in `calibrate_philip.py`). | `export_raw_extract.py`, `prepare_pde_input.py` | One crop box per patient, shared by all timepoints | Chaandan |
| Solver is single-volume NumPy/SciPy on CPU. | `vinesh/tumor_pde_solver.py` | Torch backend with a batch dimension, parity-tested against NumPy | Vinesh |
| `growth_multiplier = 0.8 + risk` is a hand-set rule; mortality risk is not growth. | `models-praneeth/train_brain_risk.py` | Learn growth parameters from calibrations; keep the old rule as a baseline to beat | Praneeth |
| The site is one 95 MB HTML file with every array inlined; 20 patients is its ceiling. | `visualization-jasim/build_site.py` | Per-patient files fetched on demand | Nisheeth |
| Plotly and fonts load from CDNs, so the viewer fails offline. | `_template.html` lines 8-11 | Vendor both into `web/vendor/` | Nisheeth |
| `generate_narrative` raises `NotImplementedError`. | `breast-cancer-sim/app/llm_narrative.py` | Implement against local Ollama with a template fallback | Vinesh |
| Dataset paths, file naming and label maps are hard-coded to UCSF. | `philip-chandan/*.py` | Adapter per dataset behind one interface | Chaandan |

## Timeline

Wall-clock PDT. This assumes a demo around 13:30 Sunday; if the real deadline differs, shift G3-G5 and keep the gaps.

| Gate | Time | Must be true |
| --- | --- | --- |
| **G0** | Sat 20:30 | Data contract agreed. Each owner's legacy copy is on `main` and imports. UCSF download running on the GX10. |
| **G1** | Sat 23:30 | Torch solver matches NumPy on fixtures. Ingest produces contract-valid output for the 20 legacy patients. Viewer runs offline from fixture files and looks like the old site. |
| **G2** | Sun 03:00 | Full UCSF cohort ingested and calibrated on the GPU. Risk v2 trained with external-validation numbers. `/api/narrative` returns grounded text from Ollama. |
| **G3** | Sun 07:00 | Growth model trained; held-out forecast metrics saved. Real baked data in the viewer. MU-Glioma-Post and LUMIERE included if their downloads landed. |
| **G4** | Sun 10:00 | Feature freeze. Full run on the GX10 with the network cable out and Wi-Fi off. |
| **G5** | Sun 12:30 | Two timed rehearsals, backup recording, feedback survey done. |

Sleep in shifts; the long-running steps (downloads, ingest, calibration) are the natural breaks for Praneeth.

If a dataset has not finished downloading by G2, ship with what is on disk. A complete UCSF run with honest validation beats three half-ingested datasets.

## Working agreement

- Branch `dev/<name>`, small pull requests into `main`, merge at every gate. No force-pushes to `main`.
- Stay in your paths. If you need a change in someone else's, send them the failing input and the proposed change.
- Every hand-off states the command, what it actually printed, and what is not done. Label numbers as measured, fixture, or not yet run.
- Nothing from `data/raw/` or `data/processed/` is committed. Patient-level files from datasets under a data use agreement do not go in this public repo; see Praneeth's plan, step 2.
- No keys or tokens in the repo. The runtime needs none: Ollama is local.

## What we will claim, and what we will not

| Claim | Evidence needed |
| --- | --- |
| "Trained on N patients from K datasets" | Row counts in `clinical.parquet`, shown in the model card |
| "Risk model AUC x on an external dataset" | Leave-one-dataset-out result saved in `risk_metrics.json` |
| "Forecast error y% on patients the model never saw" | Grouped-CV and third-timepoint results in `growth_metrics.json` |
| "Runs fully offline on the GX10" | G4 rehearsal |

Not claimed: clinical validity, treatment recommendations, calibrated uncertainty, or that the drug what-if sliders reflect measured drug effect. The intervention factors in `growth_interventions.py` are hand-set and are labelled illustrative in the UI.
