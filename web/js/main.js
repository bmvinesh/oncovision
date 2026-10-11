// OncoVision viewer: wires data, 3D stage, chart, transport and readouts to one store.
import * as THREE from "three";
import { createStore } from "./store.js";
import { loadCohort, fetchFrames, maxOf, valuesAt, scanIndexAt, interp } from "./data.js";
import { Viewer, webglSupported } from "./viewer3d.js";
import { Chart } from "./chart.js";
import { Picker } from "./picker.js";
import { renderFromData } from "./modelcard.js";
import { renderExplanation, probeApi } from "./explain.js";
import { renderDevice } from "./device.js";
import { initTour } from "./tour.js";
import { initDatasetSwitch, renderRisk, initUpload } from "./futures.js";
import {
  fmtVol, fmtPct, fmtDay, fmtNum, isNum, NA, esc, scenInfo, statusInfo, DATASET_LABEL, AGENT_LABEL, markerSvg,
} from "./format.js";

const $ = (id) => document.getElementById(id);
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const N = 32; // display grid edge

const store = createStore({
  patient: null,
  scenario: "no_further_treatment",
  compare: "",
  t: 0,
  playing: false,
  speed: 1,
  log: false,
  visible: new Set(),
  loadId: 0,        // bumps when frame sets change, to re-render
});

let cohort = null;
let viewer = null;
let chart = null;
let picker = null;
let glOk = false;
let frameSet = { id: null, obs: null, scen: {} }; // buffers for the selected patient
let loadToken = 0;
let opts = { clip: { on: false, axis: "z", pos: 0.5, flip: false }, opacity: 2, scale: "patient" };

// ---------- theme ----------
function initTheme() {
  const btn = $("theme-btn");
  const eff = () => document.documentElement.dataset.theme
    || (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  btn.addEventListener("click", () => {
    const next = eff() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("oncovision-theme", next); } catch (e) { /* storage may be blocked */ }
    btn.setAttribute("aria-label", `Switch to ${next === "dark" ? "light" : "dark"} theme`);
    chart && chart.render();
  });
  btn.setAttribute("aria-label", `Switch to ${eff() === "dark" ? "light" : "dark"} theme`);
}

// ---------- stage state ----------
function stageState(text, { loading = false, progress = null, error = false, hide = false } = {}) {
  const el = $("stage-state");
  if (hide && el.hidden) return;
  el.hidden = hide;
  el.classList.toggle("is-error", error);
  $("stage-state-text").textContent = text;
  const bar = $("stage-bar");
  bar.hidden = !loading;
  bar.classList.toggle("indeterminate", loading && progress === null);
  bar.firstElementChild.style.width = progress === null ? "" : `${Math.round(progress * 100)}%`;
}

// ---------- patient selection and frame loading ----------
function selectPatient(p, { scenario, t } = {}) {
  const s = store.get();
  const token = ++loadToken;
  const key = scenario && p.scenarios[scenario] ? scenario
    : p.scenarios[s.scenario] ? s.scenario : p.scenarioKeys[0];
  frameSet = { id: p.uid, obs: null, scen: {}, maxRaw: 0 };
  const vis = new Set(p.scenarioKeys);
  const cmp = s.compare && s.compare !== key && p.scenarios[s.compare] ? s.compare : "";
  store.set({
    patient: p, scenario: key, compare: cmp, t: isNum(t) ? Math.min(p.tEnd, Math.max(0, t)) : p.lastScan,
    playing: false, visible: vis, loadId: s.loadId + 1,
  });
  picker.select(p.uid);
  $("picker-title").textContent = p.id;
  if (window.matchMedia("(max-width: 1099px)").matches) $("picker").open = false;
  ensureFrames(p, token);
}

async function ensureFrames(p, token) {
  if (!p.hasFrames) { stageState("3D frames are not available for this patient. The chart and numbers still work.", { error: true }); return; }
  const { scenario, compare } = store.get();
  const wanted = [];
  if (p.observedFrames && !frameSet.obs) wanted.push({ kind: "obs", spec: p.observedFrames });
  for (const k of [scenario, compare].filter(Boolean)) {
    const spec = p.scenarios[k].frames;
    if (spec && !frameSet.scen[k]) wanted.push({ kind: "scen", key: k, spec });
  }
  if (!wanted.length) { updateScale(); store.set({ loadId: store.get().loadId + 1 }); return; }
  stageState("Loading 3D frames", { loading: true, progress: 0 });
  const total = wanted.length;
  const prog = new Array(total).fill(0);
  try {
    await Promise.all(wanted.map(async (w, i) => {
      const buf = await fetchFrames(cohort.root, w.spec, (f) => { prog[i] = f; if (token === loadToken) stageState("Loading 3D frames", { loading: true, progress: prog.reduce((a, b) => a + b, 0) / total }); });
      if (token !== loadToken) return;
      if (w.kind === "obs") frameSet.obs = buf; else frameSet.scen[w.key] = buf;
    }));
  } catch (e) {
    if (token === loadToken) stageState(`Could not load 3D frames: ${e.message}`, { error: true });
    console.error(e);
    return;
  }
  if (token !== loadToken) return;
  updateScale();
  if (viewer && frameSet.focusFor !== p.uid) {
    const f = tumourFocus();
    if (f) { viewer.setFocus(f.center, f.radius); frameSet.focusFor = p.uid; }
  }
  stageState("", { hide: true });
  store.set({ loadId: store.get().loadId + 1 });
}

/** Bounding sphere (object space) of the voxels that carry real signal across all loaded frames. */
function tumourFocus() {
  const bufs = [frameSet.obs, ...Object.values(frameSet.scen)].filter(Boolean);
  if (!bufs.length || !frameSet.maxRaw) return null;
  const thr = Math.max(2, frameSet.maxRaw * 0.12);
  const lo = [N, N, N], hi = [-1, -1, -1];
  for (const b of bufs) {
    for (let k = 0; k < b.length; k++) {
      if (b[k] >= thr) {
        const i = k % VOX, x = i % N, y = ((i / N) | 0) % N, z = (i / (N * N)) | 0;
        if (x < lo[0]) lo[0] = x; if (x > hi[0]) hi[0] = x;
        if (y < lo[1]) lo[1] = y; if (y > hi[1]) hi[1] = y;
        if (z < lo[2]) lo[2] = z; if (z > hi[2]) hi[2] = z;
      }
    }
  }
  if (hi[0] < 0) return null;
  const c = lo.map((l, a) => ((l + hi[a] + 1) / 2) / N - 0.5);
  const r = Math.hypot(...lo.map((l, a) => (hi[a] - l + 1) / N)) / 2 + 0.05;
  return { center: c, radius: r };
}

function updateScale() {
  let m = frameSet.obs ? maxOf(frameSet.obs) : 0;
  if (!frameSet.maxCache) frameSet.maxCache = new Map();
  for (const [k, b] of Object.entries(frameSet.scen)) {
    if (!frameSet.maxCache.has(k)) frameSet.maxCache.set(k, maxOf(b));
    m = Math.max(m, frameSet.maxCache.get(k));
  }
  frameSet.maxRaw = m;
}

const VOX = N * N * N;
function frameSlice(buf, i) { return buf.subarray(i * VOX, (i + 1) * VOX); }

/** What to show in 3D for scenario `key` at time t. Returns null while loading. */
function frameFor(p, key, t) {
  const spec = p.scenarios[key] && p.scenarios[key].frames;
  const obsN = p.observedFrames ? p.observedFrames.shape[0] : 0;
  if (t <= p.lastScan + 1e-6 || !spec) {
    if (!frameSet.obs || !obsN) {
      if (spec && frameSet.scen[key]) return { a: { id: `${p.id}|${key}|0`, data: frameSlice(frameSet.scen[key], 0) }, b: null, mix: 0, note: null };
      return null;
    }
    const idx = Math.min(obsN - 1, scanIndexAt(p, t));
    const d = frameSlice(frameSet.obs, idx);
    return { a: { id: `${p.id}|obs|${idx}`, data: d }, b: { id: `${p.id}|obs|${idx}`, data: d }, mix: 0,
      note: t > p.lastScan + 1e-6 ? "no 3D frames for this scenario; showing the last scan" : null };
  }
  const buf = frameSet.scen[key];
  if (!buf) return null;
  const T = spec.shape[0];
  const days = spec.days && spec.days.length === T ? spec.days : Array.from({ length: T }, (_, i) => p.lastScan + (i * (p.tEnd - p.lastScan)) / (T - 1));
  let j = 0;
  while (j < T - 2 && days[j + 1] <= t) j++;
  const f = Math.min(1, Math.max(0, (t - days[j]) / (days[j + 1] - days[j] || 1)));
  return { a: { id: `${p.id}|${key}|${j}`, data: frameSlice(buf, j) }, b: { id: `${p.id}|${key}|${j + 1}`, data: frameSlice(buf, j + 1) }, mix: f, note: null };
}

// ---------- rendering ----------
const RAMP = [[0x24, 0x45, 0x9c], [0x2f, 0x86, 0xd6], [0x5f, 0xd4, 0xd6], [0xf2, 0xfe, 0xff]];
function rampRGB(d) {
  d = Math.min(1, Math.max(0, d));
  const sm = (a, b, x) => { const u = Math.min(1, Math.max(0, (x - a) / (b - a))); return u * u * (3 - 2 * u); };
  const mix = (c1, c2, f) => c1.map((v, i) => v + (c2[i] - v) * f);
  let c = mix(RAMP[0], RAMP[1], sm(0, 0.35, d));
  c = mix(c, RAMP[2], sm(0.3, 0.7, d));
  return mix(c, RAMP[3], sm(0.7, 1, d));
}

const sliceCanvas = $("slice");
const sliceCtx = sliceCanvas.getContext("2d");
const sliceTmp = document.createElement("canvas");
sliceTmp.width = sliceTmp.height = N;
const sliceTmpCtx = sliceTmp.getContext("2d");

function drawSlice(fr, invMax) {
  if (!fr) return;
  const { axis, pos } = opts.clip;
  const q = Math.min(N - 1, Math.max(0, pos * N - 0.5));
  const i0 = Math.floor(q), i1 = Math.min(N - 1, i0 + 1), w = q - i0;
  const img = sliceTmpCtx.createImageData(N, N);
  const A = fr.a.data, B = (fr.b || fr.a).data, mx = fr.mix;
  const at = (z, y, x) => { const k = (z * N + y) * N + x; return (A[k] * (1 - mx) + B[k] * mx) / 255; };
  for (let r = 0; r < N; r++) {
    for (let c = 0; c < N; c++) {
      const v = (zz, yy, xx) => at(zz, yy, xx);
      let d;
      if (axis === "z") d = v(i0, r, c) * (1 - w) + v(i1, r, c) * w;
      else if (axis === "y") d = v(r, i0, c) * (1 - w) + v(r, i1, c) * w;
      else d = v(r, c, i0) * (1 - w) + v(r, c, i1) * w;
      const dn = d * invMax;
      const col = rampRGB(dn).map((c, k) => [11, 18, 32][k] + (c - [11, 18, 32][k]) * Math.min(1, dn / 0.06));
      const o = (r * N + c) * 4;
      img.data[o] = col[0]; img.data[o + 1] = col[1]; img.data[o + 2] = col[2]; img.data[o + 3] = 255;
    }
  }
  sliceTmpCtx.putImageData(img, 0, 0);
  sliceCtx.imageSmoothingEnabled = true;
  sliceCtx.imageSmoothingQuality = "high";
  sliceCtx.clearRect(0, 0, 128, 128);
  sliceCtx.drawImage(sliceTmp, 0, 0, 128, 128);
  $("slice-label").textContent = `${axis.toUpperCase()} = ${Math.round(pos * 100)}% plane`;
}

function render3D() {
  const s = store.get(), p = s.patient;
  if (!p || !p.hasFrames) return;
  const keys = [s.scenario, ...(s.compare ? [s.compare] : [])];
  const frames = keys.map((k) => frameFor(p, k, s.t));
  const invMax = opts.scale === "patient" && frameSet.maxRaw > 0 ? 255 / frameSet.maxRaw : 1;
  const split = $("split-labels");
  split.hidden = keys.length < 2;
  if (keys.length > 1) split.innerHTML = keys.map((k) => `<span>${esc(scenInfo(k).label)}</span>`).join("");
  const have = frames.every(Boolean);
  if (!have) return; // still loading; the loading state is already visible
  stageState("", { hide: true });
  const notes = frames.map((f) => f.note).filter(Boolean);
  $("stage-tags").dataset.note = notes[0] || "";
  if (viewer) {
    viewer.setOptions({ invMax, opacity: opts.opacity, clip: opts.clip });
    viewer.setLayers(frames.map((f) => ({ aId: f.a.id, aData: f.a.data, bId: (f.b || f.a).id, bData: (f.b || f.a).data, mix: f.mix })));
  }
  if (opts.clip.on || !glOk) drawSlice(frames[0], invMax);
  if (!glOk) updateScaleBar(null);
  // legend
  const maxD = opts.scale === "patient" && frameSet.maxRaw > 0 ? frameSet.maxRaw / 255 : 1;
  $("lg-1").textContent = (maxD / 2).toFixed(2);
  $("lg-2").textContent = maxD.toFixed(2);
  $("lg-scale").textContent = opts.scale === "patient"
    ? `Colours stretched to this patient's maximum (${maxD.toFixed(2)}); model seed values run 0 to 1.`
    : "Absolute scale: model seed density 0 to 1.";
}

function renderReadouts() {
  const s = store.get(), p = s.patient;
  if (!p) return;
  const v = valuesAt(p, s.scenario, s.t);
  $("ro-day").textContent = fmtDay(s.t);
  const phaseText = v.phase === "observed"
    ? `Scan ${v.scanIndex + 1} of ${v.scanCount}, day ${fmtDay(v.scanDay)}. Measured.`
    : `Forecast, ${fmtDay(v.daysSinceLast)} days after the last scan. Illustrative.`;
  $("ro-phase").textContent = phaseText;
  $("t-phase").textContent = v.phase === "observed"
    ? `Showing the model seed built from scan ${v.scanIndex + 1} of ${v.scanCount} (day ${fmtDay(v.scanDay)}). Measured scans are not interpolated.`
    : `Forecast under "${scenInfo(s.scenario).label}", ${fmtDay(v.daysSinceLast)} days after the last scan. Illustrative.`;
  $("t-readout").textContent = `Day ${fmtDay(s.t)}`;
  $("ro-burden-tag").innerHTML = v.phase === "observed" ? '<span class="tag tag-measured">measured</span>' : '<span class="tag tag-illustrative">illustrative</span>';
  $("ro-burden").textContent = fmtVol(v.burden);
  $("ro-vs-last").textContent = fmtPct(v.vsLastPct, 0);
  $("ro-vs-none").textContent = v.phase === "forecast" ? fmtPct(v.vsNonePct, 1) : "not applicable before the forecast";
  const volDt = $("ro-vol").previousElementSibling;
  if (v.phase === "observed") {
    volDt.textContent = "Segmented tumour volume (measured)";
    $("ro-vol").textContent = fmtVol(v.scan.tumor);
  } else {
    volDt.textContent = "Volume at density 0.5 or more";
    $("ro-vol").textContent = !isNum(v.volume) ? NA : v.volume <= 0 ? "below 0.5 everywhere" : fmtVol(v.volume);
  }
  renderHud(v);
  updateScenTable(p, s.t);
  const rng = $("t-range");
  if (Number(rng.value) !== Math.round(s.t)) rng.value = String(Math.round(s.t));
  rng.setAttribute("aria-valuetext", v.phase === "observed" ? `day ${fmtDay(s.t)}, measured scan ${v.scanIndex + 1}` : `day ${fmtDay(s.t)}, forecast ${fmtDay(v.daysSinceLast)} days after last scan`);
  // keep slider fill hint using CSS var
  rng.style.setProperty("--p", `${(s.t / p.tEnd) * 100}%`);
}

/** Floating readout in the viewport: what the volume is made of at this time, from the data file only. */
function renderHud(v) {
  const row = (dot, k, val) => `<div><i class="${dot}"></i><dt>${k}</dt><dd>${esc(val)}</dd></div>`;
  const obs = v.phase === "observed";
  $("hud").hidden = false;
  $("hud").innerHTML = obs
    ? `<h4><span>Segmentation, scan ${v.scanIndex + 1} of ${v.scanCount}</span><span>measured</span></h4><dl>`
      + row("d-a", "Segmented tumour", fmtVol(v.scan.tumor)) + row("d-b", "Tumour core", fmtVol(v.scan.core))
      + row("d-c", "Resection cavity", fmtVol(v.scan.cavity)) + row("d-d", "Model seed burden", fmtVol(v.burden)) + `</dl>`
    : `<h4><span>Forecast, +${fmtDay(v.daysSinceLast)} d</span><span>illustrative</span></h4><dl>`
      + row("d-d", "Modelled burden", fmtVol(v.burden))
      + row("d-b", "Volume, density ≥ 0.5", !isNum(v.volume) ? NA : v.volume <= 0 ? "below 0.5" : fmtVol(v.volume))
      + row("d-c", "Since last scan", fmtPct(v.vsLastPct, 0)) + `</dl>`;
}

/** Calibrated scale bar. In 3D it is exact at the depth the camera orbits around; in 2D it is exact. */
function updateScaleBar(camera) {
  const p = store.get().patient, el = $("viewbar");
  const bx = p && p.boxMm;
  if (!bx || !bx.every((v) => Math.abs(v - bx[0]) < 0.5)) { el.hidden = true; return; }
  let pxPerMm;
  if (glOk && viewer && camera) {
    const dist = camera.position.distanceTo(viewer.controls.target);
    pxPerMm = $("stage").clientHeight / (2 * dist * Math.tan((camera.fov * Math.PI) / 360)) / bx[0];
  } else {
    pxPerMm = $("slice").clientWidth / bx[0];
  }
  if (!(pxPerMm > 0)) { el.hidden = true; return; }
  const mm = [1, 2, 5, 10, 20, 50, 100, 200].filter((m) => m * pxPerMm <= 140).pop() || 1;
  el.hidden = false;
  $("vb-bar").style.width = `${Math.round(mm * pxPerMm)}px`;
  $("vb-label").innerHTML = `<b>${mm} mm</b>${glOk ? " at the focus depth" : ""}`;
  const c = opts.clip;
  $("vb-slice").textContent = c.on ? `Cut plane ${c.axis.toUpperCase()} at ${Math.round(c.pos * bx[0])} mm of ${Math.round(bx[0])} mm` : "";
}

function renderParams(p) {
  const row = (k, val, unit, src) => `<div><dt>${k}</dt><dd>${esc(val)}${unit && val !== NA ? `<small>${unit}</small>` : ""}</dd>${src ? `<span class="src">${src}</span>` : ""}</div>`;
  const shr = p.status === "shrinking";
  let h = row("Proliferation rate ρ", fmtNum(p.rho, 4), "per day", shr ? "held fixed for this patient" : "calibrated on this patient's first interval")
    + row("Diffusion D", isNum(p.D) ? String(p.D) : NA, "mm²/day", "not fitted")
    + row("Loss rate δ", fmtNum(p.delta, 4), "per day", shr ? "fitted, shrinking tumour" : "")
    + row("Simulation grid", isNum(p.gridCells) && isNum(p.spacingMm) ? `${p.gridCells}³ at ${p.spacingMm}` : NA, "mm", "")
    + row("Forecast horizon", fmtDay(p.tEnd - p.lastScan), "days", "");
  const vals = (cohort.meta.effects && cohort.meta.effects.values) || {};
  for (const [a, e] of Object.entries(vals)) {
    if (e && isNum(e.suppress) && e.suppress > 0) h += row(`${esc(AGENT_LABEL[a] || a)}: growth suppression`, (e.suppress * 100).toFixed(0), "%", "literature prior, illustrative");
  }
  $("param-list").innerHTML = h;
  $("scen-select").innerHTML = p.scenarioKeys.map((k) => `<option value="${esc(k)}">${esc(scenInfo(k).label)}</option>`).join("");
}

function renderEffect(p) {
  const el = $("effect-panel");
  const rows = p.scenarioKeys.map((k) => ({ k, v: valuesAt(p, k, p.tEnd).vsNonePct })).filter((r) => r.k !== "no_further_treatment");
  if (!p.scenarios.no_further_treatment || !rows.some((r) => isNum(r.v))) { el.hidden = true; return; }
  const max = Math.max(...rows.map((r) => (isNum(r.v) ? Math.abs(r.v) : 0))) || 1;
  el.hidden = false;
  el.innerHTML = `<h3 class="panel-h">Scenario effect at the end of the forecast <span class="tag tag-illustrative">illustrative</span></h3>`
    + `<ol class="effect">${rows.map((r) => `<li><span>${esc(scenInfo(r.k).label)}</span><span class="track"><span style="width:${isNum(r.v) ? (Math.abs(r.v) / max) * 100 : 0}%"></span></span><span class="v">${fmtPct(r.v, 1)}</span></li>`).join("")}</ol>`
    + `<p class="note">Modelled burden compared with no further treatment at day ${fmtDay(p.tEnd)}. The effect sizes are literature priors, not measured in this cohort.</p>`;
}

/** Burden curves and scans as CSV. Missing values are left empty, never written as 0. */
function exportCsv() {
  const p = store.get().patient;
  if (!p) return;
  const n = (v) => (isNum(v) ? String(v) : "");
  const lines = ["patient,series,tag,day,burden_mm3,volume_density_ge_0.5_mm3,segmented_tumour_mm3"];
  p.observed.forEach((o) => lines.push([p.id, "observed", "measured", n(o.day), n(o.seed), "", n(o.tumor)].join(",")));
  p.scenarioKeys.forEach((k) => { const s = p.scenarios[k]; s.day.forEach((d, i) => lines.push([p.id, k, "illustrative", n(d), n(s.burden[i]), n(s.volume ? s.volume[i] : null), ""].join(","))); });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([lines.join("\n") + "\n"], { type: "text/csv" }));
  a.download = `${p.id}_whatif.csv`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function buildScenTable(p) {
  const tb = document.querySelector("#scen-table tbody");
  const s = store.get();
  tb.innerHTML = p.scenarioKeys.map((k, i) => {
    const si = scenInfo(k, i);
    const sw = `<svg viewBox="0 0 28 10" width="28" height="10" aria-hidden="true"><line x1="1" x2="27" y1="5" y2="5" stroke="${si.color}" stroke-width="2.4" ${si.dash ? `stroke-dasharray="${si.dash}"` : ""} stroke-linecap="round"/><g transform="translate(14,5)" style="color:${si.color}">${markerSvg(si.marker, 3, 'fill="var(--surface)" stroke="currentColor" stroke-width="1.6"')}</g></svg>`;
    return `<tr data-k="${esc(k)}"><td><div class="scen-name">`
      + `<input type="radio" name="scen3d" value="${esc(k)}" aria-label="Show ${esc(si.label)} in 3D" ${k === s.scenario ? "checked" : ""}>`
      + `<input type="checkbox" data-vis="${esc(k)}" aria-label="Show ${esc(si.label)} curve in chart" ${s.visible.has(k) ? "checked" : ""}>`
      + `<span class="swatch">${sw}</span><span>${esc(si.label)}</span></div></td>`
      + `<td class="num" data-c="b">${NA}</td><td class="num" data-c="n">${NA}</td></tr>`;
  }).join("");
}

function updateScenTable(p, t) {
  const rows = document.querySelectorAll("#scen-table tbody tr");
  const s = store.get();
  rows.forEach((tr) => {
    const k = tr.dataset.k;
    const v = valuesAt(p, k, t);
    tr.querySelector('[data-c="b"]').textContent = fmtVol(v.burden);
    tr.querySelector('[data-c="n"]').textContent = v.phase === "forecast" ? (k === "no_further_treatment" ? "reference" : fmtPct(v.vsNonePct, 1)) : "n/a";
    tr.classList.toggle("is-active", k === s.scenario);
  });
}

function renderPatientStatic(p) {
  // header of stage
  $("lbl-patient").textContent = `${p.id}`;
  $("case-chip").hidden = false;
  $("case-chip").innerHTML = `Case <b>${esc(p.id)}</b>${p.dataset ? ` · ${esc(DATASET_LABEL[p.dataset] || p.dataset)}` : ""}`;
  renderParams(p);
  renderEffect(p);
  const tags = ['<span class="tag tag-illustrative">illustrative forecast</span>', '<span class="tag tag-measured">measured scans</span>'];
  if (cohort.kind === "fixture") tags.push('<span class="tag tag-fixture">sample data</span>');
  $("stage-tags").innerHTML = tags.join("");
  const bx = p.boxMm;
  const boxTxt = !bx ? null : bx.every((v) => Math.abs(v - bx[0]) < 0.5) ? `${Math.round(bx[0])} mm` : bx.map((v) => Math.round(v)).join(" × ") + " mm";
  $("scalebar").textContent = boxTxt ? `Box edge ${boxTxt}, shown on a ${N}³ grid` : `Box size ${NA}`;

  // warnings
  const warn = $("ro-warn");
  const st = p.status;
  if (st === "no_convergence" || st === "empty_seed") {
    warn.className = "callout";
    warn.innerHTML = `<strong>Fit did not converge.</strong> The growth rate hit its bound${isNum(p.rho) ? ` (ρ = ${p.rho.toFixed(2)} per day)` : ""}, so these curves are not plausible. They are shown for transparency and should not be read as a forecast.`;
    warn.hidden = false;
  } else if (st === "shrinking") {
    warn.className = "callout is-info";
    warn.innerHTML = `<strong>Shrinking tumour.</strong> Growth rate ρ was held fixed and a loss rate δ${isNum(p.delta) ? ` of ${p.delta.toFixed(4)} per day` : ""} was fitted. The model says nothing about why it shrank.`;
    warn.hidden = false;
  } else if (!st) {
    warn.className = "callout";
    warn.innerHTML = `<strong>Fit status ${NA}.</strong>`;
    warn.hidden = false;
  } else { warn.hidden = true; }

  const onNarr = (n) => { p.narrative = n; $("checked-empty").hidden = !$("ro-explain").hidden; };
  renderExplanation($("ro-explain"), p.narrative, { uid: p.uid, onUpdate: onNarr });
  $("checked-empty").hidden = !$("ro-explain").hidden;
  renderRisk($("risk-slot"), p);

  // what these numbers rest on
  const c = p.calib;
  const rhoLine = st === "shrinking"
    ? `Growth rate ρ is not fitted here (held fixed); loss rate δ = ${fmtNum(p.delta, 4)} per day.`
    : `Growth rate ρ = ${fmtNum(p.rho, 4)} per day, calibrated on this patient's first interval${c && isNum(c.ratio_target) ? ` (observed burden ratio ${c.ratio_target.toFixed(2)}, simulated ${isNum(c.ratio_achieved) ? c.ratio_achieved.toFixed(2) : NA})` : ""}.`;
  const nonePath = p.scenarios.as_recorded && p.scenarios.no_further_treatment
    && p.scenarios.as_recorded.burden.every((b, i) => Math.abs(b - p.scenarios.no_further_treatment.burden[i]) < 1e-6 * Math.max(1, b));
  $("ro-rests").innerHTML =
    `<p>${rhoLine} Diffusion D = ${isNum(p.D) ? p.D : NA} mm²/day is not fitted.</p>`
    + `<p>Treatment effects are <strong>not</strong> estimated from this cohort. In retrospective charts, treated intervals do not grow more slowly than untreated ones, because treatment is given to tumours that are already progressing. Scenario effects are literature priors, so every scenario curve is illustrative.</p>`
    + (p.assumptions.length ? `<p>Assumptions about this patient's recorded treatment:</p><ul>${p.assumptions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>` : "<p>No schedule assumptions were needed for this patient.</p>");

  const last = p.observed[p.observed.length - 1];
  const f = [
    ["Dataset", DATASET_LABEL[p.dataset] || p.dataset || NA],
    ["Scans", String(p.observed.length)],
    ["Follow-up measured", `${fmtDay(p.lastScan)} days`],
    ["Forecast horizon", `${fmtDay(p.tEnd - p.lastScan)} days`],
    ["Burden at last scan (measured seed)", fmtVol(last.seed)],
    ["Segmented tumour at last scan", fmtVol(last.tumor)],
    ["Resection cavity at last scan", fmtVol(last.cavity)],
    ["Fit status", statusInfo(p.status).label],
    ["Age at first scan", isNum(p.age) ? `${Math.round(p.age)} years` : NA],
    ["Sex", p.sex ?? NA],
    ["Diagnosis", p.diagnosis ?? NA],
    ["WHO grade", isNum(p.whoGrade) ? String(p.whoGrade) : NA],
    ["IDH status", p.idh === "wt" ? "wild-type" : p.idh === "mut" ? "mutant" : p.idh ?? NA],
    ["MGMT promoter", p.mgmt ?? NA],
  ];
  $("ro-facts").innerHTML = f.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("");

  // chart note
  const bits = ["Circles are measured scans (seed burden). Hatched area is forecast and illustrative."];
  if (p.scenarioKeys.some((k) => p.scenarios[k].lo && p.scenarios[k].hi)) bits.push("The shaded band is the uncertainty range for the selected scenario.");
  if (nonePath) bits.push('"As recorded" matches "No further treatment" because nothing was recorded after the last scan.');
  $("chart-note").textContent = bits.join(" ");

  // scrubber range
  const rng = $("t-range");
  rng.max = String(Math.round(p.tEnd));
  rng.min = "0";
  $("compare").innerHTML = `<option value="">Nothing</option>` + p.scenarioKeys.map((k) => `<option value="${esc(k)}">${esc(scenInfo(k).label)}</option>`).join("");
  buildScenTable(p);
}

function renderAll(s, changed) {
  const p = s.patient;
  if (!p) return;
  const full = !changed || changed.includes("patient");
  if (full) {
    renderPatientStatic(p);
  }
  if (full || changed.includes("scenario")) {
    $("lbl-scenario").textContent = scenInfo(s.scenario).label;
    document.querySelectorAll('#scen-table input[name="scen3d"]').forEach((r) => { r.checked = r.value === s.scenario; });
    $("scen-select").value = s.scenario;
  }
  if (full || changed.includes("compare")) {
    $("compare").value = s.compare || "";
    $("compare").querySelectorAll("option").forEach((o) => { o.disabled = o.value === s.scenario; });
  }
  if (full || changed.some((c) => ["visible", "scenario", "log"].includes(c))) {
    chart.setData({ patient: p, visible: s.visible, active: s.scenario, log: s.log });
    $("chart-log").checked = s.log;
  }
  chart.setTime(s.t);
  renderReadouts();
  render3D();
  syncUrl();
}

let urlTimer = null;
function syncUrl() {
  clearTimeout(urlTimer);
  urlTimer = setTimeout(() => {
    const s = store.get();
    if (!s.patient) return;
    const u = new URL(location.href);
    u.searchParams.set("p", s.patient.id);
    u.searchParams.set("s", s.scenario);
    u.searchParams.set("t", String(Math.round(s.t)));
    try { history.replaceState(null, "", u); } catch (e) { /* ignore */ }
  }, 400);
}

// ---------- playback ----------
let raf = 0, last = 0;
function tick(now) {
  const s = store.get();
  if (!s.playing || !s.patient) return;
  const dt = Math.min(0.1, (now - last) / 1000);
  last = now;
  const span = s.patient.tEnd;
  // 30 days per second in the forecast; the measured stretch is stepped through three times faster
  const rate = s.t < s.patient.lastScan ? 90 : 30;
  const nt = s.t + dt * rate * s.speed;
  if (nt >= span) { store.set({ t: span, playing: false }); return; }
  store.set({ t: nt });
  raf = requestAnimationFrame(tick);
}
function setPlaying(on) {
  const s = store.get();
  if (!s.patient) return;
  if (on && s.t >= s.patient.tEnd - 0.5) store.set({ t: 0 });
  store.set({ playing: on });
  if (on) { last = performance.now(); cancelAnimationFrame(raf); raf = requestAnimationFrame(tick); }
}

// ---------- controls ----------
function bindControls() {
  $("play").addEventListener("click", () => setPlaying(!store.get().playing));
  $("t-range").addEventListener("input", (e) => { store.set({ playing: false, t: Number(e.target.value) }); });
  $("speed").addEventListener("change", (e) => store.set({ speed: Number(e.target.value) }));
  $("j-first").addEventListener("click", () => store.set({ playing: false, t: 0 }));
  $("j-last").addEventListener("click", () => { const p = store.get().patient; if (p) store.set({ playing: false, t: p.lastScan }); });
  $("j-end").addEventListener("click", () => { const p = store.get().patient; if (p) store.set({ playing: false, t: p.tEnd }); });
  $("chart-log").addEventListener("change", (e) => store.set({ log: e.target.checked }));
  $("scen-select").addEventListener("change", (e) => {
    const s = store.get();
    store.set({ scenario: e.target.value, compare: s.compare === e.target.value ? "" : s.compare });
    ensureFrames(s.patient, loadToken);
  });
  $("export-csv").addEventListener("click", exportCsv);
  $("export-print").addEventListener("click", () => window.print());
  const dlg = $("method");
  $("method-btn").addEventListener("click", () => (dlg.showModal ? dlg.showModal() : dlg.setAttribute("open", "")));
  $("method-close").addEventListener("click", () => dlg.close());
  dlg.addEventListener("click", (e) => { if (e.target === dlg || e.target.closest("[data-close]")) dlg.close(); });

  $("scen-table").addEventListener("change", (e) => {
    const t = e.target;
    const s = store.get();
    if (t.name === "scen3d") {
      const token = loadToken;
      const cmp = s.compare === t.value ? "" : s.compare;
      store.set({ scenario: t.value, compare: cmp });
      ensureFrames(s.patient, token);
    } else if (t.dataset.vis) {
      const v = new Set(s.visible);
      if (t.checked) v.add(t.dataset.vis); else v.delete(t.dataset.vis);
      store.set({ visible: v });
    }
  });
  const warm = (e) => {
    const tr = e.target.closest && e.target.closest("tr[data-k]");
    const s = store.get();
    if (!tr || !s.patient || !glOk) return;
    const k = tr.dataset.k, spec = s.patient.scenarios[k] && s.patient.scenarios[k].frames;
    if (!spec || frameSet.scen[k]) return;
    const token = loadToken;
    fetchFrames(cohort.root, spec).then((b) => { if (token === loadToken) { frameSet.scen[k] = b; updateScale(); } }).catch(() => {});
  };
  $("scen-table").addEventListener("pointerover", warm);
  $("scen-table").addEventListener("focusin", warm);
  $("compare").addEventListener("change", (e) => {
    const s = store.get();
    store.set({ compare: e.target.value });
    if (e.target.value) ensureFrames(s.patient, loadToken);
  });

  const clip = () => {
    opts.clip = {
      on: $("clip-on").checked,
      axis: document.querySelector('input[name="axis"]:checked').value,
      pos: Number($("clip-pos").value) / 100,
      flip: $("clip-flip").checked,
    };
    $("clip-pos").setAttribute("aria-valuetext", `${$("clip-pos").value} percent`);
    $("slice-wrap").hidden = !(opts.clip.on || !glOk);
    render3D();
  };
  ["clip-on", "clip-pos", "clip-flip"].forEach((id) => $(id).addEventListener("input", clip));
  document.querySelectorAll('input[name="axis"]').forEach((r) => r.addEventListener("change", clip));
  $("opacity").addEventListener("input", (e) => { opts.opacity = Number(e.target.value) / 100; render3D(); });
  $("dens-scale").addEventListener("change", (e) => { opts.scale = e.target.value; render3D(); });
  $("autorotate").addEventListener("change", (e) => viewer && viewer.setAutoRotate(e.target.checked));
  $("reset-view").addEventListener("click", () => viewer && viewer.resetView());

  // keyboard on the stage: space toggles play
  $("stage").addEventListener("keydown", (e) => { if (e.key === " ") { e.preventDefault(); setPlaying(!store.get().playing); } });

  const mq = window.matchMedia("(min-width: 1100px)");
  const fix = () => { if (mq.matches) $("picker").open = true; };
  mq.addEventListener("change", fix); fix();
}

function updatePlayButton(s) {
  const b = $("play");
  b.setAttribute("aria-label", s.playing ? "Pause" : "Play");
  $("play-ico").setAttribute("d", s.playing ? "M6 4.5h4.5v15H6zM13.5 4.5H18v15h-4.5z" : "M7 4.5v15l12-7.5z");
}

// ---------- guided tour hooks ----------
function tourControl() {
  const setCompare = (k) => {
    const s = store.get();
    if (!s.patient || (k && (!s.patient.scenarios[k] || k === s.scenario || !glOk))) return;
    store.set({ compare: k });
    if (k) ensureFrames(s.patient, loadToken);
  };
  return {
    state: () => store.get(),
    play: () => setPlaying(true),
    stop: () => store.set({ playing: false }),
    jumpTo: (t) => store.set({ playing: false, t }),
    setCompare,
    /** Start from the no-treatment scenario when the patient has it, so the comparison has a reference. */
    prepare: () => {
      const s = store.get();
      if (s.patient && s.patient.scenarios.no_further_treatment && s.scenario !== "no_further_treatment") {
        store.set({ scenario: "no_further_treatment", compare: "" });
        ensureFrames(s.patient, loadToken);
      }
    },
    /** The scenario with 3D frames whose final burden is furthest from the selected one. */
    bestCompare: () => {
      const s = store.get(), p = s.patient;
      if (!p || !glOk) return "";
      const end = (k) => { const b = p.scenarios[k].burden; return b[b.length - 1]; };
      const ref = end(s.scenario);
      return p.scenarioKeys
        .filter((k) => k !== s.scenario && p.scenarios[k].frames && isNum(end(k)) && isNum(ref))
        .sort((a, b) => Math.abs(end(b) - ref) - Math.abs(end(a) - ref))[0] || "";
    },
  };
}

// ---------- gizmo ----------
function drawGizmo(camera) {
  const q = camera.quaternion.clone().invert();
  const axes = [["X", new THREE.Vector3(1, 0, 0)], ["Y", new THREE.Vector3(0, 1, 0)], ["Z", new THREE.Vector3(0, 0, 1)]];
  let h = "";
  for (const [name, a] of axes) {
    const v = a.applyQuaternion(q);
    h += `<line x1="0" y1="0" x2="${(v.x * 20).toFixed(1)}" y2="${(-v.y * 20).toFixed(1)}" stroke="#8fa6c4" stroke-width="1.6" stroke-linecap="round"/><text x="${(v.x * 27).toFixed(1)}" y="${(-v.y * 27 + 3).toFixed(1)}" text-anchor="middle">${name}</text>`;
  }
  $("gizmo").innerHTML = h;
  updateScaleBar(camera);
}

// ---------- boot ----------
async function boot() {
  initTheme();
  const topBar = document.querySelector(".top");
  const setTopH = () => document.documentElement.style.setProperty("--top-h", `${topBar.offsetHeight}px`);
  new ResizeObserver(setTopH).observe(topBar); setTopH();
  stageState("Loading patient list", { loading: true });
  glOk = webglSupported();
  try {
    cohort = await loadCohort();
  } catch (e) {
    stageState(e.message, { error: true });
    $("data-chip").textContent = "No data";
    console.error(e, e.details);
    return;
  }
  const chip = $("data-chip");
  chip.textContent = `${cohort.kind === "fixture" ? "Sample data" : "Baked data"}: ${cohort.patients.length} patients`;
  chip.classList.toggle("is-fixture", cohort.kind === "fixture");
  renderFromData(cohort);
  const ev = (cohort.meta.effects && cohort.meta.effects.evidence) || {};
  if (Object.keys(ev).length) $("method-sources").innerHTML = Object.entries(ev).map(([a, t]) => `<li><strong>${esc(AGENT_LABEL[a] || a)}:</strong> ${esc(t)}</li>`).join("");
  const api = await probeApi();   // static hosting: 404 at once and the live-only controls stay hidden
  renderDevice($("device"), api);
  initUpload($("upload-slot"), api);
  renderExplanation($("ev-explain"), cohort.meta.explainer, { kind: "results", onUpdate: (n) => { cohort.meta.explainer = n; } });

  picker = new Picker({
    list: $("plist"), count: $("plist-count"), q: $("q"), status: $("f-status"),
    sort: $("f-sort"), frames: $("f-frames"), tx: $("f-tx"),
    idh: $("f-idh"), idhWrap: $("f-idh-wrap"), grade: $("f-grade"), gradeWrap: $("f-grade-wrap"),
  }, { onSelect: (p) => selectPatient(p) });
  picker.setPatients(cohort.patients);
  initDatasetSwitch($("ds-switch"), cohort.patients, (list) => picker.setPatients(list));

  chart = new Chart($("chart"), {
    onScrub: (t) => store.set({ playing: false, t: Math.min(store.get().patient.tEnd, Math.max(0, t)) }),
    onStep: (d) => { const s = store.get(); store.set({ playing: false, t: Math.min(s.patient.tEnd, Math.max(0, Math.round(s.t) + d)) }); },
  });

  if (glOk) {
    try {
      viewer = new Viewer($("gl"), $("stage"), { size: N, onFrame: drawGizmo });
      viewer.onContext = (lost) => stageState(lost ? "The graphics context was lost. Reload the page to restore the 3D view." : "", { error: lost, hide: !lost });
      if (reduceMotion) { $("autorotate").checked = false; }
    } catch (e) {
      console.error(e);
      glOk = false;
    }
  }
  if (!glOk) {
    // No WebGL 2: keep the 2D cross-section, chart and numbers working.
    $("slice-wrap").hidden = false;
    $("slice-wrap").classList.add("is-main");
    $("clip-on").checked = true;
    opts.clip.on = true;
    ["clip-on", "clip-flip", "opacity", "autorotate", "reset-view", "compare"].forEach((id) => { $(id).disabled = true; });
    $("stage").setAttribute("aria-label", "3D view unavailable. A 2D cross-section is shown instead.");
    $("stage").classList.add("no-gl");
    $("gl-note").hidden = false;
    $("legend-hide-gl")?.remove();
  }

  bindControls();
  store.subscribe((s, changed) => { updatePlayButton(s); renderAll(s, changed); });

  // initial patient: from the URL, else the first healthy patient with frames
  const q = new URLSearchParams(location.search);
  const byId = (id) => cohort.patients.find((p) => p.id === id || p.uid === id);
  // default: the converged patient with 3D frames and the largest burden at the last scan
  // (a denser tumour shows more in 3D); not chosen for how its forecast looks.
  const byBurden = (a, b) => (isNum(b.lastBurden) ? b.lastBurden : -1) - (isNum(a.lastBurden) ? a.lastBurden : -1);
  const start = byId(q.get("p"))
    || [...cohort.patients].filter((p) => p.hasFrames && p.status === "ok").sort(byBurden)[0]
    || cohort.patients.find((p) => p.hasFrames) || cohort.patients[0];
  if (start) selectPatient(start, { scenario: q.get("s") || undefined, t: q.has("t") ? Number(q.get("t")) : undefined });
  else stageState(`No patients in the data file. ${NA}`, { error: true });

  const tour = initTour(tourControl());
  if (q.get("tour") === "1" && start) tour.start();
}

boot();
