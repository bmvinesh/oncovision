# OncoVision viewer (`web/`)

A static, offline-capable viewer for the treatment what-if: a 3D tumour that you can scrub through time, synchronised with the burden curves and numbers, plus an evidence overview and model card. Vanilla ES modules and three.js; no build step.

## Run

```bash
python3 -m http.server 8080 --directory web
# open http://localhost:8080/
```

Everything (three.js, fonts) is in `vendor/`; no request leaves localhost. WebGL 2 is needed for the 3D view; without it the page shows a 2D cross-section plus the chart and numbers.

URL parameters: `?p=<PatientID>&s=<scenario>&t=<day>` open a specific state (the page keeps them in sync as you scrub). `?data=fixture` forces the bundled sample, `?data=baked` forbids falling back to it. `?gl=off` simulates a browser without WebGL 2 (test hook).

## Data files expected

The loader reads `whatif_cases.json` from `data/` first and falls back to `data-fixture/`. Frame URLs inside the JSON are resolved relative to the directory the JSON came from (so `data-fixture/` + `whatif/<PatientID>/<scenario>.u8`).

```
data/                     (baked; optional, falls back to data-fixture/)
  whatif_cases.json
  whatif/<PatientID>/<scenario>.u8     uint8, shape [26,32,32,32] TZYX, density x 255
  whatif/<PatientID>/observed.u8       uint8, shape [n_scans,32,32,32], one frame per scan
data-fixture/             (committed sample, 3 real MU-Glioma-Post patients)
```

Per case the viewer uses: `uid`, `status`, `rho`, `delta`, `D`, `grid{cells,spacing_mm}`, `observed[]`, `observed_frames`, `scenarios{<name>:{day,burden_mm3,volume_mm3,frames}}`, `recorded_schedule`, `assumptions`, `calibrated_on_first_interval`. Top level: `dataset`, `D_mm2_per_day`, `horizon_days`, `effects`, `forecast_metrics`. Optional per-case `idh`, `who_grade` (or `clinical.{idh,who_grade}`) are shown when present, otherwise "not available". Size mismatches between `shape` and the file's byte length are reported in the stage, not silently rendered.

## What is real, what is not

| Item | Status |
| --- | --- |
| Patient observations, burden curves, scenario curves, 3D frames | Real files from the backend (`data-fixture/`). No procedural mock volumes remain. |
| Treatment priors table, forecast-check table | Read live from `whatif_cases.json` (`effects`, `forecast_metrics`). |
| Treatment-effect fit (333 intervals, 144 patients, kill rates, 6.6% worse CV) and mortality-risk AUCs (pooled, within, leave-one-dataset-out, IDH-wt) | **Hard-coded** in `js/modelcard.js` (`MODEL_RESULTS`) from results reported by the backend. They are not in any data file yet. Replace by reading a `model_card.json` when one exists. |
| Narrative, live `/api/simulate`, `/api/health` | Not built. This viewer is static-only. |
| Contract section 3 (`manifest.json`, `case.json`, `frames.u8`) | Not consumed. The viewer follows the `whatif_cases.json` format the backend now emits. |

## Honesty rules in the UI

- A persistent banner says research demo, not clinical advice, and that effects are literature priors.
- Every figure carries a tag: **measured** (scan segmentations, cohort evaluation), **illustrative** (scenario output), **sample data** (the bundled fixture). Tags are text plus a distinct shape, never colour alone.
- Missing values render as "not available", never 0. A density-threshold volume of 0 reads "below 0.5 everywhere".
- Fits that did not converge are flagged and their curves marked as not plausible; shrinking tumours are labelled.
- Colour scale is stretched to the patient's own maximum by default and the legend prints the actual maximum; "absolute 0 to 1" is one click away.

## Layout

```
index.html        overview, workspace (picker, 3D stage, transport, chart, readout), evidence, model card
whatif.html       redirect to index.html#workspace
css/app.css       tokens (dark/light), layout, components
js/main.js        wiring and state (store.js), playback, readouts
js/data.js        loading, normalising, frame fetching, interpolation
js/viewer3d.js    WebGL2 ray-marched volume (clip plane, split compare)
js/chart.js       SVG burden chart with keyboard-operable playhead
js/picker.js      virtualised, searchable, filterable patient list
js/modelcard.js   evidence cards and model card
vendor/           three.js, OrbitControls, fonts
THIRD_PARTY.md    licences and references
```

## Accessibility notes

Keyboard: the 3D stage takes arrow keys (orbit), + and - (zoom), R (reset), Space (play); the chart playhead is a slider (arrows, Shift for a week, Home, End); the patient list is a listbox (arrows, Enter). `prefers-reduced-motion` disables decorative animation and auto-rotate. Light and dark follow the OS; the button overrides it. The 3D stage stays dark in both themes, as in imaging viewers.
