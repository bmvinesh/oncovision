// Plain-language explanation block: AI-written text, how it was checked, and an optional live re-run.
// Works without the API (static mode): the baked narrative is shown and the re-run button stays hidden.
import { esc } from "./format.js";

const CHECKED = {
  "debate-verified": "Drafted by one agent, audited sentence by sentence by a second agent, and checked by a rule-based judge.",
  "partially-verified": "Drafted and audited by two agents and checked by a rule-based judge. Sentences that did not pass were replaced by the verified source statements.",
  "judge-only": "Drafted by an agent and checked by a rule-based judge. The second agent did not audit every sentence.",
  template: "Assembled from verified statements. No generated wording.",
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

function traceHtml(n) {
  const t = n.trace;
  if (!t || !Array.isArray(t.rounds)) return "";
  const a = t.agents || {};
  const agent = (label, x, extra = "") => x
    ? `<tr><th scope="row">${label}</th><td>${esc(x.model || x.type || "")}</td><td class="num">${x.calls ?? "-"}</td><td class="num">${x.ms != null ? (x.ms / 1000).toFixed(1) + " s" : "-"}</td><td>${extra}</td></tr>` : "";
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
    const overruled = Object.values(r.skeptic || {}).filter((o) => o.outcome !== "upheld");
    return `<div class="dbt-round"><h5>Round ${r.round}: ${(r.flagged || []).length ? `${r.flagged.length} sentence${r.flagged.length > 1 ? "s" : ""} sent back` : "nothing sent back"}</h5>`
      + (rows ? `<ul>${rows}</ul>` : "")
      + (overruled.length ? `<p class="note">${overruled.length} Skeptic objection${overruled.length > 1 ? "s were" : " was"} set aside by the Judge after checking the numbers and wording.</p>` : "")
      + (r.skeptic_error ? `<p class="note">Second agent: ${esc(r.skeptic_error)}</p>` : "") + `</div>`;
  }).join("");
  const origins = (t.final || []).reduce((m, f) => { m[f.origin] = (m[f.origin] || 0) + 1; return m; }, {});
  const comp = Object.entries(origins).map(([k, v]) => `${v} ${k === "ledger" ? "from the verified statement" : k === "revised" ? "revised after review" : "as drafted"}`).join(", ");
  return `<details class="ro-details dbt"><summary>How this was checked</summary>
    <div class="table-wrap"><table class="data"><thead><tr><th scope="col">Agent</th><th scope="col">Model</th><th scope="col" class="num">Calls</th><th scope="col" class="num">Time</th><th scope="col"></th></tr></thead><tbody>
      ${agent("Advocate", a.advocate, "writes, citing statements")}
      ${agent("Skeptic", a.skeptic, "audits every sentence")}
      ${agent("Judge", a.judge && { model: "rule-based, no model", calls: null, ms: null }, "has the last word")}
    </tbody></table></div>
    ${rounds}
    ${comp ? `<p class="note">Final text: ${esc(comp)}, plus the fixed closing line.</p>` : ""}
    <details class="dbt-claims"><summary>Verified statements the text may use (${(n.claims || []).length})</summary><ol>${(n.claims || []).map((c) => `<li>${esc(c)}</li>`).join("")}</ol></details>
  </details>`;
}

export function renderExplanation(el, narr, { uid = null, kind = "case", onUpdate = null } = {}) {
  if (!el) return;
  const api = apiState && apiState.ok;
  if (!narr || typeof narr.text !== "string" || !narr.text.trim()) {
    el.hidden = !api || (!uid && kind !== "results");
    if (!el.hidden) {
      el.innerHTML = `<h3>In plain words</h3><p class="note">No explanation is stored for this patient.</p>
        <button class="btn" type="button" data-act="gen">Write one with the local model</button><p class="note" data-role="status" aria-live="polite"></p>`;
      bind(el, uid, kind, onUpdate);
    }
    return;
  }
  el.hidden = false;
  const ai = narr.source === "llm";
  const ms = Number.isFinite(narr.elapsed_ms) ? ` in ${(narr.elapsed_ms / 1000).toFixed(1)} s` : "";
  const how = CHECKED[narr.verification] || (ai ? "Checked against the verified statements." : CHECKED.template);
  el.innerHTML = `
    <h3>In plain words ${ai ? '<span class="tag tag-ai">AI-written</span>' : '<span class="tag tag-fixture">template</span>'}</h3>
    <p class="expl-text">${esc(narr.text)}</p>
    <p class="note">${esc(how)}${ai ? ` Model ${esc(narr.model || "local")}${ms}, running on this device; nothing was sent elsewhere.` : ""}</p>
    ${api ? '<button class="btn" type="button" data-act="rerun">Re-run live</button>' : ""}
    <p class="note" data-role="status" aria-live="polite"></p>
    ${traceHtml(narr)}`;
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
