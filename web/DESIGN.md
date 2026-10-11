# OncoVision front end: design note

## Direction: the reading room

A calm instrument, not a landing page. The page is a light table with one dark viewport on it, the way a
radiology reading room has one lit screen. Density is moderate, layout is predictable, motion is functional.

The one thing to remember: **every sentence of the AI explanation carries its receipt.** The explanation is
shown as a ledger, one sentence per row, with where the sentence came from, the verified statements it
cites, and any objection the second agent raised next to the rule-based judge's ruling.

## Colour

The palette comes from the H&E stain used on pathology slides, which replaces the earlier near-black and cyan.

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `--bg` (slide glass) | `#EEF1F4` | `#100D1E` | page |
| `--surface` | `#FFFFFF` | `#18142B` | panels |
| `--text` (hematoxylin) | `#1E1838` | `#ECE9F6` | ink, structure |
| `--accent` (eosin) | `#B0305C` | `#F07FA3` | the one accent: playhead, active scenario, primary action |
| `--measured` | `#3F3A9E` | `#A9A4FF` | the "measured" tag |
| `--warn` | `#855300` | `#F0B840` | the "illustrative" tag, warnings |
| `--stage-0` (film) | `#0B0E14` | `#0B0E14` | 3D stage, dark in both themes |

The density ramp in the 3D view runs indigo, violet, eosin, pale rose (`#6650C8`, `#A862CC`, `#F0759A`,
`#FFF3F5`). Scenario colours are unchanged in hue and still paired with a dash pattern and a marker shape.
Tags keep text plus shape: filled dot (measured), dashed diamond (illustrative), hatch (sample data),
outlined square (AI-written).

## Type

Only the fonts already vendored are used; nothing new was added.

- Display: Geist at weight 640 with tight tracking. Instrument Serif italic is kept for one phrase in the
  page title and nowhere else.
- Body: Geist 15 px. Data, identifiers and stamps: Geist Mono with tabular figures.
- On screens 1800 px and wider the root size steps up so the page holds on a projector.

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

## Open points

- The console shows one failed request for `/api/health` on static hosting. A page cannot silence that
  line; it is the probe that hides the live-only controls.
- A "requests that left this origin: 0" counter was considered for the device strip and left out, because
  it would not come from `/api/health`.
