// "On this device" strip. Rendered only when /api/health answers, and only from fields in that answer:
// device, model, llm.loaded, cases, explanations. On static hosting the strip stays hidden.
import { esc, isNum } from "./format.js";

export function renderDevice(el, api) {
  if (!el) return;
  const h = api && api.ok && api.health && typeof api.health === "object" ? api.health : null;
  if (!h) { el.hidden = true; return; }
  const bits = ["<span>On this device</span>"];
  if (typeof h.device === "string" && h.device) bits.push(`<span><b>${esc(h.device)}</b></span>`);
  const llm = h.llm && typeof h.llm === "object" ? h.llm : null;
  const loaded = llm ? llm.loaded : null;
  if (typeof h.model === "string" && h.model) {
    // llm.loaded is a list of loaded model names, or a boolean in older builds
    const isLoaded = Array.isArray(loaded) ? loaded.includes(h.model) : loaded === true ? true : loaded === false ? false : null;
    bits.push(`<span>model <b>${esc(h.model)}</b>${isLoaded === true ? ", loaded" : isLoaded === false ? ", not loaded" : ""}</span>`);
  } else if (Array.isArray(loaded) && loaded.length) {
    bits.push(`<span>loaded <b>${loaded.map(esc).join(", ")}</b></span>`);
  }
  if (isNum(h.cases)) bits.push(`<span><b>${h.cases}</b> ${h.cases === 1 ? "case" : "cases"}</span>`);
  const ex = h.explanations;
  if (isNum(ex)) bits.push(`<span><b>${ex}</b> ${ex === 1 ? "explanation" : "explanations"}</span>`);
  else if (ex && typeof ex === "object") {
    const parts = [ex.protocol, ex.judge ? `${ex.judge} judge` : null].filter((v) => typeof v === "string" && v);
    if (parts.length) bits.push(`<span>explanations <b>${parts.map(esc).join(", ")}</b></span>`);
  }
  if (bits.length < 2) { el.hidden = true; return; }
  el.innerHTML = bits.join("");
  el.hidden = false;
}
