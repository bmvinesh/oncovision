// Data loading and normalisation. Tolerant of missing files and fields.
// Source order: data/whatif_cases.json (baked), then data-fixture/whatif_cases.json (sample).
// Frame URLs in the JSON are relative to the directory the JSON was read from.
import { isNum, shortId, datasetOf } from "./format.js";

const SOURCES = [
  { root: "data/", kind: "baked" },
  { root: "data-fixture/", kind: "fixture" },
];

/** ?data=fixture forces the sample data; ?data=baked forbids falling back. */
function sourceOrder() {
  let pref = "";
  try { pref = new URLSearchParams(location.search).get("data") || ""; } catch (e) { /* ignore */ }
  if (pref === "fixture") return [SOURCES[1]];
  if (pref === "baked") return [SOURCES[0]];
  return SOURCES;
}

async function fetchJson(url) {
  const r = await fetch(url, { cache: "no-cache" });
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

export async function loadCohort() {
  const errors = [];
  for (const src of sourceOrder()) {
    try {
      const raw = await fetchJson(src.root + "whatif_cases.json");
      if (!raw || !Array.isArray(raw.cases)) throw new Error("no cases array");
      const patients = raw.cases.map(normalisePatient).filter(Boolean);
      return {
        kind: src.kind,
        root: src.root,
        meta: {
          dataset: raw.dataset || null,
          D: isNum(raw.D_mm2_per_day) ? raw.D_mm2_per_day : null,
          horizon: isNum(raw.horizon_days) ? raw.horizon_days : null,
          effects: raw.effects || null,
          forecastMetrics: raw.forecast_metrics || null,
          illustrative: raw.illustrative !== false,
        },
        patients,
      };
    } catch (e) {
      errors.push(e.message);
    }
  }
  const err = new Error("No what-if data found. Run oncovision.sim.whatif and place whatif_cases.json (and whatif/) in web/data/.");
  err.details = errors;
  throw err;
}

function normalisePatient(c) {
  if (!c || !c.uid || !Array.isArray(c.observed) || !c.observed.length || !c.scenarios) return null;
  const observed = c.observed.map((o, i) => ({
    index: isNum(o.tp_index) ? o.tp_index : i,
    day: o.day,
    tumor: isNum(o.tumor_mm3) ? o.tumor_mm3 : null,
    core: isNum(o.core_mm3) ? o.core_mm3 : null,
    cavity: isNum(o.rc_mm3) ? o.rc_mm3 : null,
    seed: isNum(o.seed_burden_mm3) ? o.seed_burden_mm3 : null,
  })).sort((a, b) => a.day - b.day);
  const scenarios = {};
  for (const [k, s] of Object.entries(c.scenarios)) {
    if (!s || !Array.isArray(s.day) || !Array.isArray(s.burden_mm3)) continue;
    scenarios[k] = {
      key: k,
      day: s.day,
      burden: s.burden_mm3,
      volume: Array.isArray(s.volume_mm3) ? s.volume_mm3 : null,
      frames: validFrames(s.frames),
    };
  }
  const keys = Object.keys(scenarios);
  if (!keys.length) return null;
  const lastScan = observed[observed.length - 1].day;
  const tEnd = Math.max(...keys.map((k) => scenarios[k].day[scenarios[k].day.length - 1]));
  const cells = c.grid && isNum(c.grid.cells) ? c.grid.cells : null;
  const spacing = c.grid && isNum(c.grid.spacing_mm) ? c.grid.spacing_mm : null;
  const sch = c.recorded_schedule || {};
  const p = {
    uid: c.uid,
    id: shortId(c.uid),
    dataset: datasetOf(c.uid),
    status: c.status || null,
    rho: isNum(c.rho) ? c.rho : null,
    delta: isNum(c.delta) ? c.delta : null,
    D: isNum(c.D) ? c.D : null,
    boxMm: cells && spacing ? cells * spacing : null,
    gridCells: cells,
    spacingMm: spacing,
    observed,
    observedFrames: validFrames(c.observed_frames),
    scenarios,
    scenarioKeys: keys,
    calib: c.calibrated_on_first_interval || null,
    drugs: Array.isArray(sch.drugs) ? sch.drugs : [],
    radiation: Array.isArray(sch.radiation) ? sch.radiation : [],
    assumptions: Array.isArray(c.assumptions) ? c.assumptions : [],
    lastScan,
    firstScan: observed[0].day,
    tEnd,
    // optional clinical / risk fields, shown only when the baker provides them
    idh: c.idh ?? (c.clinical && c.clinical.idh) ?? null,
    whoGrade: c.who_grade ?? (c.clinical && c.clinical.who_grade) ?? null,
  };
  p.hasFrames = keys.some((k) => scenarios[k].frames) || !!p.observedFrames;
  p.lastBurden = observed[observed.length - 1].seed;
  const none = scenarios.no_further_treatment || scenarios[keys[0]];
  const endB = none.burden[none.burden.length - 1];
  p.forecastChangePct = isNum(p.lastBurden) && p.lastBurden > 0 && isNum(endB) ? (endB / p.lastBurden - 1) * 100 : null;
  p.hasTreatment = p.drugs.length > 0 || p.radiation.length > 0;
  return p;
}

function validFrames(f) {
  if (!f || typeof f.url !== "string" || !Array.isArray(f.shape) || f.shape.length !== 4) return null;
  if (f.dtype && f.dtype !== "uint8") return null;
  if (f.order && f.order !== "TZYX") return null;
  const n = f.shape.reduce((a, b) => a * b, 1);
  return {
    url: f.url,
    shape: f.shape,
    days: Array.isArray(f.days) ? f.days : null,
    maxValue: isNum(f.max_value) ? f.max_value : 255,
    bytes: n,
  };
}

// ---------- values at a time ----------

/** Piecewise-linear lookup, clamped to the ends. Returns null when arrays are unusable. */
export function interp(xs, ys, x) {
  const n = xs.length;
  if (!n || !ys || ys.length !== n) return null;
  if (x <= xs[0]) return ys[0];
  if (x >= xs[n - 1]) return ys[n - 1];
  let lo = 0, hi = n - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (xs[m] <= x) lo = m; else hi = m; }
  const f = (x - xs[lo]) / (xs[hi] - xs[lo] || 1);
  const a = ys[lo], b = ys[hi];
  return isNum(a) && isNum(b) ? a + (b - a) * f : null;
}

export function scanIndexAt(p, t) {
  let idx = 0;
  for (let i = 0; i < p.observed.length; i++) if (p.observed[i].day <= t + 1e-6) idx = i;
  return idx;
}

/** Everything the readout needs at time t for one scenario. */
export function valuesAt(p, scenKey, t) {
  const s = p.scenarios[scenKey];
  const none = p.scenarios.no_further_treatment;
  const inForecast = t > p.lastScan + 1e-6;
  const si = scanIndexAt(p, t);
  const scan = p.observed[si];
  let burden, volume = null;
  if (!inForecast) {
    burden = scan.seed;
  } else {
    burden = s ? interp(s.day, s.burden, t) : null;
    volume = s && s.volume ? interp(s.day, s.volume, t) : null;
  }
  const noneB = inForecast && none ? interp(none.day, none.burden, t) : null;
  const last = p.lastBurden;
  return {
    phase: inForecast ? "forecast" : "observed",
    scanIndex: si,
    scanCount: p.observed.length,
    scanDay: scan.day,
    scan,
    daysSinceLast: inForecast ? t - p.lastScan : null,
    burden,
    volume,
    vsLastPct: isNum(burden) && isNum(last) && last > 0 ? (burden / last - 1) * 100 : null,
    vsNonePct: inForecast && isNum(burden) && isNum(noneB) && noneB > 0 ? (burden / noneB - 1) * 100 : null,
  };
}

// ---------- frames ----------

const buffers = new Map(); // url -> Promise<Uint8Array>
const order = [];

/** Fetch a raw uint8 frame file. onProgress(fraction|null). Cached; at most ~24 files kept. */
export function fetchFrames(root, spec, onProgress) {
  const url = new URL(spec.url, new URL(root, document.baseURI)).href;
  if (buffers.has(url)) return buffers.get(url);
  const pr = (async () => {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`frames ${spec.url}: HTTP ${r.status}`);
    const total = Number(r.headers.get("content-length")) || spec.bytes;
    let data;
    if (r.body && r.body.getReader && onProgress) {
      const reader = r.body.getReader();
      const out = new Uint8Array(spec.bytes);
      let got = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        if (got + value.length > out.length) throw new Error(`frames ${spec.url}: larger than expected (${spec.bytes} bytes)`);
        out.set(value, got);
        got += value.length;
        onProgress(Math.min(1, got / total));
      }
      if (got !== spec.bytes) throw new Error(`frames ${spec.url}: ${got} bytes, expected ${spec.bytes}`);
      data = out;
    } else {
      data = new Uint8Array(await r.arrayBuffer());
      if (data.length !== spec.bytes) throw new Error(`frames ${spec.url}: ${data.length} bytes, expected ${spec.bytes}`);
    }
    return data;
  })();
  buffers.set(url, pr);
  order.push(url);
  pr.catch(() => buffers.delete(url));
  while (order.length > 24) buffers.delete(order.shift());
  return pr;
}

export function maxOf(u8) {
  let m = 0;
  for (let i = 0; i < u8.length; i++) if (u8[i] > m) m = u8[i];
  return m;
}
