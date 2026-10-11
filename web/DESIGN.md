# OncoVision front end: design note

## Direction: the clinical monograph

A calm instrument, not a landing page, and strictly greyscale. The page reads like a printed clinical
monograph: white paper, black ink, hairline rules, a serif for headings and figures. One black imaging
console sits on it, the way a radiology reading room has one lit screen. Density is moderate, layout is
predictable, motion is functional.

The one thing to remember: **every sentence of the AI explanation carries its receipt.** The explanation is
shown as a ledger, one sentence per row, with where the sentence came from, the verified statements it
cites, and any objection the second agent raised next to the rule-based judge's ruling.

## Colour

There is no hue anywhere, including the 3D view and the charts. (Earlier versions used an H&E-stain palette
and then a slate palette with cobalt, teal and amber accents; both were replaced on request.)

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `--bg`, `--surface` | `#FFFFFF` | `#0B0B0B` | page, panels |
| `--surface-2` | `#F4F4F4` | `#171717` | the evidence band, insets, hover |
| `--line` | `#E2E2E2` | `#2B2B2B` | hairlines |
| `--line-strong` | `#8C8C8C` | `#707070` | control borders |
| `--text` | `#0A0A0A` | `#F4F4F4` | ink, primary actions, selection |
| `--text-2` | `#3A3A3A` | `#C6C6C6` | body copy |
| `--text-3` | `#6E6E6E` | `#8E8E8E` | labels, notes |

Each token is one `light-dark()` pair. The imaging console and the footer set `color-scheme: dark`, so they
take the dark values in both themes without a second token block. This needs a browser with `light-dark()`
(Chrome 123, Firefox 120, Safari 17.5 or later).

With no colour, meaning is carried by four other things, always paired with text:

- **Solidity.** Measured is a filled ink chip. Illustrative is a dashed outline. Sample data is hatched.
  AI-written is a plain outline.
- **Line pattern and marker shape.** Scenario curves differ by dash pattern and marker, with two grey
  levels; the selected scenario is the heavy line and each curve is labelled at its right end.
- **Inversion.** The selected patient, the Judge, the notice line and the primary button are ink blocks
  with paper-coloured text.
- **Hatching.** Forecast regions, held-out bars and warnings are hatched.

The density ramp in the 3D view runs dark grey to white (`#262626`, `#6E6E6E`, `#BDBDBD`, `#FFFFFF`), like a
windowed scan. The same stops are in `js/viewer3d.js`, `js/main.js` (2D slice) and the legend in the CSS.

## Type

Only the fonts already vendored are used. Instrument Serif for the headline, section headings, the scenario
name on the console and static figures (evidence numbers, tally). Geist for text and controls; live numbers
that change during playback are Geist Light with tabular figures so they do not jitter. Geist Mono for
identifiers, tags and axis values. Labels are small uppercase Geist with wide tracking.
On screens 1800 px and wider the root size steps up so the page holds on a projector.

## Layout

```
top bar: wordmark · sections · on-this-device strip (API only) · data chip · theme
notice line, ink on paper inverted (sticky with the top bar)
headline + privacy line, then the four labels as a ruled strip
┌ imaging console: viewport, legend,    ┐ ┌ four steps                ┐
│ timeline, view options (all black)    │ │ patient picker            │
│ burden chart (synced playhead)        │ │ parameters                │
│ scenario effect                       │ │ numbers, scenario table   │
└───────────────────────────────────────┘ └───────────────────────────┘
How the explanation was checked: agents → tally → sentence ledger
Evidence, on a full-bleed grey band: a ruled grid of four findings
Model card: a document, heading in the left column, body in the right
footer, black in both themes
```

Panels are ruled blocks, not cards: one ink rule above, no box, no radius, no shadow. From 1200 px the
sections after the workspace put their label in a left margin column. Two columns from 1000 px, one column
below. On a phone the order is steps, picker, console, chart, numbers.

## Motion

- Timeline playback and the tour's step highlight are the only moving parts. There is no scroll-triggered
  decoration and no ambient animation except the loading bar.
- Under `prefers-reduced-motion` the tour does not advance by itself and does not play the timeline; each
  step jumps to its end state and waits for "Next".

## What changed and why

| Change | Why |
| --- | --- |
| Stage moved to the top, title reduced to one band | The first screen on a projector shows the tumour, what it is and the privacy line. |
| Tumour framed closer by default | It was a small dim shape in a large box. |
| Explanation moved out of the side rail into its own section, as a sentence ledger | The checking story was a collapsed footnote. |
| "On this device" strip | Shows `device`, `model`, `llm.loaded`, `cases` and `explanations` from `/api/health`. Hidden on static hosting. |
| Guided tour (`?tour=1` or the button) | A path from patient to scenarios to checking that runs in about 90 seconds without narration. |
| View options collapsed; "Compare with" moved to the stage header | Fewer controls on the first screen; comparison is the main action. |
| Honesty line is sticky | The "not a prediction, not medical advice" message stays visible while scrolling. |
| Viewport overlays: composition readout, scale bar, cut-plane position | The stage reads like an imaging viewer. Every value comes from the data file or the camera geometry. |
| Read-only model parameter panel with units, scenario select | Shows what the stored simulation used. There are no sliders because nothing re-runs in the browser. |
| Scenario effect panel, CSV export, print | Effect versus no further treatment at the end of the forecast; raw curves as CSV. |
| "Evidence & methodology" dialog and case chip in the header | The method and the sources of the priors are one click from the first screen. |
| Greyscale throughout, serif headings, ruled blocks instead of cards | The slate palette with coloured accents and rounded cards read as a stock dashboard. |
| Transport and view options moved onto the black console | The viewer is one instrument, not a dark box with light controls under it. |

Kept as they were: the data format, URL parameters, element IDs, tags and their wording, "not available" for missing
values, the WebGL 2 fallback, keyboard operation, and the exported API of `js/explain.js`.

## Slots for later features

Each slot is hidden unless its data is present. `?preview=future` shows the empty designs, labelled as
previews, with no numbers in them.

| Slot | Shown when | Needs a new field? |
| --- | --- | --- |
| Dataset switch | Patients in the file come from more than one dataset (read from the `uid` prefix) | No |
| Uncertainty band on the chart | A scenario carries `burden_lo_mm3` and `burden_hi_mm3`, same length as `burden_mm3` | **Yes, proposed** |
| Risk score with top drivers | A case carries `risk: { score, drivers: [{ label, weight }] }` | **Yes, proposed** |
| Upload drop zone for a synthetic patient | `/api/health` reports `upload: true` | **Yes, proposed**, plus an endpoint |

The three proposed fields are not in `whatif_cases.json` or `docs/contracts/` today. The viewer only reads
them if they exist. Please confirm or rename them before the backend emits anything.

## Asked for and not built, because the data does not support it

A later brief asked for a "clinical-grade" look. These items from it were left out, since the project rule
is to show nothing that is not in the data files:

- Cell population breakdown (dormant, proliferating, hypoxic, necrotic): the model has one density field.
  The viewport shows segmentation volumes (measured) and modelled burden (illustrative) instead.
- Sliders for angiogenesis, hypoxic tolerance, drug sensitivity, matrix density, and presets for other
  cancers: none of these are model parameters, and there is no live simulation endpoint.
- 95% confidence band, dose-response curve, Shannon heterogeneity score: not in the data. The band slot
  appears if the fields in the table above are added.
- DICOM and PDF pathology report export: only CSV and the browser's print are provided.
- A µm scale bar, an "in vitro" timeline, a specimen number, a "deterministic seed" status, a new product
  name: the scale bar is in mm, the timeline is days since the first scan, the case is the patient ID.
- Tailwind: the viewer has no build step and loads nothing from a CDN, so the same look is plain CSS tokens.

## Open points

- The console shows one failed request for `/api/health` on static hosting. A page cannot silence that
  line; it is the probe that hides the live-only controls.
- A "requests that left this origin: 0" counter was considered for the device strip and left out, because
  it would not come from `/api/health`.
