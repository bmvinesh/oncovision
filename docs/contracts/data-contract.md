# Data contract v1.0

Three hand-offs. Freeze at gate G0. After that, a change needs agreement from the producer and every consumer, and a bumped `schema_version`.

Conventions everywhere: arrays are `(Z, Y, X)`, volumes are mm³, time is days from the patient's first scan, missing values stay missing (`null` / NaN) and are never written as 0. `patient_uid` is `<dataset>:<patient_id>` and is the join key across all tables.

Dataset keys: `ucsf_lptdg`, `mu_glioma_post`, `lumiere`.

## 1. Ingest output

Producer: `oncovision.imaging` (Chaandan). Consumers: sim (Vinesh), risk and growth (Praneeth), baker (Nisheeth).

```
python -m oncovision.imaging.ingest --dataset <key> --raw <dir> --out data/processed [--patients ID ...] [--grid 64] [--workers 16]
```

Re-running skips patients already done. One bad patient is logged to `ingest_errors.jsonl` and skipped; it does not stop the run.

### `data/processed/clinical.parquet` (one row per patient)

| Column | Type | Notes |
| --- | --- | --- |
| `patient_uid`, `dataset`, `patient_id` | str | |
| `age_years` | float | at first scan |
| `sex` | `M` / `F` / null | |
| `who_grade` | 2, 3, 4 or null | |
| `idh` | `wt` / `mut` / null | |
| `mgmt` | `methylated` / `unmethylated` / null | |
| `codel_1p19q` | bool or null | |
| `who_2021_diagnosis` | str or null | |
| `extent_of_resection` | `GTR` / `STR` / `biopsy` / `none` / null | |
| `on_treatment_at_baseline` | `chemo` / `rt` / `chemo_rt` / `none` / null | |
| `deceased` | bool or null | death recorded during follow-up |
| `survival_days` | float or null | from first scan to death or last contact, when the dataset gives it |
| `n_timepoints` | int | |

Add dataset-specific extras with the prefix `x_`. Do not rename the columns above per dataset.

### `data/processed/timepoints.parquet` (one row per patient per scan)

| Column | Notes |
| --- | --- |
| `patient_uid`, `tp_index` | `tp_index` 0 is the first scan, ordered by date |
| `day` | days from `tp_index` 0 |
| `ncr_mm3`, `edema_mm3`, `et_mm3`, `rc_mm3` | necrotic / non-enhancing core, oedema or FLAIR hyperintensity, enhancing tumour, resection cavity |
| `tumor_mm3` | `ncr + edema + et`. **Cavity is excluded.** This is the quantity the simulation models. |
| `spacing_z`, `spacing_y`, `spacing_x` | native mm |
| `mask_path`, `pde_path` | relative to `data/processed` |
| `qc_flags` | comma-separated: `ok`, `empty_tumor`, `has_cavity`, `resampled`, `label_map_assumed`, ... |

Each adapter maps its dataset's label values to these four regions in one table at the top of the adapter file, and records where the mapping came from (dataset paper or README). An unverified mapping sets `label_map_assumed`.

### Masks and PDE inputs

- `masks/<dataset>/<patient_id>/tp<k>.npz`: `labels` uint8 `(Z,Y,X)` with the harmonised values 0 background, 1 ncr, 2 edema, 3 et, 4 cavity; `spacing` float32 `(3,)`; `crop_origin` int `(3,)`. Resampled to 1 mm isotropic. **All timepoints of one patient share the same crop box**, so voxels line up across time.
- `pde/<dataset>/<patient_id>/tp<k>_g<grid>.npy`: float32 `(grid,grid,grid)` in `[0,1]`, tumour density seeded from labels 1-3 only, plus a sidecar `.json` with `spacing_mm`, `tumor_voxels`, `scale_from_1mm`.

## 2. Calibration table

Producer: `oncovision.sim.calibrate` (Vinesh). Consumers: growth model (Praneeth), baker (Nisheeth).

```
python -m oncovision.sim.calibrate --processed data/processed --out data/processed/calibration.parquet --device cuda --grid 64 [--batch 32]
```

One row per consecutive pair of timepoints of a patient:

| Column | Notes |
| --- | --- |
| `patient_uid`, `tp_from`, `tp_to`, `interval_days` | |
| `rho`, `D`, `delta` | fitted, per day and mm²/day |
| `target_burden`, `fitted_burden`, `rel_error` | burden is the sum of density |
| `dice` | simulated vs observed tumour mask at `tp_to`, thresholded at 0.5 |
| `status` | `ok`, `shrinking`, `no_convergence`, `empty_seed` |
| `solver_version`, `grid` | |

Shrinking tumours are a real result, not an error: fit `delta` with `rho` fixed, and set `status = shrinking`.

Frame export, used by the baker and the API:

```
python -m oncovision.sim.export_frames --processed data/processed --params <parquet with patient_uid, rho, D, delta> --out <dir> --frames 26 --display-grid 32 --device cuda
```

## 3. Viewer data

Producer: `tools/bake_viewer_data.py` (Nisheeth writes, Praneeth runs). Consumer: `web/`.

```
web/data/
  manifest.json
  patients/<dataset>/<patient_id>/case.json
  patients/<dataset>/<patient_id>/frames.u8
```

`web/data-fixture/` holds the same layout built from the legacy site, committed, so the viewer works before any real bake.

### `manifest.json`

```json
{
  "schema_version": "1.0",
  "generated_at": "2026-10-11T09:00:00Z",
  "git_commit": "abc1234",
  "datasets": [{"key": "ucsf_lptdg", "label": "UCSF-LPTDG", "n_patients": 298, "n_timepoints": 596}],
  "models": {
    "risk":   {"version": "risk-v2", "n_train": 0, "metrics": {}},
    "growth": {"version": "growth-v1", "n_train": 0, "metrics": {}}
  },
  "patients": [
    {"uid": "ucsf_lptdg:100118", "dataset": "ucsf_lptdg", "id": "100118", "disease": "glioma",
     "idh": "wt", "who_grade": 4, "risk": 0.71, "observed_growth_pct": 170.2,
     "has_frames": true, "case_url": "patients/ucsf_lptdg/100118/case.json"}
  ]
}
```

Every patient with a risk score appears in `patients`. `has_frames` is true only for those with a baked simulation; the picker can list the rest but disables the 3D view for them. The numbers above are placeholders, not results.

### `case.json`

```json
{
  "schema_version": "1.0",
  "uid": "ucsf_lptdg:100118",
  "clinical": {"age_years": 61, "sex": "M", "who_grade": 4, "idh": "wt", "mgmt": "unmethylated", "who_2021_diagnosis": "Glioblastoma"},
  "risk": {"score": 0.71, "out_of_fold": true, "drivers": [{"feature": "idh_wt", "value": 1, "shap": 0.42}]},
  "observed": [{"tp_index": 0, "day": 0, "tumor_mm3": 10500, "et_mm3": 900, "edema_mm3": 9100, "ncr_mm3": 500, "rc_mm3": 0}],
  "sim": {
    "params": {"rho": 0.021, "D": 0.05, "delta": 0.0, "origin": "calibrated"},
    "days": [0, 7, 14],
    "tumor_mm3": [10500, 11200, 12100],
    "frames": {"url": "frames.u8", "dtype": "uint8", "shape": [26, 32, 32, 32], "order": "TZYX", "max_value": 255}
  },
  "forecast": {"kind": "held_out", "target_tp": 1, "predicted_tumor_mm3": 26000, "observed_tumor_mm3": 28400, "abs_pct_error": 8.5},
  "narrative": {"text": "...", "source": "llm", "model": "nemotron3:33b"},
  "provenance": {"synthetic": false, "notes": []}
}
```

- `sim.params.origin` is `calibrated` (fitted to this patient's own follow-up) or `predicted` (from the growth model, follow-up not used). The UI must show which.
- `forecast.kind` is `held_out` or `calibrated`; `forecast` is `null` when there is no follow-up to compare with.
- `frames.u8` is raw bytes: density × 255, rounded, `T × Z × Y × X`, no header. 26 × 32³ is about 850 kB per patient.
- `narrative.source` is `llm` or `template`.
- The breast tab keeps its legacy content: its cases use `disease: "breast"`, `dataset: "tcga_brca"`, and only the `sim` block is required.

## 4. Local API

Producer: `oncovision.api` (Vinesh). Consumer: `web/`. Served on the GX10 at `http://127.0.0.1:8000`, same origin as the static site. The viewer must work with the API absent (GitHub Pages) by hiding live features.

| Route | Body | Returns |
| --- | --- | --- |
| `GET /api/health` | | `{"device": "cuda", "gpu": "NVIDIA GB10", "llm_model": "...", "llm_ready": true}` |
| `POST /api/simulate` | `{"uid", "rho_scale", "D_scale", "drug", "dose", "horizon_days"}` | `{"days", "tumor_mm3", "frames_b64", "shape", "elapsed_ms", "illustrative": true}` |
| `POST /api/narrative` | `{"uid"}` | `{"text", "source", "model", "elapsed_ms"}` |

`frames_b64` is the same uint8 layout as `frames.u8`, base64-encoded.
