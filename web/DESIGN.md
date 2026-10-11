# OncoVision front end: design note

## Direction: the reading room

A calm instrument, not a landing page. The page is a light table with one dark viewport on it, the way a
radiology reading room has one lit screen. Density is moderate, layout is predictable, motion is functional.

The one thing to remember: **every sentence of the AI explanation carries its receipt.** The explanation is
shown as a ledger, one sentence per row, with where the sentence came from, the verified statements it
cites, and any objection the second agent raised next to the rule-based judge's ruling.

## Colour

A clinical slate palette with restrained semantic accents. (The first version of this revamp used an
H&E-stain palette; it was replaced on request.)

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `--bg` | `#F1F5F9` | `#0F172A` | page |
| `--surface` | `#FFFFFF` | `#1E293B` | panels |
| `--text` | `#0F172A` | `#E2E8F0` | ink |
| `--accent` (cobalt) | `#1D4ED8` | `#7EA6FF` | primary actions, selection, effect bars |
| `--teal` | `#0F766E` | `#5CC8C0` | active markers: playhead, viewport marks, the "measured" tag |
| `--warn` (amber) | `#8A4B08` | `#DFA64A` | the "illustrative" tag, warnings |
| `--bad` (crimson) | `#9F1239` | `#EE8A9C` | errors, the no-treatment curve |
| `--stage-0` | `#0B1220` | `#0B1220` | 3D stage, deep slate in both themes, not black |

The density ramp in the 3D view runs cobalt to cyan to near-white (`#24459C`, `#2F86D6`, `#5FD4D6`,
`#F2FEFF`), like a single-channel fluorescence readout. Scenario colours are still paired with a dash
pattern and a marker shape. Tags keep text plus shape.

## Type

Only the fonts already vendored are used. Geist for everything, Geist Mono with tabular figures for data.
Panel sub-headings are small uppercase with wide tracking. Instrument Serif is no longer used.
On screens 1800 px and wider the root size steps up so the page holds on a projector.

## Layout

```
top bar: brand · sections · on-this-device strip (API only) · data chip · theme
honesty line (sticky with the top bar)
title + privacy line + "Play the guided tour"
┌ 3D stage, transport, view options ────┐ ┌ four steps                ┐
│ burden chart (synced playhead)        │ │ patient picker            │
└───────────────────────────────────────┘ │ numbers, scenario table   │
                                          └───────────────────────────┘
How the explanation was checked: agents → tally → sentence ledger
Evidence · Model card (same content, restyled)
```

Two columns from 1000 px, one column below. On a phone the order is steps, picker, stage, chart, numbers.

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

Kept as they were: the data format, URL parameters, tags and their wording, "not available" for missing
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
