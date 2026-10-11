// Number and label helpers. Missing values are never rendered as 0.
export const NA = "not available";
export const isNum = (v) => typeof v === "number" && Number.isFinite(v);

/** Cell-equivalent burden or volume in mm^3 -> readable string. */
export function fmtVol(mm3) {
  if (!isNum(mm3)) return NA;
  const a = Math.abs(mm3);
  if (a < 1000) return `${mm3.toFixed(0)} mm³`;
  if (a < 1e6) return `${(mm3 / 1000).toFixed(a < 1e4 ? 2 : a < 1e5 ? 1 : 0)} mL`;
  return `${(mm3 / 1e6).toFixed(2)} L`;
}
export function fmtPct(v, digits = 0) {
  if (!isNum(v)) return NA;
  const s = Math.abs(v) < 0.5 * Math.pow(10, -digits) ? 0 : v;
  return `${s > 0 ? "+" : s < 0 ? "−" : ""}${Math.abs(s).toFixed(digits)}%`;
}
export function fmtDay(d) {
  return isNum(d) ? `${Math.round(d)}` : NA;
}
export function fmtNum(v, digits = 3) {
  return isNum(v) ? v.toFixed(digits) : NA;
}
export const shortId = (uid) => String(uid).split(":").pop();
export const datasetOf = (uid) => (String(uid).includes(":") ? String(uid).split(":")[0] : "");

export const DATASET_LABEL = {
  mu_glioma_post: "MU-Glioma-Post",
  ucsd_ptgbm: "UCSD-PTGBM",
  ucsf_lptdg: "UCSF-LPTDG",
  lumiere: "LUMIERE",
};

export const STATUS = {
  ok: { label: "fit converged", cls: "st-ok", short: "ok" },
  shrinking: { label: "shrinking tumour (loss rate fitted)", cls: "st-shrinking", short: "shrinking" },
  no_convergence: { label: "fit did not converge", cls: "st-bad", short: "no convergence" },
  empty_seed: { label: "empty seed, no fit", cls: "st-bad", short: "empty seed" },
};
export function statusInfo(s) {
  return STATUS[s] || { label: s ? String(s).replace(/_/g, " ") : NA, cls: "st-none", short: s || NA };
}

export const SCENARIOS = {
  no_further_treatment: { label: "No further treatment", short: "None", color: "var(--s-none)", dash: "", marker: "circle" },
  as_recorded: { label: "As recorded in the chart", short: "As recorded", color: "var(--s-rec)", dash: "9 5", marker: "square" },
  tmz_6_cycles: { label: "Temozolomide, 6 cycles", short: "TMZ", color: "var(--s-tmz)", dash: "", marker: "triangle" },
  tmz_plus_ttfields: { label: "Temozolomide + TTFields", short: "TMZ+TTF", color: "var(--s-ttf)", dash: "2 4 9 4", marker: "diamond" },
  bevacizumab: { label: "Bevacizumab", short: "Bev", color: "var(--s-bev)", dash: "1.5 4", marker: "cross" },
};
const FALLBACK_COLORS = ["var(--text-2)", "var(--warn)"];
export function scenInfo(key, i = 0) {
  return SCENARIOS[key] || { label: String(key).replace(/_/g, " "), short: key, color: FALLBACK_COLORS[i % 2], dash: "", marker: "circle" };
}
export const AGENT_LABEL = {
  temozolomide: "Temozolomide", ttfields: "TTFields", bevacizumab: "Bevacizumab",
  lomustine_pcv: "Lomustine / PCV", immunotherapy: "Immunotherapy", radiation: "Radiation",
};

/** Escape for innerHTML. */
export function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/** SVG marker path for the chart; centred on 0,0 with size r. */
export function markerSvg(kind, r, attrs) {
  const a = attrs || "";
  switch (kind) {
    case "square": return `<rect x="${-r}" y="${-r}" width="${2 * r}" height="${2 * r}" ${a}/>`;
    case "triangle": return `<path d="M0 ${-r * 1.2} L${r * 1.15} ${r * 0.9} L${-r * 1.15} ${r * 0.9}Z" ${a}/>`;
    case "diamond": return `<path d="M0 ${-r * 1.3} L${r * 1.3} 0 L0 ${r * 1.3} L${-r * 1.3} 0Z" ${a}/>`;
    case "cross": return `<path d="M${-r} ${-r} L${r} ${r} M${r} ${-r} L${-r} ${r}" fill="none" ${a}/>`;
    default: return `<circle r="${r}" ${a}/>`;
  }
}
