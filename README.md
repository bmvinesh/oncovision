# OncoVision

Local glioma growth forecasting on the ASUS Ascent GX10: a molecular risk model, a 3D reaction-diffusion growth simulation seeded from each patient's real tumour mask, and an interactive 3D viewer. UCSC AI Hackathon, October 2026.

OncoVision is the continuation of **OncoPulse** ([QBIHack](https://github.com/praneethmandalapu/QBIHack), built on an M4 MacBook Air). The product and design carry over. What changes is scale: the GX10 lets us use the whole cohort and more datasets instead of 20 hand-picked patients, and run the simulation and the language model on the device.

**Status (2026-10-11): treatment-aware simulation, three-dataset ingest, pooled risk model and a what-if viewer prototype exist; see [treatment effects: method and measured results](docs/runbooks/treatment-effects.md) and [dataset downloads](docs/runbooks/data-downloads.md). Measured results are modest and stated as such there: drug effects are not identifiable from the retrospective cohorts, so the what-if uses literature priors and is labelled illustrative.**

## Start here

| Read | Who |
| --- | --- |
| [Overview, architecture, timeline](docs/plans/00-overview.md) | Everyone |
| [Data contract](docs/contracts/data-contract.md) | Everyone (frozen at gate G0) |
| [Praneeth: GX10, data, training](docs/plans/01-praneeth-gx10-data-training.md) | Praneeth |
| [Vinesh: simulation engine, local AI service](docs/plans/02-vinesh-sim-engine-local-ai.md) | Vinesh |
| [Nisheeth: viewer migration and new panels](docs/plans/03-nisheeth-frontend-viewer.md) | Nisheeth |
| [Chaandan: dataset adapters, QC, demo](docs/plans/04-chaandan-data-adapters-demo.md) | Chaandan |

## Layout

```
src/oncovision/
  imaging/   dataset adapters, mask extraction, PDE input prep      (Chaandan)
  sim/       PDE solver, calibration, frame export                  (Vinesh)
  llm/ api/  local narrative + what-if simulation service           (Vinesh)
  risk/      mortality risk model                                   (Praneeth)
  growth/    learned growth-parameter model + forecast validation   (Praneeth)
  viz/       render helpers used by the baker                       (Nisheeth)
web/         static viewer                                          (Nisheeth)
tools/       bake_viewer_data.py                                    (Nisheeth)
scripts/gx10 environment, downloads, pipeline runs                  (Praneeth)
docs/        plans, contracts, demo                                 (shared)
```

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[train,api,viz,dev]"
pytest
```

Only the GX10 needs GPU packages; everything else is developed and tested on CPU against fixtures.

## Honesty rules

This is a research demonstration, not a clinical tool. Mortality risk, calibrated growth, and held-out forecasts are different quantities and are labelled separately. Numbers shown in the viewer or said in the demo come from saved measurements, never from targets.
