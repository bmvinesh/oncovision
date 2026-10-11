# OncoVision viewer (`web/`)

A static, offline-capable viewer for the treatment what-if: a 3D tumour that you can scrub through time, synchronised with the burden curves and numbers, a sentence-by-sentence record of how the AI explanation was checked, plus an evidence overview and model card. Vanilla ES modules and three.js; no build step. The design direction, tokens and layout are in [DESIGN.md](DESIGN.md).

## Run

```bash
python3 -m http.server 8080 --directory web
# open http://localhost:8080/
```

Everything (three.js, fonts) is in `vendor/`; no request leaves localhost. WebGL 2 is needed for the 3D view; without it the page shows a 2D cross-section plus the chart and numbers.

URL parameters: `?p=<PatientID>&s=<scenario>&t=<day>` open a specific state (the page keeps them in sync as you scrub). `?data=fixture` forces the bundled sample, `?data=baked` forbids falling back to it. `?gl=off` simulates a browser without WebGL 2 (test hook). `?tour=1` starts the guided tour on load. `?preview=future` shows the empty designs of the slots for later features (see below).

## Publish

`.github/workflows/pages.yml` publishes `web/` to GitHub Pages on every push to `main` that touches it. Pages has to be enabled once by a repo admin (Settings > Pages > Source: GitHub Actions). `web/data/` is git-ignored, so the published site runs on the bundled sample in `data-fixture/`; the live-only controls stay hidden because there is no API.

## Data files expected

The loader reads `whatif_cases.json` from `data/` first and falls back to `data-fixture/`. Frame URLs inside the JSON are resolved relative to the directory the JSON came from (so `data-fixture/` + `whatif/<PatientID>/<scenario>.u8`).

```
data/                     (baked; optional, falls back to data-fixture/)
  whatif_cases.json
  whatif/<PatientID>/<scenario>.u8     uint8, shape [26,32,32,32] TZYX, density x 255
  whatif/<PatientID>/observed.u8       uint8, shape [n_scans,32,32,32], one frame per scan
data-fixture/             (committed sample, 3 real MU-Glioma-Post patients)
```

Per case the viewer uses: `uid`, `status`, `rho`, `delta`, `D`, `grid{cells,spacing_mm}`, `frames_box_mm`, `clinical{age_years,sex,who_grade,idh,mgmt,diagnosis}`, `observed[]`, `observed_frames`, `scenarios{<name>:{day,burden_mm3,volume_mm3,frames}}`, `recorded_schedule`, `assumptions`, `calibrated_on_first_interval`. Top level: `dataset`, `D_mm2_per_day`, `horizon_days`, `effects`, `forecast_metrics`, `model_card{treatment_fit,risk,forecast}`. Any missing field renders as "not available"; the IDH and WHO-grade filters appear only when some patient has the field. Size mismatches between `shape` and the file's byte length are reported in the stage, not silently rendered.

## What is real, what is not

| Item | Status |
| --- | --- |
| Patient observations, burden curves, scenario curves, 3D frames, clinical fields, box size | Real files from the backend (`data-fixture/`). No procedural mock volumes. |
| Treatment-effect fit, risk AUCs, forecast check, priors | Read live from `whatif_cases.json` (`model_card`, `effects`, `forecast_metrics`). Absent block renders "not available". |
| Two facts quoted from `docs/runbooks/treatment-effects.md` (growth-rate autocorrelation -0.02 over 221 interval pairs; 71 of 150 calibrations needed the shrinking branch) | **Hard-coded** in `js/modelcard.js` (`RUNBOOK_FACTS`), labelled with their source. Replace when the JSON carries them. |
| `cv_vs_no_treatment_baseline_pct` | The sign is interpreted as: negative = worse than the no-treatment baseline (-6.6 is shown as "6.6% worse"). |
| Plain-language explanation (`narrative` per case, `model_card.explainer`) | Baked into `whatif_cases.json` by `oncovision.llm.debate`; shown with an **AI-written** tag as a sentence ledger: each sentence with its origin (as drafted, revised, verified statement used), the statements it cites, and any Skeptic objection next to the Judge's ruling. The counts above the ledger are counted from `narrative.trace`. When the page is served by `oncovision.api.app`, a **Re-run live** button calls `POST /api/narrative`; on static hosting it is hidden. |
| "On this device" strip in the top bar | Only values from `GET /api/health`: `device`, `model`, `llm.loaded`, `cases`, `explanations`. Hidden on static hosting. |
| Guided tour captions | Fixed copy in `js/tour.js`; scenario names come from the data. |
| Viewport readout, scale bar, cut-plane position | Segmentation volumes and burden from the data file; the scale bar is computed from the box size and the camera, and in 3D is exact only at the depth the camera orbits around. |
| Model parameter panel, scenario effect panel, CSV export | Read from the selected case and `effects`. Read-only: nothing is re-simulated in the browser. Missing values are empty in the CSV, never 0. |
| Live `/api/simulate` | Not built. |
| Contract section 3 (`manifest.json`, `case.json`, `frames.u8`) | Not consumed. The viewer follows the `whatif_cases.json` format the backend emits. |

## Honesty rules in the UI

- A banner that stays pinned while scrolling says illustrative research simulation, not a prediction, not medical advice, and that effects are literature priors.
- Every figure carries a tag: **measured** (scan segmentations, cohort evaluation), **illustrative** (scenario output), **sample data** (the bundled fixture). AI text carries **AI-written**. Tags are text plus a distinct shape, never colour alone.
- Missing values render as "not available", never 0. A density-threshold volume of 0 reads "below 0.5 everywhere".
- Fits that did not converge are flagged and their curves marked as not plausible; shrinking tumours are labelled.
- Colour scale is stretched to the patient's own maximum by default and the legend prints the actual maximum; "absolute 0 to 1" is one click away.

## Slots for later features

Each is hidden unless its data is present; nothing here changes the data format. The proposed fields are listed in [DESIGN.md](DESIGN.md) and need agreeing before the backend emits them.

| Slot | Shown when |
| --- | --- |
| Dataset switch | patients in the file come from more than one dataset (`uid` prefix) |
| Uncertainty band on the chart | a scenario has `burden_lo_mm3` and `burden_hi_mm3` (proposed) |
| Risk score with top drivers | a case has `risk` (proposed) |
| Upload drop zone | `/api/health` reports `upload: true` (proposed); there is no upload endpoint yet |

## Layout

```
index.html        title, workspace (3D stage, transport, chart; guided steps, picker, readout), checked explanation, evidence, model card
whatif.html       redirect to index.html#workspace
css/app.css       tokens (dark/light), layout, components
js/main.js        wiring and state (store.js), playback, readouts
js/data.js        loading, normalising, frame fetching, interpolation
js/viewer3d.js    WebGL2 ray-marched volume (clip plane, split compare)
js/chart.js       SVG burden chart with keyboard-operable playhead
js/picker.js      virtualised, searchable, filterable patient list
js/modelcard.js   evidence cards and model card
js/explain.js     plain-language explanation as a sentence ledger, agents, tally, live re-run
js/device.js      "on this device" strip from /api/health
js/tour.js        guided tour (four steps, ?tour=1)
js/futures.js     slots for later features, hidden without data
                  (main.js also draws the viewport readout, scale bar, parameter panel, effect panel and CSV export)
vendor/           three.js, OrbitControls, fonts
THIRD_PARTY.md    licences and references
```

## Accessibility notes

Keyboard: the 3D stage takes arrow keys (orbit), + and - (zoom), R (reset), Space (play); the chart playhead is a slider (arrows, Shift for a week, Home, End); the patient list is a listbox (arrows, Enter). The guided tour is four buttons plus a caption bar (Pause, Next, End tour; Escape ends it). `prefers-reduced-motion` disables decorative animation and auto-rotate, and the tour then waits for "Next" at every step and does not play the timeline by itself. Light and dark follow the OS; the button overrides it. The 3D stage stays dark in both themes, as in imaging viewers.
