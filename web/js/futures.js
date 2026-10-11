// Slots for features whose data does not exist yet. Each one stays hidden unless its data is present.
// ?preview=future shows the empty designs, labelled as previews, with no numbers in them.
// Proposed fields are listed in ../DESIGN.md; nothing here changes the data format.
import { esc, isNum, NA, DATASET_LABEL } from "./format.js";

export const PREVIEW = (() => {
  try { return new URLSearchParams(location.search).get("preview") === "future"; } catch (e) { return false; }
})();
const previewTag = '<span class="tag tag-fixture">design preview</span>';

/** Dataset switch: appears when the patients in the file come from more than one dataset. */
export function initDatasetSwitch(el, patients, onChange) {
  if (!el) return;
  const sets = [...new Set(patients.map((p) => p.dataset).filter(Boolean))];
  if (sets.length < 2 && !PREVIEW) { el.hidden = true; return; }
  const btn = (v, label, on) => `<button type="button" data-ds="${esc(v)}" aria-pressed="${on}">${esc(label)}</button>`;
  el.innerHTML = `<h3 class="note">Dataset ${sets.length < 2 ? previewTag : ""}</h3><div class="segc" role="group" aria-label="Dataset">`
    + btn("", "All", true) + sets.map((s) => btn(s, DATASET_LABEL[s] || s, false)).join("") + `</div>`;
  el.hidden = false;
  el.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-ds]");
    if (!b) return;
    el.querySelectorAll("button[data-ds]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    onChange(b.dataset.ds ? patients.filter((p) => p.dataset === b.dataset.ds) : patients);
  });
}

/** Risk score with top drivers: appears when the case carries a `risk` block. */
export function renderRisk(el, p) {
  if (!el) return;
  const r = p && p.risk;
  if (!r && !PREVIEW) { el.hidden = true; return; }
  el.hidden = false;
  el.classList.toggle("is-preview", !r);
  if (!r) {
    el.innerHTML = `<h3>Risk score ${previewTag}</h3><p class="ro-big">${NA}</p><p class="note">Appears when a patient carries a risk score and its top drivers.</p>`;
    return;
  }
  const max = Math.max(...r.drivers.map((d) => Math.abs(d.weight)), 0) || 1;
  el.innerHTML = `<h3>Risk score</h3><p class="ro-big">${isNum(r.score) ? r.score.toFixed(2) : NA}</p>`
    + (r.drivers.length ? `<ol class="drivers" aria-label="Top drivers">${r.drivers.map((d) =>
      `<li><span>${esc(d.label)}</span><span class="bar-track"><span style="width:${(Math.abs(d.weight) / max) * 100}%"></span></span><span class="mono">${d.weight > 0 ? "+" : d.weight < 0 ? "−" : ""}${Math.abs(d.weight).toFixed(2)}</span></li>`).join("")}</ol>`
      : `<p class="note">Drivers ${NA}.</p>`);
}

/** Upload drop zone for a synthetic patient: appears when /api/health reports upload: true. */
export function initUpload(el, api) {
  if (!el) return;
  const live = !!(api && api.ok && api.health && api.health.upload === true);
  if (!live && !PREVIEW) { el.hidden = true; return; }
  el.hidden = false;
  el.classList.toggle("is-preview", !live);
  el.innerHTML = `<h3>Add a synthetic patient ${live ? "" : previewTag}</h3>
    <div class="drop" tabindex="0" role="button" aria-describedby="drop-status">Drop a segmentation file here</div>
    <p id="drop-status" class="note" aria-live="polite">Synthetic data only. The file stays on this device.</p>`;
  const drop = el.querySelector(".drop"), status = el.querySelector("#drop-status");
  const off = () => { status.textContent = "Upload is not connected in this build."; };
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("is-over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("is-over"); }));
  drop.addEventListener("drop", off);
  drop.addEventListener("click", off);
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); off(); } });
}
