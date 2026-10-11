# Vinesh: simulation engine and local AI service

You own the PDE engine you wrote, now with a GPU backend and batch calibration, and the small local service that makes the demo live: what-if simulation and LLM narrative. None of it needs the GX10 to develop. Write device-agnostic code, test on CPU, and give Praneeth one command to run on CUDA.

Read [the overview](00-overview.md) and [the data contract](../contracts/data-contract.md) first.

**Paths you own:** `src/oncovision/sim/**`, `src/oncovision/llm/**`, `src/oncovision/api/**`, `tests/sim/**`, `tests/api/**`.

## Step 1. Migrate (by G0)

From QBIHack `4a77efc`, `brain-cancer-sim/simulation-vinesh-philip-chandan/`:

| Old | New |
| --- | --- |
| `vinesh/tumor_pde_solver.py` | `sim/solver.py` |
| `vinesh/growth_interventions.py` | `sim/interventions.py` |
| `vinesh/mask_seeding.py` | `sim/seeding.py` |
| `vinesh/export_frames.py`, `make_jasim_sequences.py` | `sim/export_frames.py` |
| `philip-chandan/calibrate_philip.py` | `sim/calibrate.py` |
| `vinesh/bench_pde_grid.py` | `sim/bench.py` |
| `vinesh/test_solver.py`, `test_mask_seeding.py`, `philip-chandan/tests/test_calibrate_philip.py` | `tests/sim/` |

Replace the `sys.path.insert` calls with package imports. Drop `handoff_contract.py` and `spike_paths.py`: paths now come from command-line arguments and the data contract. `prepare_pde_input.py` moves to Chaandan (`imaging/pde_input.py`), since seeding from labels belongs with the masks.

The breast copy of the solver is identical physics; do not migrate it separately.

**Fixture.** The 20 legacy patients have masks committed in QBIHack under `brain-cancer-sim/data/processed/segmentations/`. Build `tests/fixtures/sim/` from two of them (one IDH wild-type that grew, one IDH-mutant that was stable; 100118 and 100002 are the old demo pair) downsampled to 32³ so the fixture is a few hundred kB. Until Chaandan's ingest exists, seed directly from those masks.

## Step 2. Torch backend (by G1)

Keep the NumPy solver as the reference. Add a torch implementation with the same array contract.

```python
solve_growth(u0, timesteps, dt, params, *, backend="numpy"|"torch", device="cpu", return_frames=True|int)
```

1. Laplacian: 7-point stencil via `torch.nn.functional.conv3d` on a replicate-padded tensor, to match SciPy `laplace(mode="nearest")`. Note the old code divides by the mean squared spacing; keep that so results match, and handle anisotropic spacing only if a dataset actually needs it.
2. Batch dimension: `u0` may be `(B, Z, Y, X)` with per-item `rho`, `D`, `delta` tensors. This is what makes cohort calibration fast.
3. `return_frames=int` keeps only that many evenly spaced frames. The old solver copies every step into a list, which at 128³ and many steps is gigabytes.
4. Keep the CFL check; with per-item `D` use the largest.
5. `float32` throughout. No `torch.compile` or mixed precision until the plain version is measured.

Tests in `tests/sim/`: torch on CPU matches NumPy within `1e-4` max absolute difference after 50 steps on the fixtures; batch of two equals two single runs; `D = 0` reduces to logistic growth with the closed-form answer; mass never leaves `[0, 1]`. Parametrise the device so the same tests run on CUDA on the GX10.

## Step 3. Batch calibration (by G1, run at scale by G2)

Old `calibrate_philip.py` runs SciPy `brentq` per patient, calling the solver for each trial. Replace with a batched search:

1. For each pair of consecutive timepoints, the target is the observed follow-up burden from the PDE cube. Chaandan's shared crop box makes this directly comparable, so the old `wt_scaled` workaround is no longer the default; keep it as a fallback flag.
2. Batched bisection over the whole batch at once, about 25 iterations. The old code fits `risk_multiplier` (a multiplier on `rho`) with `D` left at the solver default of 0.15; keep that, and write the effective `rho = base rho × multiplier` and the `D` used to the table.
3. If the tumour shrank, the old code already switches to fitting `delta`; keep that branch, batched, and set `status = shrinking`.
4. After the fit, compute Dice of the simulated mask against the observed follow-up mask.
5. Write `calibration.parquet` exactly as in the contract. Resumable: skip pairs already in the output.

Expose `calibrate_batch(u0, target_burden, interval_days, ...)` as a function as well as the CLI; Praneeth's forecast code calls the solver directly.

Report from your laptop: time per patient on CPU at 64³. Praneeth reports the GPU number.

## Step 4. Frame export (by G2)

`python -m oncovision.sim.export_frames` per the contract: takes a parameter table, runs forward, writes for each patient a `(T, g, g, g)` uint8 stack downsampled to the display grid plus `days` and `tumor_mm3` per frame. Agree the output directory layout with Nisheeth so his baker reads it without glue code.

## Step 5. Local API (by G2)

One FastAPI app, `src/oncovision/api/app.py`, routes as in the contract. It also serves `web/` as static files so the viewer and API share an origin.

**`POST /api/simulate`.** Load the patient's PDE cube, apply `rho_scale`, `D_scale` and the intervention, run the torch solver, return display-grid frames. Keep the model loaded and cubes cached with a bound (LRU, 64 entries). One simulation at a time behind a lock; reject or queue the rest. Validate ranges: scales in `[0.25, 4]`, dose in `[0, 1]`, horizon up to 730 days. Always return `"illustrative": true`: the drug factors in `interventions.py` are hand-set numbers, not measured effects.

**`POST /api/narrative`.** In `src/oncovision/llm/`:

1. Build a facts block from the patient's `case.json`: clinical fields, risk score and top drivers, observed volumes, forecast and its error, and whether parameters were calibrated or predicted.
2. Call Ollama at `http://127.0.0.1:11434/api/chat`. Model from the `ONCOVISION_LLM_MODEL` environment variable, default `nemotron3:33b`. Temperature low, output capped at about 200 tokens, 60 s timeout.
3. The prompt tells the model to explain only the given facts, in plain language, with no treatment advice and no numbers that are not in the facts.
4. Check the output: every number in the text must appear in the facts block (allowing rounding). If the check fails, or Ollama is down or slow, return a deterministic template built from the same facts with `source: "template"`.
5. Cache by `(uid, facts hash, model)` on disk so pre-generation before the demo works.

Develop against any small Ollama model on your laptop; nothing else changes on the GX10. No cloud model, no API key, anywhere.

**`GET /api/health`.** Device, GPU name, whether the LLM model is loaded.

Tests in `tests/api/` with a fake Ollama: template fallback on timeout, on a fabricated number, and on an empty reply; simulate rejects out-of-range input; a second concurrent simulate does not run in parallel.

## Gates

| Gate | Your evidence |
| --- | --- |
| G0 Sat 20:30 | Code migrated, old tests pass under the new package layout. |
| G1 Sat 23:30 | Torch/NumPy parity tests pass. Batch calibration reproduces the old fitted `rho` for the legacy demo pair, or the difference is explained. |
| G2 Sun 03:00 | Praneeth has run calibration on the full cohort with your CLI. `/api/narrative` and `/api/simulate` work against fixtures. |
| G3 Sun 07:00 | API running on the GX10 with the real model and real cubes. Latency of both routes measured there. |
| G4 Sun 10:00 | Fixes only. |

## Done means

- [ ] `pytest tests/sim tests/api` passes on CPU, and `tests/sim` on CUDA on the GX10.
- [ ] `calibrate` and `export_frames` CLIs match the contract; Praneeth ran both on the full cohort.
- [ ] Narrative never shows a number that is not in the facts; fallback is labelled.
- [ ] What-if output is labelled illustrative in the response.
- [ ] Measured: solver time at 64³ and 128³ on CPU and GPU, narrative time to full response. Give these to Chaandan for the demo script.
