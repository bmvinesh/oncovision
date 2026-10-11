# What the GX10 (DGX Spark class) is, and is not, needed for

Measured 2026-10-11. Raw numbers: `docs/results/bench_solver_*.json`; reproduce with
`python scripts/gx10/bench_solver.py`.

## The simulation: a modest advantage, not a requirement

Batched reaction-diffusion solve, 60 explicit steps, the same code on every machine:

| Machine / backend | 64 patients at 64^3 | 8 patients at 128^3 | Throughput (G voxel-steps/s) |
| --- | --- | --- | --- |
| GX10, GB10 GPU (Torch CUDA) | 0.95 s | 0.95 s | 1.06 |
| MacBook M4, 10 cores (Torch CPU) | 2.2 s* | 2.5 s* | 0.46 |
| MacBook M4 (Torch MPS) | 3.3 s | 3.3 s | 0.31 |
| GX10, 20 Arm cores (Torch CPU) | 2.6 s* | 2.4 s* | 0.39 |
| GX10, one thread (NumPy/SciPy) | 6.7 s* | 8.5 s* | 0.15 |
| MacBook M4, one thread (NumPy/SciPy) | 9.6 s* | 10.9 s* | 0.11 |

\* timed at a smaller batch and scaled linearly (the work is linear in batch size).

The GPU is about **2.3x faster than the M4's CPU** and 7x faster than single-threaded NumPy. That tracks memory
bandwidth: the stencil is memory-bound, and the GB10's unified memory is only about twice an M4's. The cohort
calibration that took about 20 minutes on the GX10 (150 patients) would take roughly 45 minutes on the M4. **The
solver alone does not justify the hardware**, and an honest write-up should say so.

## What the hardware does uniquely enable

| Capability | Measured | Why a 16 GB laptop cannot do it |
| --- | --- | --- |
| Local explanation model `nemotron3:33b` | 27.6 GB (Q4_K_M), 69 tokens/s, 3 to 5 s per explanation | the weights alone exceed 16 GB of RAM |
| Larger local model `nemotron-3-super` | 94 GB resident in GPU memory, 17.8 tokens/s, 32 s cold load | needs a 96 GB+ unified-memory machine |
| Simulation + LLM + the 57 GB cohort on one box | all in the same 121 GB pool | different tools on different machines otherwise |
| Patient images never leave the device | no network at run time (Ollama is local) | true of any local machine, not specific to this one |

The model-size rows are arithmetic on file sizes and were not attempted on the MacBook.

## What a stronger case would need (not done)

* A workload where the simulation is the bottleneck: ensembles (thousands of runs per patient for uncertainty) or
  higher-resolution grids. At 1 G voxel-steps/s a 1000-member ensemble for 150 patients at 64^3 over 180 days is about
  2 hours; on the M4 about 4 hours. Useful, still not decisive.
* Using the 94 GB model as an independent checker of the 33B model's explanations (it fits only here); not built.

## Local explanations: how they are kept honest

`src/oncovision/llm/narrative.py` gives the model pre-verbalised claims, then accepts its reply only if every number
matches a claim and every sentence quoting a directional number carries the right direction words. A first version
that checked only numbers let the model call a result that is 6.6% worse "6.6 percentage points higher", which is
why the direction check exists. On the three fixture patients, 2 of 3 case explanations and the results explainer
passed and the third fell back to the deterministic template (`docs/results/narrative_examples.json`). The check does
not prove the wording is right; the viewer must label model-written text as AI-written.
