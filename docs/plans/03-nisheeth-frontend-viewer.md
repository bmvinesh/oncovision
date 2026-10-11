# Nisheeth: viewer migration and new panels

You own the viewer. Keep the OncoPulse design exactly; change how it gets its data so it scales from 20 patients to the whole cohort, works with no internet, and shows the new results. None of this needs the GX10: you work against fixture files.

Read [the overview](00-overview.md) and [the data contract](../contracts/data-contract.md), section 3 and 4, first.

**Paths you own:** `web/**`, `tools/**`, `src/oncovision/viz/**`, `tests/web/**`.

## What exists

In QBIHack `4a77efc`, `breast-cancer-sim/visualization-jasim/`:

- `_template.html` (583 lines): all markup, CSS and vanilla JS. Two tabs, brain and breast. Each has a Plotly 3D volume, a play/scrub timeline, a growth curve, KPIs, and a slices panel. The brain tab also has a cohort chart and a patient picker.
- `build_site.py`: loads frame stacks, downsamples to a 32³ display grid, and string-replaces placeholders (`__BRAIN__`, `__BREAST_GROWTH__`, `__COHORT__`, `__BRAIN_SLICES__`, ...) on line 377-378 of the template with JSON. The output is one 95 MB `index.html`.
- `render_3d.py`, `color_maps.py`, `make_brain_frames.py`, `make_breast_frames.py`: Python helpers for downsampling, analytics, slice figures and colour scales.
- Lines 8-11 of the template load Google Fonts and Plotly 2.35.2 from CDNs.

**Stack decision: keep vanilla JS and Plotly. Do not rewrite in React.** The design already works, the page is small, and a rewrite spends the night reproducing what exists.

## Step 1. Migrate and split (by G0)

1. Copy `_template.html` to `web/index.html`. Move the `<style>` block to `web/css/app.css` and the `<script>` block to `web/js/` as ES modules: `main.js`, `data.js` (loading), `brain.js`, `breast.js`, `plot.js` (shared Plotly layout and traces).
2. Vendor offline assets into `web/vendor/`: `plotly-2.35.2.min.js` and the woff2 files for Space Grotesk, JetBrains Mono and Instrument Serif, with a local `@font-face` stylesheet. Remove every external URL from `web/`.
3. Copy `render_3d.py` and `color_maps.py` to `src/oncovision/viz/`.
4. Rename OncoPulse to OncoVision in titles and footer. Keep the palette and layout.

Serve with `python -m http.server 8080 --directory web`. No bundler, no build step.

## Step 2. Fixture and loader (by G1)

1. Write `tools/legacy_site_to_fixture.py`: read the old `docs/index.html`, pull the JSON payloads out of the `const ... = ...;` line, and write them in contract layout to `web/data-fixture/`: `manifest.json`, one `case.json` and one `frames.u8` per patient. Values in the old payload are floats rounded to 2 decimals at 32³; convert to uint8. Commit the fixture (about 20 patients, roughly 20 MB; if that is too heavy keep 6 and note which).
2. `data.js`: fetch `data/manifest.json`, falling back to `data-fixture/manifest.json` when the first is missing. Fetch `case.json` and `frames.u8` only when a patient is selected. Decode frames into one `Uint8Array` and hand Plotly a view per frame; do not copy 26 arrays into objects. Keep a small cache (last 5 patients). Ignore a response if the user has already picked another patient.
3. Regenerate the grid coordinates (`GRID_X/Y/Z`) in JS from the `shape` in `case.json` instead of shipping them.
4. Rewire `brain.js` and `breast.js` to the loader. Target for G1: the page looks and behaves like the old site, from files, with the network tab showing no external requests.

Missing fields are shown as "not available", never as 0. If `sim.params.origin` is `predicted`, the stage label says so.

## Step 3. Cohort picker (by G2)

The old picker is a dropdown of 20. Replace with a panel driven by `manifest.patients`:

- search by ID; filters for dataset, IDH, grade; sort by risk or observed growth;
- rows without `has_frames` are listed but open only the clinical and risk panels;
- virtualise or page the list so a few hundred rows stay smooth;
- keep the two old demo patients (100118, 100002) as the default landing pair.

## Step 4. New panels (by G3)

Build each against the fixture first, with fixture numbers visibly marked as such.

| Panel | Data | Notes |
| --- | --- | --- |
| **Risk and drivers** | `case.risk` | Score plus a horizontal bar of the top SHAP drivers. Label: "mortality risk, relative rank; not a growth prediction". |
| **Forecast vs observed** | `case.sim`, `case.observed`, `case.forecast` | Add observed timepoints as markers on the existing growth curve. Show predicted vs observed follow-up volume and the error. State whether it is held-out or calibrated. |
| **Model card** | `manifest.datasets`, `manifest.models` | Patients and scans per dataset; risk AUC (UCSF-only, pooled, external); forecast error vs the baselines. This replaces the "Real UCSF cohort" block and is where "20 patients before, N now" is shown. |
| **Local AI narrative** | `POST /api/narrative`, else `case.narrative` | Show source (`llm` or `template`) and model name. Loading state; never show partial text as final. |
| **What-if** | `POST /api/simulate` | Sliders for growth rate and diffusion, a treatment selector, a run button. Replays the returned frames in the same 3D stage, with the baseline curve kept for comparison. Badge: "illustrative". |
| **System chip** | `GET /api/health` | "Running on GX10 · GB10 · model name", or "static mode" when there is no API. |

On page load, call `/api/health` once with a short timeout. If it fails, hide the what-if panel and use baked narratives. The same build must work on GitHub Pages and on the GX10.

## Step 5. The baker (by G2, with Praneeth)

`tools/bake_viewer_data.py` is the port of `build_site.py` without the HTML templating. Inputs: `clinical.parquet`, `timepoints.parquet`, `risk_scores.parquet` and SHAP, `growth_params.parquet`, `forecasts.parquet`, the metric JSON files, and Vinesh's exported frame stacks. Output: `web/data/` per the contract.

- Validate what you write: a small `validate_viewer_data.py` that checks every `case_url` exists, shapes match byte lengths, and required fields are present. Run it at the end of the bake and in tests against the fixture.
- Agree the frame-stack directory layout with Vinesh at G0 so there is no adapter in between.
- The breast tab is carried over as it is: bake its two legacy scenarios once from the old payload and leave them.
- Praneeth runs the bake on the GX10. Test it yourself on the fixture-sized inputs he sends you.

## Step 6. Offline and deploy (by G4)

1. Offline audit: load the page in a fresh browser profile with the network disabled except localhost. Zero failed or external requests. Save the request list in `docs/demo/offline-audit.md` (ask Chaandan to file it).
2. Check at the demo display's resolution, and that play/scrub stays smooth with the largest patient.
3. GitHub Pages: publish `web/` from a workflow or the `gh-pages` branch. Vinesh owns the repo settings, so ask him to enable Pages. **Do not publish `web/data/` until Praneeth confirms the data use agreements allow it;** Pages ships with `data-fixture/` until then.

## Gates

| Gate | Your evidence |
| --- | --- |
| G0 Sat 20:30 | Template split into files and served locally. Fonts and Plotly vendored. |
| G1 Sat 23:30 | Fixture generated from the legacy site. Viewer runs from it, visually matching the old site, with no external requests. |
| G2 Sun 03:00 | Cohort picker working on a manifest of a few hundred rows (generate a synthetic manifest to test, labelled synthetic). Baker passes validation on sample inputs. |
| G3 Sun 07:00 | All panels working against real baked data from the GX10 and the live API. |
| G4 Sun 10:00 | Offline audit clean on the GX10. Pages build live in static mode. |

## Done means

- [ ] Old design preserved; both tabs work.
- [ ] No external request from a cold browser.
- [ ] Page weight before selecting a patient is under 5 MB (the old page was 95 MB); measure and record it.
- [ ] Picker handles the full cohort.
- [ ] Each new panel shows its provenance: held-out or calibrated, LLM or template, illustrative, fixture or real.
- [ ] Static mode and GX10 mode both verified.
