// Plain-language explanation block: AI-written text shown as a sentence ledger, with the agents that
// produced it, the objections raised and the judge's rulings. Optional live re-run.
// Works without the API (static mode): the baked narrative is shown and the re-run button stays hidden.
// Everything shown here is read from the narrative object; counts are counted from its trace.
import { esc } from "./format.js";

const CHECKED = {
  "debate-verified": "Drafted by one agent, audited sentence by sentence by a second agent, and checked by a rule-based judge.",
  "partially-verified": "Drafted and audited by two agents and checked by a rule-based judge. Sentences that did not pass were replaced by the verified source statements.",
  "judge-only": "Drafted by an agent and checked by a rule-based judge. The second agent did not audit every sentence.",
  template: "Assembled from verified statements. No generated wording.",
};

const ORIGIN = {
  advocate: { cls: "stamp-advocate", label: "as drafted" },
  revised: { cls: "stamp-revised", label: "revised after review" },
  ledger: { cls: "stamp-ledger", label: "verified statement used" },
  fixed: { cls: "stamp-fixed", label: "fixed closing line" },
};

let apiState = null; // null = not probed, otherwise { ok, health }

export async function probeApi() {
  if (apiState) return apiState;
  try {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 1500);
    const r = await fetch("/api/health", { signal: ctl.signal, cache: "no-store" });
    clearTimeout(timer);
    apiState = r.ok ? { ok: true, health: await r.json() } : { ok: false };
  } catch {
    apiState = { ok: false };
  }
  return apiState;
}

const upheld = (o) => typeof o.outcome === "string" && o.outcome.trim().toLowerCase().startsWith("upheld");
const secs = (ms) => (Number.isFinite(ms) ? `${(ms / 1000).toFixed(1)} s` : null);
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/** Objections per final sentence, matched on the sentence text (or the quoted text) in any round. */
function objectionsFor(t, text) {
  const out = [];
  for (const r of t.rounds || []) {
    const sents = r.sentences || [];
    for (const [i, o] of Object.entries(r.skeptic || {})) {
      const s = sents[Number(i)];
      if ((s && s.text === text) || o.quote === text) out.push({ round: r.round, o, judge: (r.judge || {})[i] || [] });
    }
  }
  return out;
}

function benchHtml(t) {
  const a = t.agents || {};
  const meta = (x) => [x.model, x.calls != null ? plural(x.calls, "call", "calls") : null, secs(x.ms)].filter(Boolean).map(esc).join(" · ");
  const li = (cls, role, does, m) => `<li class="${cls}"><span class="role">${role}</span><span class="does">${does}</span>${m ? `<span class="meta">${m}</span>` : ""}</li>`;
  return `<ol class="bench" aria-label="Who checked this text, in order">`
    + (a.advocate ? li("", "Advocate", "Writes the text, citing a verified statement for each sentence.", meta(a.advocate)) : "")
    + (a.skeptic ? li("", "Skeptic", "Audits every sentence and raises objections.", meta(a.skeptic)) : "")
    + (a.judge ? li("is-judge", "Judge", "Checks numbers and wording, rules on each objection, has the last word.", "rule-based, no model") : "")
    + `</ol>`;
}

function tallyHtml(t, nSent) {
  const rounds = t.rounds || [];
  const objs = rounds.flatMap((r) => Object.values(r.skeptic || {}));
  const sentBack = rounds.reduce((n, r) => n + (r.flagged || []).length, 0);
  const cell = (v, k) => `<div><dd>${v}</dd><dt>${k}</dt></div>`;
  return `<dl class="tally">`
    + cell(nSent, nSent === 1 ? "sentence checked" : "sentences checked")
    + cell(rounds.length, rounds.length === 1 ? "round" : "rounds")
    + cell(objs.length, objs.length === 1 ? "objection from the Skeptic" : "objections from the Skeptic")
    + cell(objs.filter(upheld).length, "upheld by the Judge")
    + cell(objs.filter((o) => !upheld(o)).length, "overruled by the Judge")
    + cell(sentBack, sentBack === 1 ? "sentence sent back" : "sentences sent back")
    + `</dl>`;
}

function ledgerHtml(n) {
  const t = n.trace;
  const fin = t.final || [];
  const claims = n.claims || [];
  // the fixed closing line is appended after the debate; show it as its own row when the text ends with it
  const joined = fin.map((f) => f.text).join(" ");
  const rest = n.text.startsWith(joined) ? n.text.slice(joined.length).trim() : "";
  const rows = fin.map((f) => ({ text: f.text, cites: f.cites || [], origin: ORIGIN[f.origin] ? f.origin : "advocate" }));
  if (rest) rows.push({ text: rest, cites: [], origin: "fixed" });
  const items = rows.map((r) => {
    const og = ORIGIN[r.origin];
    const cited = r.cites.map((c) => ({ c, n: Number(String(c).replace(/\D/g, "")) })).filter((x) => claims[x.n - 1]);
    const cite = cited.length
      ? `<details class="cite"><summary>Cites ${cited.length === 1 ? "statement" : "statements"} ${cited.map((x) => x.n).join(", ")}</summary><ol>${cited.map((x) => `<li><b>${x.n}</b>${esc(claims[x.n - 1])}</li>`).join("")}</ol></details>` : "";
    const obj = r.origin === "fixed" ? [] : objectionsFor(t, r.text);
    const thread = obj.map(({ o, judge }) => {
      const out = String(o.outcome || "");
      const k = out.indexOf(":");
      const head = k > 0 ? out.slice(0, k) : out;
      const why = k > 0 ? out.slice(k + 1).trim() : "";
      return `<div class="objection"><p><b>Skeptic</b><span>${o.type ? `<span class="mono">${esc(o.type)}:</span> ` : ""}${esc(o.reason || "")}</span></p>`
        + judge.map((j) => `<p><b>Judge</b><span>${esc(j)}</span></p>`).join("")
        + `<p><b>Ruling</b><span><span class="ruling">${esc(head.charAt(0).toUpperCase() + head.slice(1))}.</span> ${esc(why ? why.charAt(0).toUpperCase() + why.slice(1) + "." : "")}</span></p></div>`;
    }).join("");
    return `<li><div class="l-body"><p class="l-text">${esc(r.text)}</p><span class="stamp ${og.cls}">${og.label}</span>`
      + (cite || thread ? `<div class="l-extra">${cite}${thread}</div>` : "") + `</div></li>`;
  }).join("");
  return { html: `<ol class="ledger" aria-label="The explanation, sentence by sentence">${items}</ol>`, complete: n.text.startsWith(joined) && fin.length > 0 };
}

/** The full record of each round, as before: what was sent back and why. */
function recordHtml(n) {
  const t = n.trace;
  const rounds = t.rounds.map((r) => {
    const sents = r.sentences || [];
    const rows = (r.flagged || []).map((i) => {
      const j = (r.judge || {})[String(i)] || [];
      const o = (r.skeptic || {})[String(i)];
      const bits = [
        ...j.map((x) => `<li><strong>Judge:</strong> ${esc(x)}</li>`),
        ...(o ? [`<li><strong>Skeptic (${esc(o.type)}):</strong> ${esc(o.reason)} <em>${esc(o.outcome)}</em></li>`] : []),
      ].join("");
      return `<li><q>${esc((sents[i] || {}).text || "")}</q><ul>${bits}</ul></li>`;
    }).join("");
    const overruled = Object.values(r.skeptic || {}).filter((o) => !upheld(o));
    return `<div class="dbt-round"><h5>Round ${r.round}: ${(r.flagged || []).length ? `${plural(r.flagged.length, "sentence", "sentences")} sent back` : "nothing sent back"}</h5>`
      + (rows ? `<ul>${rows}</ul>` : "")
      + (overruled.length ? `<p class="note">${overruled.length} Skeptic objection${overruled.length > 1 ? "s were" : " was"} set aside by the Judge after checking the numbers and wording.</p>` : "")
      + (r.skeptic_error ? `<p class="note">Second agent: ${esc(r.skeptic_error)}</p>` : "") + `</div>`;
  }).join("");
  return `<details class="ro-details dbt"><summary>Round-by-round record</summary>${rounds}
    <details class="dbt-claims"><summary>Verified statements the text may use (${(n.claims || []).length})</summary><ol>${(n.claims || []).map((c) => `<li>${esc(c)}</li>`).join("")}</ol></details>
  </details>`;
}

export function renderExplanation(el, narr, { uid = null, kind = "case", onUpdate = null } = {}) {
  if (!el) return;
  const api = apiState && apiState.ok;
  const title = kind === "results" ? "The results in plain words" : "In plain words";
  if (!narr || typeof narr.text !== "string" || !narr.text.trim()) {
    el.hidden = !api || (!uid && kind !== "results");
    if (!el.hidden) {
      el.innerHTML = `<div class="expl-head"><h3>${title}</h3></div><p class="note">No explanation is stored for this ${kind === "results" ? "data file" : "patient"}.</p>
        <div><button class="btn" type="button" data-act="gen">Write one with the local model</button></div><p class="note" data-role="status" aria-live="polite"></p>`;
      bind(el, uid, kind, onUpdate);
    }
    return;
  }
  el.hidden = false;
  const ai = narr.source === "llm";
  const ms = Number.isFinite(narr.elapsed_ms) ? ` in ${(narr.elapsed_ms / 1000).toFixed(1)} s` : "";
  const how = CHECKED[narr.verification] || (ai ? "Checked against the verified statements." : CHECKED.template);
  const t = narr.trace && Array.isArray(narr.trace.rounds) ? narr.trace : null;
  const led = t ? ledgerHtml(narr) : null;
  el.innerHTML = `
    <div class="expl-head">
      <h3>${title} ${ai ? '<span class="tag tag-ai">AI-written</span>' : '<span class="tag tag-fixture">template</span>'}</h3>
      ${api ? '<button class="btn" type="button" data-act="rerun">Re-run live</button>' : ""}
    </div>
    <p class="expl-how">${esc(how)}${ai ? ` Model ${esc(narr.model || "local")}${ms}, running on this device; nothing was sent elsewhere.` : ""}</p>
    <p class="note" data-role="status" aria-live="polite"></p>
    ${t ? benchHtml(t) + tallyHtml(t, (t.final || []).length) : ""}
    ${led && led.complete ? led.html : `<p class="expl-text">${esc(narr.text)}</p>${led ? led.html : ""}`}
    ${t ? recordHtml(narr) : ""}`;
  bind(el, uid, kind, onUpdate);
}

function bind(el, uid, kind, onUpdate) {
  const btn = el.querySelector("button[data-act]");
  if (!btn || (!uid && kind !== "results")) return;
  const status = el.querySelector('[data-role="status"]');
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    status.textContent = "Two agents are working on this, then the judge checks it. About 10 to 20 seconds.";
    try {
      const r = await fetch("/api/narrative", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ uid, kind, rerun: btn.dataset.act === "rerun" }),
      });
      if (!r.ok) throw new Error(`The service answered ${r.status}`);
      const n = await r.json();
      renderExplanation(el, n, { uid, kind, onUpdate });
      if (onUpdate) onUpdate(n);
    } catch (e) {
      status.textContent = `Could not run the local model: ${e.message}`;
      btn.disabled = false;
    }
  });
}
