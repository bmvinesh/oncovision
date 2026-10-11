// Guided tour: pick a patient, play the forecast, compare two scenarios, see how the explanation was checked.
// Runs by itself in about 90 seconds ("Play the guided tour" or ?tour=1); each step is also a button.
// With prefers-reduced-motion nothing advances or plays by itself: each step jumps to its end state.
import { esc, scenInfo } from "./format.js";

const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const $ = (id) => document.getElementById(id);

export function initTour(ctl) {
  const bar = $("tour-bar"), nav = $("steps");
  if (!bar || !nav) return { start() {} };
  const items = [...nav.querySelectorAll("li")];
  let cur = -1, auto = false, paused = false, timers = [], lit = null;

  const later = (fn, ms) => { const id = setTimeout(fn, ms); timers.push(id); return id; };
  const clear = () => { timers.forEach(clearTimeout); timers = []; };
  const show = (el) => el && el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  const focusOn = (...ids) => {
    document.querySelectorAll(".tour-focus").forEach((e) => e.classList.remove("tour-focus"));
    ids.forEach((id) => $(id) && $(id).classList.add("tour-focus"));
  };
  /** Advance when playback has stopped (or at once when nothing plays), plus a pause to read. */
  const afterPlayback = (hold) => {
    const poll = () => { if (ctl.state().playing) later(poll, 250); else later(next, hold); };
    later(poll, 400);
  };
  const runPlayback = () => {
    const p = ctl.state().patient;
    if (!p) return;
    if (reduce) ctl.jumpTo(p.tEnd); else { ctl.jumpTo(p.lastScan); ctl.play(); }
  };

  const STEPS = [
    {
      text: () => "Pick a patient. Each one is a real case from a public, de-identified research cohort.",
      run() { $("picker").open = true; show($("workspace")); focusOn("picker"); if (auto) later(next, 7000); },
    },
    {
      text: () => "Play the forecast. Up to the last scan the tumour is measured. After it the simulation runs forward, and that part is illustrative.",
      run() { ctl.setCompare(""); show($("stage-panel")); focusOn("stage-panel"); runPlayback(); if (auto) afterPlayback(3500); },
    },
    {
      text: () => {
        const s = ctl.state();
        return s.compare
          ? `Compare two scenarios. Left: ${scenInfo(s.scenario).label}. Right: ${scenInfo(s.compare).label}. The same tumour, two assumptions.`
          : "Compare two scenarios. Choose a second scenario next to the patient name to see them side by side.";
      },
      run() {
        const k = ctl.bestCompare();
        if (k) ctl.setCompare(k);
        show($("stage-panel")); focusOn("stage-panel", "scen-block"); runPlayback(); paint();
        if (auto) afterPlayback(6000);
      },
    },
    {
      text: () => "See how the explanation was checked. Each sentence shows where it came from and what it cites. Objections sit next to the judge's ruling.",
      run() {
        ctl.stop(); show($("checked")); focusOn();
        const rows = [...document.querySelectorAll("#ro-explain .ledger > li")];
        if (!auto || reduce || !rows.length) { if (auto) later(next, 12000); return; }
        rows.forEach((li, i) => later(() => {
          if (lit) lit.classList.remove("is-lit");
          lit = li; li.classList.add("is-lit");
          if (li.querySelector(".objection")) li.scrollIntoView({ behavior: "smooth", block: "center" });
        }, 2500 + i * 1500));
        later(next, 2500 + rows.length * 1500 + 5000);
      },
    },
  ];

  function paint() {
    items.forEach((li, i) => { li.classList.toggle("is-current", i === cur); li.classList.toggle("is-done", cur > i); });
    items.forEach((li, i) => li.firstElementChild.setAttribute("aria-current", i === cur ? "step" : "false"));
    if (cur < 0) { bar.hidden = true; return; }
    const done = cur >= STEPS.length;
    const text = done ? "That is the tour. Everything on this page is open to explore." : STEPS[cur].text();
    bar.hidden = false;
    bar.innerHTML = `<div><p class="eyebrow">${done ? "Tour finished" : `Step ${cur + 1} of ${STEPS.length}`}</p><p aria-live="polite">${esc(text)}</p></div>`
      + `<div class="tour-btns">`
      + (auto && !done && !reduce ? `<button class="btn" type="button" data-t="pause">${paused ? "Resume" : "Pause"}</button>` : "")
      + (!done ? `<button class="btn" type="button" data-t="next">Next</button>` : `<button class="btn" type="button" data-t="replay">Replay</button>`)
      + `<button class="btn" type="button" data-t="end">${done ? "Close" : "End tour"}</button></div>`
      + `<div class="tour-prog" aria-hidden="true"><span style="width:${Math.min(100, ((cur + (done ? 0 : 1)) / STEPS.length) * 100)}%"></span></div>`;
  }

  function go(i) {
    clear();
    if (lit) { lit.classList.remove("is-lit"); lit = null; }
    cur = i; paused = false;
    if (cur >= STEPS.length) { auto = false; ctl.stop(); focusOn(); paint(); return; }
    paint();
    STEPS[cur].run();
  }
  function next() { go(cur + 1); }
  function end() { clear(); ctl.stop(); focusOn(); if (lit) lit.classList.remove("is-lit"); cur = -1; auto = false; paint(); }
  function start() { auto = !reduce; ctl.prepare(); go(0); }

  bar.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-t]");
    if (!b) return;
    const t = b.dataset.t;
    if (t === "next") next();
    else if (t === "end") end();
    else if (t === "replay") start();
    else if (t === "pause") {
      paused = !paused;
      if (paused) { clear(); ctl.stop(); } else go(cur);
      if (paused) paint();
      bar.querySelector('[data-t="pause"]')?.focus();
    }
  });
  nav.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-step]");
    if (b) { auto = false; go(Number(b.dataset.step)); }
  });
  $("tour-start").addEventListener("click", start);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && cur >= 0) end(); });
  return { start };
}
