# Third-party material in `web/`

Everything below is vendored in `web/vendor/` so the viewer runs offline. Nothing is loaded from a CDN at run time.

| Item | Version | Licence | Where | Source |
| --- | --- | --- | --- | --- |
| three.js (ES module build) | 0.170.0 | MIT | `vendor/three.module.min.js`, `vendor/three-LICENSE` | https://github.com/mrdoob/three.js |
| three.js `OrbitControls` | 0.170.0 | MIT | `vendor/OrbitControls.js` (unmodified; imports `three` through the import map in `index.html`) | `examples/jsm/controls/OrbitControls.js` |
| Geist (variable, latin subset) | 5.3.0 (`@fontsource-variable/geist`) | SIL OFL 1.1 | `vendor/geist-latin-wght-normal.woff2`, `vendor/geist-LICENSE.txt` | https://github.com/vercel/geist-font |
| Geist Mono (variable, latin subset) | 5.3.0 (`@fontsource-variable/geist-mono`) | SIL OFL 1.1 | `vendor/geist-mono-latin-wght-normal.woff2`, `vendor/geist-mono-LICENSE.txt` | https://github.com/vercel/geist-font |
| Instrument Serif (regular and italic, latin subset) | 5.3.0 (`@fontsource/instrument-serif`) | SIL OFL 1.1 | `vendor/instrument-serif-latin-400-*.woff2`, `vendor/instrument-serif-LICENSE.txt` | https://github.com/Instrument/instrument-serif |

Files were downloaded once with `curl` from the jsDelivr npm mirror at the pinned versions above.

## Design and code references

No component code was copied from the following; each was read for the idea and re-implemented in `css/app.css` and `js/`.

| Reference | Licence | What was taken |
| --- | --- | --- |
| three.js examples `webgl_texture3d`, `webgl_volume_cloud` (MIT) | MIT | The approach for volume rendering: render the back faces of a unit box, ray-march a `Data3DTexture` in the fragment shader, composite front to back. The shader in `js/viewer3d.js` is written for this project (clip plane with cut-face cap, two-frame blend, density-gradient shading). |
| Magic UI "Bento Grid" (magicui.design) | MIT | The idea of a grid of unequal-span cards for the evidence overview. The cards here are plain CSS grid and carry the project's own content; no Magic UI code is included. |
| React Bits "Orb" (reactbits.dev) | MIT + Commons Clause | **Not used.** Its licence forbids redistributing the component, including as a ported version, so the loading orb in the stage is an original CSS radial-gradient animation. |
| Aceternity UI "3D card", 21st.dev | various | **Not used.** The 21st.dev plugin needs an API key and was skipped, as agreed. |

## Guidance used while building

- Anthropic `frontend-design` skill and the `threejs-*` skills from CloudAI-X/threejs-skills were installed project-locally (`.claude/skills/`, not committed) and used as guidance only.

## Data

Sample data in `data-fixture/` comes from MU-Glioma-Post (CC BY 4.0, TCIA). It is a derived, down-sampled density seed, not the original images.
