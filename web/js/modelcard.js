// Evidence cards and model card. Cohort-level results that the backend reported are kept in
// MODEL_RESULTS below (hard-coded until a baked model_card.json exists; see README).
// Priors and forecast metrics are read live from whatif_cases.json.
import { esc, isNum, NA, AGENT_LABEL, DATASET_LABEL } from "./format.js";

export const MODEL_RESULTS = {
  treatmentFit: {
    dataset: "MU-Glioma-Post",
    intervals: 333,
    patients: 144,
    cvWorsePct: 6.6,
    // kill rate per day; ci null means "includes 0, bounds not reported"
    agents: [
      { key: "temozolomide", label: "Temozolomide", value: 0, ci: null },
      { key: "ttfields", label: "TTFields", value: 0, ci: null },
      { key: "radiation", label: "Radiation", value: 0, ci: null },
      { key: "bevacizumab", label: "Bevacizumab", value: 0.003, ci: [0, 0.009] },
    ],
  },
  risk: {
    pooled: { auc: 0.74, n: 676 },
    within: [{ k: "UCSF", auc: 0.78 }, { k: "MU", auc: 0.66 }, { k: "UCSD", auc: 0.69 }],
    lodo: [{ k: "UCSF", auc: 0.72 }, { k: "MU", auc: 0.56 }, { k: "UCSD", auc: 0.46 }],
    idhWtUcsf: 0.75,
  },
};

const FORECAST_LABEL = {
  with_treatment: "Growth model with treatment schedule",
  treatment_blind: "Growth model, treatment ignored",
  cohort_median_rate: "Cohort median growth rate",
  no_change: "No change since last scan",
};

/** Dot plot of fitted kill rates with intervals. Axis 0 to 0.01 per day. */
function fitPlot(W) {
  const f = MODEL_RESULTS.treatmentFit;
  const rowH = 30, L = Math.min(118, W * 0.3), R = 16, T = 8, B = 30;
  const H = T + rowH * f.agents.length + B;
  const max = 0.0135;
  const X = (v) => L + (v / max) * (W - L - R);
  let s = "";
  [0, 0.0025, 0.005, 0.0075, 0.01].forEach((v) => {
    s += `<line class="v-axis" x1="${X(v)}" x2="${X(v)}" y1="${T}" y2="${H - B}" stroke-opacity="${v === 0 ? 1 : 0.35}"/><text x="${X(v)}" y="${H - B + 16}" text-anchor="middle">${v}</text>`;
  });
  f.agents.forEach((a, i) => {
    const y = T + rowH * i + rowH / 2;
    s += `<text x="${L - 10}" y="${y + 4}" text-anchor="end">${esc(a.label)}</text>`;
    if (a.ci) {
      s += `<line x1="${X(a.ci[0])}" x2="${X(a.ci[1])}" y1="${y}" y2="${y}" stroke="var(--accent)" stroke-width="3" stroke-linecap="round"/>`;
      s += `<circle cx="${X(a.value)}" cy="${y}" r="5" fill="var(--accent)"/>`;
      s += `<text x="${X(a.ci[1]) + 8}" y="${y - 8}" class="v-strong">${a.value}/day</text><text x="${X(a.ci[1]) + 8}" y="${y + 8}">interval ${a.ci[0]} to ${a.ci[1]}</text>`;
    } else {
      s += `<rect x="${X(0) - 5}" y="${y - 5}" width="10" height="10" fill="var(--surface)" stroke="var(--accent)" stroke-width="2"/>`;
      s += `<text x="${X(0) + 14}" y="${y + 4}">0 (interval includes 0)</text>`;
    }
  });
  s += `<text x="${(L + W - R) / 2}" y="${H - 2}" text-anchor="middle">fitted kill rate per day (cohort data, not literature)</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Fitted kill rates per day. Temozolomide, TTFields and radiation are 0 with intervals including 0. Bevacizumab 0.003, interval 0 to 0.009.">${s}</svg>`;
}

/** Grouped horizontal bars of AUC, with the chance line at 0.5. */
function riskPlot(compact, W) {
  const r = MODEL_RESULTS.risk;
  const rows = compact
    ? [{ k: "Pooled", auc: r.pooled.auc, kind: "pooled" }, ...r.lodo.map((d) => ({ k: `Held out ${d.k}`, auc: d.auc, kind: "lodo" }))]
    : [
      { k: "Pooled, 676 patients", auc: r.pooled.auc, kind: "pooled" },
      ...r.within.map((d) => ({ k: `Within ${d.k}`, auc: d.auc, kind: "within" })),
      ...r.lodo.map((d) => ({ k: `Held out: ${d.k}`, auc: d.auc, kind: "lodo" })),
      { k: "IDH wild-type alone, UCSF", auc: r.idhWtUcsf, kind: "idh" },
    ];
  const rowH = compact ? 26 : 28, L = Math.min(compact ? 128 : 150, W * 0.42), R = 40, T = 6, B = 28;
  const H = T + rowH * rows.length + B;
  const lo = 0.4, hi = 0.85;
  const X = (v) => L + ((v - lo) / (hi - lo)) * (W - L - R);
  let s = `<line class="v-chance" x1="${X(0.5)}" x2="${X(0.5)}" y1="${T}" y2="${H - B}"/><text x="${X(0.5)}" y="${H - B + 16}" text-anchor="middle" style="fill:var(--bad)">chance 0.5</text>`;
  [0.6, 0.7, 0.8].forEach((v) => { s += `<text x="${X(v)}" y="${H - B + 16}" text-anchor="middle">${v}</text>`; });
  rows.forEach((d, i) => {
    const y = T + rowH * i;
    const fill = d.kind === "lodo" ? `url(#hatch-risk)` : d.kind === "idh" ? "var(--text-3)" : "var(--accent)";
    const stroke = d.kind === "lodo" ? "var(--accent)" : "none";
    s += `<text x="${L - 10}" y="${y + rowH / 2 + 4}" text-anchor="end">${esc(d.k)}</text>`;
    s += `<rect x="${X(lo)}" y="${y + 5}" width="${Math.max(2, X(d.auc) - X(lo))}" height="${rowH - 10}" rx="2" fill="${fill}" stroke="${stroke}" stroke-width="1.5"/>`;
    s += `<text x="${Math.max(X(d.auc), X(0.5)) + 6}" y="${y + rowH / 2 + 4}" class="v-strong">${d.auc.toFixed(2)}</text>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Risk model AUC. Pooled 0.74. Within dataset UCSF 0.78, MU 0.66, UCSD 0.69. Held-out dataset UCSF 0.72, MU 0.56, UCSD 0.46, which is chance.">`
    + `<defs><pattern id="hatch-risk" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="var(--surface)"/><line x1="0" y1="0" x2="0" y2="6" stroke="var(--accent)" stroke-width="2.5"/></pattern></defs>${s}</svg>`
    + (compact ? "" : `<p class="note">Solid bars are in-sample or within-dataset; hatched bars hold out a whole dataset. The axis starts at 0.4.</p>`);
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
    const grow = r.k === "with_treatment" || r.k === "treatment_blind";
    s += `<text x="${L - 10}" y="${y + rowH / 2 + 4}" text-anchor="end">${esc(r.label)}</text>`;
    if (r.mae === null) { s += `<text x="${L}" y="${y + rowH / 2 + 4}">${NA}</text>`; return; }
    s += `<rect x="${X(0)}" y="${y + 5}" width="${Math.max(2, X(r.mae) - X(0))}" height="${rowH - 10}" rx="2" fill="${grow ? "var(--warn)" : "var(--accent)"}" ${grow ? 'fill-opacity=".85"' : ""}/>`;
    s += `<text x="${X(r.mae) + 6}" y="${y + rowH / 2 + 4}" class="v-strong">${r.mae.toFixed(2)}</text>`;
  });
  s += `<text x="${L}" y="${H - 6}">typical error of the log burden ratio, lower is better</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Forecast error by method: ${rows.map((r) => `${r.label} ${r.mae === null ? NA : r.mae.toFixed(2)}`).join("; ")}">${s}</svg>`;
}

/** Render fn(width) into the element and again whenever its width changes. */
function mount(id, fn) {
  const el = document.getElementById(id);
  if (!el) return;
  let lastW = 0;
  const draw = () => {
    const w = Math.max(240, Math.floor(el.clientWidth));
    if (w === lastW) return;
    lastW = w;
    el.innerHTML = fn(w);
  };
  new ResizeObserver(draw).observe(el);
  draw();
}

export function renderStatic() {
  mount("bento-fit", (w) => fitPlot(w));
  mount("bento-risk", (w) => riskPlot(true, w));
  mount("risk-chart", (w) => riskPlot(false, w));
}

export function renderFromData(cohort) {
  const m = cohort.meta;
  // dataset line
  const ds = m.dataset ? DATASET_LABEL[m.dataset] || m.dataset : null;
  document.getElementById("mc-dataset").innerHTML =
    `Cases in this build: <strong>${cohort.patients.length}</strong>${ds ? ` from ${esc(ds)}` : ""}${cohort.kind === "fixture" ? ' <span class="tag tag-fixture">sample data</span>' : ""}. `
    + `Diffusion D is fixed at ${isNum(m.D) ? m.D + " mm²/day" : NA}; forecast horizon ${isNum(m.horizon) ? Math.round(m.horizon) + " days" : NA}.`;

  // priors
  const tb = document.querySelector("#priors-table tbody");
  if (m.effects && m.effects.values && m.effects.evidence) {
    const rows = Object.keys(m.effects.evidence).map((a) => {
      const v = m.effects.values[a];
      const sup = v && isNum(v.suppress) ? (v.suppress > 0 ? `${(v.suppress * 100).toFixed(0)}%` : "not modelled") : NA;
      return `<tr><th scope="row">${esc(AGENT_LABEL[a] || a)}</th><td class="num">${sup}</td><td>${esc(m.effects.evidence[a])}</td></tr>`;
    });
    if (m.effects.radiation) rows.push(`<tr><th scope="row">Radiation</th><td class="num">${isNum(m.effects.values.radiation?.k_per_gy) ? m.effects.values.radiation.k_per_gy + " per Gy" : NA}</td><td>${esc(m.effects.radiation)}</td></tr>`);
    tb.innerHTML = rows.join("");
  } else {
    tb.innerHTML = `<tr><td colspan="3">${NA}</td></tr>`;
  }

  // forecast check
  const fm = m.forecastMetrics;
  const rows = forecastRows(fm);
  const n = fm && fm.n_forecasts, np = fm && fm.n_patients;
  const where = isNum(n) && isNum(np) ? `${n} forecasts from ${np} patients` : "an unknown number of forecasts";
  const text = document.getElementById("mc-forecast-text");
  const ftb = document.querySelector("#forecast-table tbody");
  const note = document.getElementById("bento-forecast-note");
  const bars = document.getElementById("bento-forecast");
  if (rows.length) {
    const tw = rows.find((r) => r.k === "with_treatment"), nc = rows.find((r) => r.k === "no_change"), cm = rows.find((r) => r.k === "cohort_median_rate");
    const worse = tw && tw.mae !== null && cm && cm.mae !== null && tw.mae > cm.mae;
    const msg = `Each forecast predicts the next observed scan from the previous one and is scored on how far its predicted growth ratio is from the observed ratio (natural log). Based on ${where} in the sample cohort. `
      + (worse ? "The growth model, with or without the treatment schedule, did worse than predicting the cohort median rate or no change. Treat its forecasts as illustration, not prediction." : "");
    text.textContent = msg;
    note.textContent = worse
      ? `On ${where}, the model's error (${tw.mae.toFixed(2)}) was about ${(tw.mae / cm.mae).toFixed(1)} times that of simply using the cohort median growth rate (${cm.mae.toFixed(2)}). The scenarios show what the model does, not what will happen.`
      : `Based on ${where}.`;
    ftb.innerHTML = rows.map((r) =>
      `<tr><th scope="row">${esc(r.label)}</th><td class="num">${r.mae === null ? NA : r.mae.toFixed(2)}</td><td class="num">${r.factor === null ? NA : `${r.factor.toFixed(1)}x`}</td><td class="num">${r.dir === null ? (r.k === "no_change" ? "not defined" : NA) : `${(r.dir * 100).toFixed(0)}%`}</td></tr>`).join("");
    mount("bento-forecast", (w) => forecastBars(rows, w));
  } else {
    text.textContent = `Forecast metrics are ${NA} in this data file.`;
    note.textContent = "";
    bars.innerHTML = "";
    ftb.innerHTML = "";
  }
}
