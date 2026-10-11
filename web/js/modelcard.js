// Evidence cards and model card.
// Everything is read from whatif_cases.json: `model_card` (treatment-effect fit, risk model, forecast),
// `effects` (priors) and `forecast_metrics`. A missing block renders as "not available", never as a number.
// The only strings kept here are two measured facts that come from docs/runbooks/treatment-effects.md and
// are not (yet) in the data file; they are marked as such below.
import { esc, isNum, NA, AGENT_LABEL, DATASET_LABEL } from "./format.js";

/** Measured facts quoted from docs/runbooks/treatment-effects.md (Finding 2). Replace when the JSON carries them. */
const DS_SHORT = { ucsf_lptdg: "UCSF", mu_glioma_post: "MU", ucsd_ptgbm: "UCSD" };
const DS_ORDER = ["ucsf_lptdg", "mu_glioma_post", "ucsd_ptgbm"];
const dsShort = (k) => DS_SHORT[k] || DATASET_LABEL[k] || k;
const dsKeys = (...objs) => {
  const all = [...new Set(objs.flatMap((o) => Object.keys(o || {})))];
  return [...DS_ORDER.filter((k) => all.includes(k)), ...all.filter((k) => !DS_ORDER.includes(k))];
};

const FORECAST_LABEL = {
  with_treatment: "Growth model with treatment schedule",
  treatment_blind: "Growth model, treatment ignored",
  cohort_median_rate: "Cohort median growth rate",
  no_change: "No change since last scan",
};

const f3 = (v) => (isNum(v) ? (Math.abs(v) < 5e-4 ? "0" : v.toFixed(3)) : NA);
const f2 = (v) => (isNum(v) ? v.toFixed(2) : NA);
const listJoin = (a) => (a.length <= 1 ? a.join("") : `${a.slice(0, -1).join(", ")} and ${a[a.length - 1]}`);
const $ = (id) => document.getElementById(id);
const set = (id, html) => { const el = $(id); if (el) el.innerHTML = html; };
const setText = (id, t) => { const el = $(id); if (el) el.textContent = t; };

// ---------- normalise model_card ----------

function readFit(mc) {
  const tf = mc && mc.treatment_fit;
  if (!tf || typeof tf !== "object") return null;
  const AGENT_ORDER = ["temozolomide", "ttfields", "bevacizumab"];
  const entries = Object.entries(tf.kill_per_day || {}).sort((a, b) => {
    const ia = AGENT_ORDER.indexOf(a[0]), ib = AGENT_ORDER.indexOf(b[0]);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
  const agents = entries.map(([key, v]) => ({
    key, label: AGENT_LABEL[key] || key, unit: "per day",
    value: v && isNum(v.value) ? v.value : null,
    ci: v && Array.isArray(v.ci95) && v.ci95.length === 2 && v.ci95.every(isNum) ? v.ci95 : null,
    exposed: v && isNum(v.exposed_intervals) ? v.exposed_intervals : null,
  }));
  const rad = tf.radiation_k_per_gy;
  const radiation = rad && typeof rad === "object" ? {
    key: "radiation", label: "Radiation", unit: "per Gy",
    value: isNum(rad.value) ? rad.value : null,
    ci: Array.isArray(rad.ci95) && rad.ci95.length === 2 && rad.ci95.every(isNum) ? rad.ci95 : null,
    exposed: null,
  } : null;
  return {
    nIntervals: isNum(tf.n_intervals) ? tf.n_intervals : null,
    nPatients: isNum(tf.n_patients) ? tf.n_patients : null,
    // the file stores the cross-validated change versus the no-treatment baseline; negative means worse
    cvPct: isNum(tf.cv_vs_no_treatment_baseline_pct) ? tf.cv_vs_no_treatment_baseline_pct : null,
    ciLevel: isNum(tf.ci_level) ? tf.ci_level : null,
    caveat: typeof tf.caveat === "string" ? tf.caveat : null,
    agents, radiation,
  };
}

function readRisk(mc) {
  const r = mc && mc.risk;
  if (!r || typeof r !== "object") return null;
  const pick = (o) => (o && isNum(o.auc) ? o.auc : null);
  const keys = dsKeys(r.within_dataset, r.leave_one_dataset_out, r.idh_wild_type_alone_auc);
  return {
    n: isNum(r.n) ? r.n : null,
    pooled: pick(r.pooled),
    keys,
    within: Object.fromEntries(keys.map((k) => [k, pick(r.within_dataset && r.within_dataset[k])])),
    lodo: Object.fromEntries(keys.map((k) => [k, pick(r.leave_one_dataset_out && r.leave_one_dataset_out[k])])),
    idh: Object.fromEntries(keys.map((k) => [k, r.idh_wild_type_alone_auc && isNum(r.idh_wild_type_alone_auc[k]) ? r.idh_wild_type_alone_auc[k] : null])),
    deceased: r.deceased_rate_by_dataset || null,
    nBy: r.n_by_dataset || null,
  };
}

const CI_TEXT = (a, level) => {
  if (!a.ci) return NA;
  const lvl = isNum(level) ? `${Math.round(level * 100)}% ` : "";
  return `${lvl}interval ${f3(a.ci[0])} to ${f3(a.ci[1])}`;
};
const includesZero = (a) => a.ci && a.ci[0] <= 0 && a.ci[1] >= 0;

// ---------- plots ----------

/** Dot plot of fitted kill rates (per day) with intervals. */
function fitPlot(W, fit) {
  const rows = fit.agents.filter((a) => isNum(a.value));
  if (!rows.length) return "";
  const rowH = 34, L = Math.min(118, W * 0.3), R = 16, T = 8, B = 36;
  const H = T + rowH * rows.length + B;
  const hi = Math.max(...rows.map((a) => (a.ci ? a.ci[1] : a.value)), 0.001);
  const max = hi * 1.5;
  const X = (v) => L + (v / max) * (W - L - R);
  const ticks = [0, max / 4, max / 2, (3 * max) / 4, max].map((v) => Math.round(v * 1000) / 1000);
  let s = "";
  [...new Set(ticks)].forEach((v) => {
    s += `<line class="v-axis" x1="${X(v)}" x2="${X(v)}" y1="${T}" y2="${H - B}" stroke-opacity="${v === 0 ? 1 : 0.35}"/><text x="${X(v)}" y="${H - B + 16}" text-anchor="middle">${v}</text>`;
  });
  rows.forEach((a, i) => {
    const y = T + rowH * i + rowH / 2;
    s += `<text x="${L - 10}" y="${y + 4}" text-anchor="end">${esc(a.label)}</text>`;
    if (a.ci) s += `<line x1="${X(a.ci[0])}" x2="${X(a.ci[1])}" y1="${y}" y2="${y}" stroke="var(--measured)" stroke-width="3" stroke-linecap="round"/>`;
    if (a.value === 0) s += `<rect x="${X(0) - 5}" y="${y - 5}" width="10" height="10" fill="var(--surface)" stroke="var(--measured)" stroke-width="2"/>`;
    else s += `<circle cx="${X(a.value)}" cy="${y}" r="5" fill="var(--measured)"/>`;
    const lx = X(a.ci ? a.ci[1] : a.value) + 8;
    s += `<text x="${lx}" y="${y - 7}" class="v-strong">${f3(a.value)}</text>`;
    if (a.ci) s += `<text x="${lx}" y="${y + 9}">${f3(a.ci[0])} to ${f3(a.ci[1])}</text>`;
  });
  s += `<text x="${L}" y="${H - 4}">fitted kill rate per day, from cohort data (not literature)</text>`;
  const desc = rows.map((a) => `${a.label} ${f3(a.value)}${a.ci ? `, interval ${f3(a.ci[0])} to ${f3(a.ci[1])}` : ""}`).join("; ");
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Fitted kill rates per day. ${esc(desc)}.">${s}</svg>`;
}

/** Horizontal AUC bars with the chance line at 0.5. */
function riskPlot(compact, W, risk) {
  const rows = [];
  if (isNum(risk.pooled)) rows.push({ k: compact ? "Pooled" : `Pooled${isNum(risk.n) ? `, ${risk.n} patients` : ""}`, auc: risk.pooled, kind: "pooled" });
  if (!compact) risk.keys.forEach((k) => { if (isNum(risk.within[k])) rows.push({ k: `Within ${dsShort(k)}`, auc: risk.within[k], kind: "within" }); });
  risk.keys.forEach((k) => { if (isNum(risk.lodo[k])) rows.push({ k: `Held out ${dsShort(k)}`, auc: risk.lodo[k], kind: "lodo" }); });
  if (!compact) risk.keys.forEach((k) => { if (isNum(risk.idh[k])) rows.push({ k: `IDH wild-type alone, ${dsShort(k)}`, auc: risk.idh[k], kind: "idh" }); });
  if (!rows.length) return "";
  const rowH = compact ? 26 : 28, L = Math.min(compact ? 128 : 190, W * 0.42), R = 40, T = 6, B = 28;
  const H = T + rowH * rows.length + B;
  const lo = 0.4, hi = 0.85;
  const X = (v) => L + ((v - lo) / (hi - lo)) * (W - L - R);
  const id = compact ? "hatch-risk-c" : "hatch-risk";
  let s = `<line class="v-chance" x1="${X(0.5)}" x2="${X(0.5)}" y1="${T}" y2="${H - B}"/><text x="${X(0.5)}" y="${H - B + 16}" text-anchor="middle" style="fill:var(--bad)">chance 0.5</text>`;
  (compact ? [0.7, 0.8] : [0.6, 0.7, 0.8]).forEach((v) => { s += `<text x="${X(v)}" y="${H - B + 16}" text-anchor="middle">${v}</text>`; });
  rows.forEach((d, i) => {
    const y = T + rowH * i;
    const fill = d.kind === "lodo" ? `url(#${id})` : d.kind === "idh" ? "var(--text-3)" : "var(--measured)";
    const stroke = d.kind === "lodo" ? "var(--measured)" : "none";
    s += `<text x="${L - 10}" y="${y + rowH / 2 + 4}" text-anchor="end">${esc(d.k)}</text>`;
    s += `<rect x="${X(lo)}" y="${y + 5}" width="${Math.max(2, X(d.auc) - X(lo))}" height="${rowH - 10}" fill="${fill}" stroke="${stroke}" stroke-width="1.5"/>`;
    s += `<text x="${Math.max(X(d.auc), X(0.5)) + 6}" y="${y + rowH / 2 + 4}" class="v-strong">${d.auc.toFixed(2)}</text>`;
  });
  const desc = rows.map((r) => `${r.k} ${r.auc.toFixed(2)}`).join("; ");
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Risk model AUC. ${esc(desc)}. Chance is 0.5.">`
    + `<defs><pattern id="${id}" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="var(--surface)"/><line x1="0" y1="0" x2="0" y2="6" stroke="var(--measured)" stroke-width="2.5"/></pattern></defs>${s}</svg>`
    + (compact ? "" : `<p class="note">Solid bars are pooled or within-dataset; hatched bars hold out a whole dataset; grey bars use IDH wild-type status alone. The axis starts at 0.4.</p>`);
}

function forecastRows(fm) {
  const order = ["with_treatment", "treatment_blind", "cohort_median_rate", "no_change"];
  return order.filter((k) => fm && fm[k]).map((k) => {
    const m = fm[k];
    const mae = isNum(m.mae_log_ratio) ? m.mae_log_ratio : null;
    // direction accuracy is not defined for a predictor that always says "no change"
    const dir = k === "no_change" ? null : isNum(m.direction_accuracy) ? m.direction_accuracy : null;
    return { k, label: FORECAST_LABEL[k], mae, factor: mae === null ? null : Math.exp(mae), dir };
  });
}

function forecastBars(rows, W) {
  const rowH = 30, L = Math.min(210, W * 0.46), R = 48, T = 4, B = 34;
  const H = T + rowH * rows.length + B;
  const max = Math.max(...rows.map((r) => r.mae || 0), 0.5) * 1.1;
  const X = (v) => L + (v / max) * (W - L - R);
  let s = `<line class="v-axis" x1="${X(0)}" x2="${X(0)}" y1="${T}" y2="${H - B}"/>`;
  rows.forEach((r, i) => {
    const y = T + rowH * i;
    const model = r.k === "with_treatment" || r.k === "treatment_blind";
    s += `<text x="${L - 10}" y="${y + rowH / 2 + 4}" text-anchor="end">${esc(r.label)}</text>`;
    if (r.mae === null) { s += `<text x="${L}" y="${y + rowH / 2 + 4}">${NA}</text>`; return; }
    s += `<rect x="${X(0)}" y="${y + 5}" width="${Math.max(2, X(r.mae) - X(0))}" height="${rowH - 10}" fill="${model ? "var(--warn)" : "var(--measured)"}" ${model ? 'fill-opacity=".85"' : ""}/>`;
    s += `<text x="${X(r.mae) + 6}" y="${y + rowH / 2 + 4}" class="v-strong">${r.mae.toFixed(2)}</text>`;
  });
  s += `<text x="${L}" y="${H - 6}">typical error of the log burden ratio, lower is better</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Forecast error by method: ${rows.map((r) => `${r.label} ${r.mae === null ? NA : r.mae.toFixed(2)}`).join("; ")}">${s}</svg>`;
}

/** Render fn(width) into the element and again whenever its width changes. */
const mounted = new Map();
function mount(id, fn) {
  const el = $(id);
  if (!el) return;
  if (mounted.has(id)) { mounted.get(id).fn = fn; mounted.get(id).draw(true); return; }
  let lastW = 0;
  const m = { fn };
  m.draw = (force) => {
    const w = Math.max(240, Math.floor(el.clientWidth));
    if (w === lastW && !force) return;
    lastW = w;
    el.innerHTML = m.fn(w);
  };
  mounted.set(id, m);
  new ResizeObserver(() => m.draw(false)).observe(el);
  m.draw(true);
}

// ---------- page ----------

export function renderFromData(cohort) {
  const m = cohort.meta;
  const mc = m.modelCard;
  const fit = readFit(mc);
  const risk = readRisk(mc);

  // dataset line
  const ds = m.dataset ? DATASET_LABEL[m.dataset] || m.dataset : null;
  set("mc-dataset",
    `Cases in this build: <strong>${cohort.patients.length}</strong>${ds ? ` from ${esc(ds)}` : ""}${cohort.kind === "fixture" ? ' <span class="tag tag-fixture">sample data</span>' : ""}. `
    + `Diffusion D is fixed at ${isNum(m.D) ? m.D + " mm²/day" : NA}; forecast horizon ${isNum(m.horizon) ? Math.round(m.horizon) + " days" : NA}.`);

  // ----- treatment fit -----
  if (fit) {
    const zero = [...fit.agents, ...(fit.radiation ? [fit.radiation] : [])].filter((a) => a.value === 0 && includesZero(a));
    const nonzero = fit.agents.filter((a) => isNum(a.value) && a.value > 0);
    const where = `${fit.nIntervals ?? NA} intervals from ${fit.nPatients ?? NA} patients (${ds || NA})`;
    const lvl = isNum(fit.ciLevel) ? `${Math.round(fit.ciLevel * 100)}% ` : "";
    const worse = isNum(fit.cvPct) ? Math.abs(fit.cvPct) : null;
    const dirWord = !isNum(fit.cvPct) ? null : fit.cvPct < 0 ? "worse" : "better";
    setText("b-cv", worse === null ? NA : `${worse.toFixed(1)}%`);
    setText("b-cv-unit", dirWord ? `${dirWord} than a no-treatment baseline` : "versus a no-treatment baseline");
    setText("b-fit-text", `Leave-patients-out cross-validation, ${where}. Fitted kill rates for ${listJoin(zero.map((a) => a.label.toLowerCase() === "ttfields" ? "TTFields" : a.label.toLowerCase()))} are 0, with ${lvl}intervals that include 0.`
      + (nonzero.length ? ` ${nonzero.map((a) => `${a.label} is ${f3(a.value)} per day (${f3(a.ci?.[0])} to ${f3(a.ci?.[1])})`).join("; ")}.` : ""));
    setText("mc-fit-text", `We fitted a per-day kill rate for each agent to growth between consecutive scans: ${where}. This is a finding, not a failure of the fit: in retrospective charts, treatment is given to tumours that are already progressing (confounding by indication), so treated intervals do not grow more slowly than untreated ones.`);
    const rows = [...fit.agents, ...(fit.radiation ? [fit.radiation] : [])].map((a) => {
      const note = a.value === 0 && includesZero(a) ? " (interval includes 0)" : "";
      return `<tr><th scope="row">${esc(a.label)}</th><td class="num">${f3(a.value)} ${a.unit}</td><td>${esc(CI_TEXT(a, fit.ciLevel))}${note}</td><td class="num">${isNum(a.exposed) ? a.exposed : NA}</td></tr>`;
    });
    $("fit-table").querySelector("tbody").innerHTML = rows.join("");
    setText("mc-fit-cv", worse === null ? `Held-out prediction against a no-treatment baseline: ${NA}.`
      : `Predicting held-out patients' volume change with the fitted treatment terms was ${worse.toFixed(1)}% ${dirWord} than a baseline that ignores treatment. The scenarios therefore use literature priors instead, and say so.`);
    setText("mc-fit-caveat", fit.caveat || "");
    mount("bento-fit", (w) => fitPlot(w, fit));
  } else {
    setText("b-cv", NA);
    setText("b-fit-text", "The treatment-effect fit is not available in this data file.");
    set("bento-fit", "");
    setText("mc-fit-text", "The treatment-effect fit is not available in this data file.");
    $("fit-table").querySelector("tbody").innerHTML = `<tr><td colspan="4">${NA}</td></tr>`;
    setText("mc-fit-cv", ""); setText("mc-fit-caveat", "");
  }

  // ----- priors -----
  const tb = $("priors-table").querySelector("tbody");
  if (m.effects && m.effects.values && m.effects.evidence) {
    const rows = Object.keys(m.effects.evidence).map((a) => {
      const v = m.effects.values[a];
      const sup = v && isNum(v.suppress) ? (v.suppress > 0 ? `${(v.suppress * 100).toFixed(0)}%` : "not modelled") : NA;
      return `<tr><th scope="row">${esc(AGENT_LABEL[a] || a)}</th><td class="num">${sup}</td><td>${esc(m.effects.evidence[a])}</td></tr>`;
    });
    if (m.effects.radiation) rows.push(`<tr><th scope="row">Radiation</th><td class="num">${isNum(m.effects.values.radiation?.k_per_gy) ? m.effects.values.radiation.k_per_gy + " per Gy" : NA}</td><td>${esc(m.effects.radiation)}</td></tr>`);
    tb.innerHTML = rows.join("");
    const sups = Object.values(m.effects.values).map((v) => v && v.suppress).filter((x) => isNum(x) && x > 0);
    setText("b-prior-range", sups.length ? `${Math.round(Math.min(...sups) * 100)} to ${Math.round(Math.max(...sups) * 100)}%` : NA);
  } else {
    tb.innerHTML = `<tr><td colspan="3">${NA}</td></tr>`;
    setText("b-prior-range", NA);
  }

  // ----- risk -----
  if (risk && (isNum(risk.pooled) || risk.keys.some((k) => isNum(risk.lodo[k])))) {
    setText("b-risk-auc", isNum(risk.pooled) ? f2(risk.pooled) : NA);
    setText("b-risk-unit", `pooled AUC${isNum(risk.n) ? `, ${risk.n} patients` : ""}`);
    const held = risk.keys.filter((k) => isNum(risk.lodo[k]));
    const chance = held.filter((k) => risk.lodo[k] <= 0.5);
    setText("b-risk-text", held.length
      ? `Held out by dataset it falls to ${listJoin(held.map((k) => f2(risk.lodo[k])))}.${chance.length ? ` ${listJoin(chance.map(dsShort))} ${chance.length > 1 ? "are" : "is"} at or below chance.` : ""}`
      : "Held-out results are not available.");
    mount("bento-risk", (w) => riskPlot(true, w, risk));
    mount("risk-chart", (w) => riskPlot(false, w, risk));
    const within = risk.keys.filter((k) => isNum(risk.within[k]));
    const idhUcsf = risk.idh.ucsf_lptdg;
    const dec = risk.deceased && Object.keys(risk.deceased).length
      ? ` Deceased rates differ by cohort (${dsKeys(risk.deceased).map((k) => `${dsShort(k)} ${Math.round(risk.deceased[k] * 100)}%`).join(", ")}), so cross-dataset AUCs are not like for like.` : "";
    setText("mc-risk-text",
      `Pooled AUC is ${f2(risk.pooled)}${isNum(risk.n) ? ` over ${risk.n} patients` : ""}.`
      + (within.length ? ` Within each dataset it is ${listJoin(within.map((k) => `${f2(risk.within[k])} (${dsShort(k)})`))}.` : "")
      + (held.length ? ` When a whole dataset is held out it is ${listJoin(held.map((k) => f2(risk.lodo[k])))}, so external validation is weak${chance.length ? `, and at or below chance for ${listJoin(chance.map(dsShort))}` : ""}.` : "")
      + (isNum(idhUcsf) ? ` For comparison, IDH wild-type status alone gives AUC ${f2(idhUcsf)} on UCSF.` : "")
      + dec);
  } else {
    setText("b-risk-auc", NA);
    setText("b-risk-text", "The risk model results are not available in this data file.");
    set("bento-risk", ""); set("risk-chart", "");
    setText("mc-risk-text", "The risk model results are not available in this data file.");
  }

  // ----- forecast check -----
  const fm = m.forecastMetrics;
  const rows = forecastRows(fm);
  const n = fm && fm.n_forecasts, np = fm && fm.n_patients;
  const where = isNum(n) && isNum(np) ? `${n} forecasts from ${np} patients` : "an unknown number of forecasts";
  const ftb = $("forecast-table").querySelector("tbody");
  if (rows.length) {
    const tw = rows.find((r) => r.k === "with_treatment"), cm = rows.find((r) => r.k === "cohort_median_rate");
    const worse = tw && tw.mae !== null && cm && cm.mae !== null && tw.mae > cm.mae;
    const ratio = worse ? (tw.mae / cm.mae).toFixed(1) : null;
    const cvNote = fm.note ? ` ${fm.note.charAt(0).toUpperCase()}${fm.note.slice(1)}.` : "";
    setText("mc-forecast-text",
      `Each forecast predicts the next observed scan from the previous one and is scored on how far the predicted growth ratio is from the observed ratio (natural log). Based on ${where}.${cvNote} `
      + (worse ? "The growth model, with or without the treatment schedule, did worse than predicting the cohort median rate or no change. Treat its forecasts as illustration, not prediction." : ""));
    setText("bento-forecast-note", worse
      ? `On ${where}, the model's error (${tw.mae.toFixed(2)}) was about ${ratio} times that of simply using the cohort median growth rate (${cm.mae.toFixed(2)}). The scenarios show what the model does, not what will happen.`
      : `Based on ${where}.`);
    ftb.innerHTML = rows.map((r) =>
      `<tr><th scope="row">${esc(r.label)}</th><td class="num">${r.mae === null ? NA : r.mae.toFixed(2)}</td><td class="num">${r.factor === null ? NA : `${r.factor.toFixed(1)}x`}</td><td class="num">${r.dir === null ? (r.k === "no_change" ? "not defined" : NA) : `${(r.dir * 100).toFixed(0)}%`}</td></tr>`).join("");
    mount("bento-forecast", (w) => forecastBars(rows, w));
  } else {
    setText("mc-forecast-text", `Forecast metrics are ${NA} in this data file.`);
    setText("bento-forecast-note", "");
    set("bento-forecast", "");
    ftb.innerHTML = "";
  }

  // measured facts from the data file (model_card.growth_rate_autocorrelation, model_card.calibration)
  const raw = m.modelCard || {};
  const gac = raw.growth_rate_autocorrelation;
  if (gac && isNum(gac.r)) {
    set("mc-autocorr",
      `<span class="tag tag-measured">measured</span> The correlation between a patient's growth rate in one interval and the next is <strong>${gac.r.toFixed(2)}</strong> (${gac.n_pairs} consecutive interval pairs), so a method that extrapolates a patient's own history has nothing to work with here. `
      + `<span class="note">Source: docs/runbooks/treatment-effects.md, Finding 2.</span>`);
  } else {
    set("mc-autocorr", `Growth-rate persistence between intervals is ${NA} in this data file.`);
  }
  const cal = raw.calibration;
  const lim = $("mc-limits");
  if (cal && isNum(cal.n_shrinking) && lim && !lim.querySelector("[data-runbook]")) {
    const li = document.createElement("li");
    li.textContent = `In the full run, ${cal.n_shrinking} of ${cal.n_patients} first-interval calibrations needed the shrinking branch, where growth rate is held fixed and only a loss rate is fitted.`;
    li.dataset.runbook = "1";
    lim.appendChild(li);
  }
}
