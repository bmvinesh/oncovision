// Burden-over-time chart. Plain SVG, re-rendered at the real pixel width so type stays legible on phones.
// Scenario encodings: colour + dash + marker shape. The playhead is a keyboard-operable slider.
import { fmtVol, fmtDay, scenInfo, markerSvg, esc, AGENT_LABEL, isNum } from "./format.js";
import { interp } from "./data.js";

const M = { l: 62, t: 14, b: 30 };

function niceLinearTicks(max, n = 5) {
  const raw = max / n;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const f = raw / mag;
  const step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * mag;
  const out = [];
  for (let v = 0; v <= max * 1.001; v += step) out.push(v);
  return out;
}
function logTicks(min, max) {
  const out = [];
  const lo = Math.floor(Math.log10(min)), hi = Math.ceil(Math.log10(max));
  for (let e = lo; e <= hi; e++) for (const m of [1, 2, 5]) { const v = m * Math.pow(10, e); if (v >= min * 0.999 && v <= max * 1.001) out.push(v); }
  return out.length > 7 ? out.filter((v) => /^[15]/.test(String(v))) : out;
}
function dayTicks(tEnd, width) {
  const target = Math.max(3, Math.floor(width / 90));
  const raw = tEnd / target;
  const steps = [10, 20, 30, 50, 60, 90, 100, 120, 150, 180, 200, 250, 300, 365, 500];
  const step = steps.find((s) => s >= raw) || 500;
  const out = [];
  for (let d = 0; d <= tEnd; d += step) out.push(d);
  return out;
}
const same = (a, b) => a.length === b.length && a.every((v, i) => Math.abs(v - b[i]) <= 1e-6 * Math.max(1, Math.abs(v)));

export class Chart {
  constructor(el, { onScrub, onStep }) {
    this.el = el;
    this.onScrub = onScrub;
    this.onStep = onStep;
    this.data = null;
    this.t = 0;
    this.ro = new ResizeObserver(() => { if (this.data) this.render(); });
    this.ro.observe(el);
  }

  setData(d) { this.data = d; this.render(); }
  setTime(t) { this.t = t; this.updatePlayhead(); }

  render() {
    const d = this.data;
    if (!d || !d.patient) { this.el.innerHTML = ""; return; }
    const p = d.patient;
    const W = Math.max(280, this.el.clientWidth);
    const wide = W >= 640;
    const R = wide ? 118 : 14;
    const lanes = this.lanes(p);
    const plotH = Math.round(Math.min(360, Math.max(230, W * 0.42)));
    const laneH = lanes.length ? lanes.length * 12 + 22 : 0;
    const H = M.t + plotH + M.b + laneH;
    const x0 = M.l, x1 = W - R, y0 = M.t, y1 = M.t + plotH;
    const tEnd = p.tEnd;
    const X = (t) => x0 + (t / tEnd) * (x1 - x0);

    const vis = p.scenarioKeys.filter((k) => d.visible.has(k) || k === d.active);
    const vals = [];
    p.observed.forEach((o) => { if (isNum(o.seed) && o.seed > 0) vals.push(o.seed); });
    vis.forEach((k) => p.scenarios[k].burden.forEach((v) => { if (isNum(v) && v > 0) vals.push(v); }));
    if (!vals.length) { this.el.innerHTML = '<p class="note">No burden values are available for this patient.</p>'; return; }
    const vmin = Math.min(...vals), vmax = Math.max(...vals);
    const log = !!d.log;
    const yMin = log ? vmin * 0.7 : 0;
    const yMax = log ? vmax * 1.4 : vmax * 1.08;
    const Y = (v) => {
      if (log) { const a = Math.log(Math.max(v, yMin)), lo = Math.log(yMin), hi = Math.log(yMax); return y1 - ((a - lo) / (hi - lo)) * (y1 - y0); }
      return y1 - ((v - yMin) / (yMax - yMin)) * (y1 - y0);
    };
    this.X = X; this.Y = Y; this.geom = { x0, x1, y0, y1, tEnd, W, H };

    let g = "";
    const yt = log ? logTicks(yMin, yMax) : niceLinearTicks(yMax);
    for (const v of yt) {
      g += `<line class="grid" x1="${x0}" x2="${x1}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}"/>`;
      g += `<text class="ax" x="${x0 - 8}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${esc(fmtVol(v))}</text>`;
    }
    for (const t of dayTicks(tEnd, x1 - x0)) {
      g += `<line class="grid" x1="${X(t).toFixed(1)}" x2="${X(t).toFixed(1)}" y1="${y1}" y2="${y1 + 4}"/>`;
      g += `<text class="ax" x="${X(t).toFixed(1)}" y="${y1 + 18}" text-anchor="middle">${t} d</text>`;
    }
    // forecast region
    const xl = X(p.lastScan);
    g = `<defs><pattern id="hatch" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="7" stroke="var(--warn)" stroke-opacity=".22" stroke-width="1.5"/></pattern></defs>`
      + `<rect x="${xl.toFixed(1)}" y="${y0}" width="${Math.max(0, x1 - xl).toFixed(1)}" height="${y1 - y0}" fill="url(#hatch)"/>`
      + `<line x1="${xl.toFixed(1)}" x2="${xl.toFixed(1)}" y1="${y0}" y2="${y1}" stroke="var(--text-3)" stroke-dasharray="4 4"/>`
      + (wide ? `<text class="ax" x="${(xl + 6).toFixed(1)}" y="${y0 + 12}">last scan: forecast starts (illustrative)</text>`
        : `<text class="ax" x="${x1 - 3}" y="${y0 + 12}" text-anchor="end">forecast<tspan x="${x1 - 3}" dy="12">(illustrative)</tspan></text>`)
      + g;
    g += `<text class="ax" x="${x0 + 6}" y="${y0 + 12}">measured</text>`;

    // scenario curves, inactive first so the active one sits on top
    const order = [...vis.filter((k) => k !== d.active), ...(vis.includes(d.active) ? [d.active] : [])];
    for (const k of order) {
      const s = p.scenarios[k], si = scenInfo(k, p.scenarioKeys.indexOf(k));
      if (s.lo && s.hi && k === d.active) {
        // uncertainty band, drawn only when the data file carries one for this scenario
        const up = s.day.map((t, i) => `${X(t).toFixed(1)},${Y(Math.max(s.hi[i], yMin)).toFixed(1)}`);
        const dn = s.day.map((t, i) => `${X(t).toFixed(1)},${Y(Math.max(s.lo[i], yMin)).toFixed(1)}`).reverse();
        g += `<polygon points="${[...up, ...dn].join(" ")}" fill="${si.color}" fill-opacity=".14" stroke="${si.color}" stroke-opacity=".5" stroke-width="1" stroke-dasharray="2 3"><title>Uncertainty range for ${esc(si.label)} (illustrative)</title></polygon>`;
      }
      const pts = s.day.map((t, i) => (isNum(s.burden[i]) && s.burden[i] > 0 ? `${X(t).toFixed(1)},${Y(s.burden[i]).toFixed(1)}` : null)).filter(Boolean).join(" ");
      const act = k === d.active;
      g += `<polyline points="${pts}" fill="none" stroke="${si.color}" stroke-width="${act ? 3 : 1.8}" ${si.dash ? `stroke-dasharray="${si.dash}"` : ""} stroke-linejoin="round" stroke-linecap="round" opacity="${act ? 1 : 0.9}"/>`;
    }
    // measured scans
    const op = p.observed.filter((o) => isNum(o.seed) && o.seed > 0);
    if (op.length > 1) g += `<polyline points="${op.map((o) => `${X(o.day).toFixed(1)},${Y(o.seed).toFixed(1)}`).join(" ")}" fill="none" stroke="var(--text)" stroke-width="1.5" opacity=".55"/>`;
    for (const o of op) {
      g += `<circle cx="${X(o.day).toFixed(1)}" cy="${Y(o.seed).toFixed(1)}" r="4.5" fill="var(--surface)" stroke="var(--text)" stroke-width="2"><title>Scan ${o.index + 1}, day ${fmtDay(o.day)}: burden ${esc(fmtVol(o.seed))} (measured)</title></circle>`;
    }
    // direct labels at the right end
    if (wide) {
      const groups = [];
      for (const k of vis) {
        const s = p.scenarios[k];
        const gr = groups.find((q) => same(p.scenarios[q.keys[0]].burden, s.burden));
        if (gr) gr.keys.push(k); else groups.push({ keys: [k], y: Y(s.burden[s.burden.length - 1]), end: s.day[s.day.length - 1] });
      }
      groups.sort((a, b) => a.y - b.y);
      let prev = -99;
      for (const gr of groups) { gr.ly = Math.max(gr.y, prev + 13); prev = gr.ly; }
      for (const gr of groups) {
        const si = scenInfo(gr.keys[0], 0);
        const txt = gr.keys.map((k) => scenInfo(k).short).join(" = ");
        g += `<line x1="${X(gr.end).toFixed(1)}" y1="${gr.y.toFixed(1)}" x2="${x1 + 6}" y2="${gr.ly.toFixed(1)}" stroke="${si.color}" stroke-width="1"/>`;
        g += `<text class="lab" x="${x1 + 9}" y="${(gr.ly + 4).toFixed(1)}" style="fill:${si.color}">${esc(txt)}</text>`;
      }
    }
    // treatment lanes
    let ly = y1 + M.b + 6;
    if (lanes.length) {
      g += `<text class="ax" x="${x0}" y="${ly + 2}">Recorded treatment (clinical sheet)</text>`;
      ly += 8;
      lanes.forEach((ln, i) => {
        const yy = ly + i * 12;
        g += `<text class="ax" x="${x0 - 8}" y="${yy + 8}" text-anchor="end" style="font-size:10px">${esc(ln.label)}</text>`;
        g += `<line class="grid" x1="${x0}" x2="${x1}" y1="${yy + 5}" y2="${yy + 5}" stroke-opacity=".5"/>`;
        for (const sp of ln.spans) {
          const a = Math.max(0, sp.a), b = Math.min(tEnd, sp.b);
          if (b <= a) continue;
          g += `<rect x="${X(a).toFixed(1)}" y="${yy}" width="${Math.max(3, X(b) - X(a)).toFixed(1)}" height="9" fill="${ln.color}" opacity=".85"><title>${esc(ln.label)}: day ${fmtDay(sp.a)} to ${fmtDay(sp.b)}${sp.a < 0 ? " (started before the first scan)" : ""}</title></rect>`;
        }
      });
    }
    // playhead
    g += `<g id="ph-marks"></g>`
      + `<g class="ph" id="ph" tabindex="0" role="slider" aria-label="Time, days since first scan" aria-orientation="horizontal" aria-valuemin="0" aria-valuemax="${Math.round(tEnd)}">`
      + `<line id="ph-line" x1="0" x2="0" y1="${y0}" y2="${y1}" stroke="var(--teal)" stroke-width="2"/>`
      + `<path class="ph-handle" id="ph-handle" d="M-7 ${y0 - 2} h14 v9 l-7 6 l-7 -6z" fill="var(--teal)" stroke="var(--surface)" stroke-width="1.5"/>`
      + `<rect class="ph-hit" x="-12" y="${y0}" width="24" height="${y1 - y0}" fill="transparent"/>`
      + `</g>`;

    this.el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Tumour burden over time for ${esc(p.id)}. Measured scans are circles; forecasts start at the last scan and are illustrative.">${g}`
      + `<rect id="plot-hit" x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}" fill="transparent" style="cursor:ew-resize;touch-action:none"/></svg>`;
    // keep the playhead above the hit rect
    const svg = this.el.querySelector("svg");
    svg.appendChild(svg.querySelector("#ph-marks"));
    svg.appendChild(svg.querySelector("#ph"));
    this.svg = svg;
    this.bind();
    this.updatePlayhead();
  }

  lanes(p) {
    const map = new Map();
    const add = (key, label, color, a, b) => {
      if (!isNum(a) || !isNum(b)) return;
      if (!map.has(key)) map.set(key, { label, color, spans: [] });
      map.get(key).spans.push({ a, b });
    };
    const COL = { temozolomide: "var(--s-tmz)", ttfields: "var(--s-ttf)", bevacizumab: "var(--s-bev)" };
    p.drugs.forEach((dr) => add(dr.agent, AGENT_LABEL[dr.agent] || dr.agent, COL[dr.agent] || "var(--text-3)", dr.start_day, dr.end_day));
    p.radiation.forEach((r) => add("radiation", "Radiation", "var(--warn)", r.start_day, r.end_day));
    return [...map.values()];
  }

  tFromEvent(e) {
    const r = this.svg.getBoundingClientRect();
    const sx = (this.geom.W / r.width) || 1;
    const x = (e.clientX - r.left) * sx;
    return Math.min(this.geom.tEnd, Math.max(0, ((x - this.geom.x0) / (this.geom.x1 - this.geom.x0)) * this.geom.tEnd));
  }

  bind() {
    const svg = this.svg;
    let down = false;
    const move = (e) => { if (down) this.onScrub(Math.round(this.tFromEvent(e))); };
    svg.addEventListener("pointerdown", (e) => {
      down = true;
      try { svg.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      this.onScrub(Math.round(this.tFromEvent(e)));
    });
    svg.addEventListener("pointermove", move);
    const up = () => { down = false; };
    svg.addEventListener("pointerup", up);
    svg.addEventListener("pointercancel", up);
    const ph = svg.querySelector("#ph");
    ph.addEventListener("keydown", (e) => {
      const big = e.shiftKey ? 7 : 1;
      if (e.key === "ArrowRight" || e.key === "ArrowUp") this.onStep(big);
      else if (e.key === "ArrowLeft" || e.key === "ArrowDown") this.onStep(-big);
      else if (e.key === "PageUp") this.onStep(30);
      else if (e.key === "PageDown") this.onStep(-30);
      else if (e.key === "Home") this.onScrub(0);
      else if (e.key === "End") this.onScrub(this.geom.tEnd);
      else return;
      e.preventDefault();
    });
  }

  updatePlayhead() {
    if (!this.svg || !this.geom) return;
    const p = this.data.patient;
    const t = Math.min(this.geom.tEnd, Math.max(0, this.t));
    const x = this.X(t);
    const ph = this.svg.querySelector("#ph");
    ph.setAttribute("transform", `translate(${x.toFixed(1)},0)`);
    ph.setAttribute("aria-valuenow", String(Math.round(t)));
    ph.setAttribute("aria-valuetext", `day ${Math.round(t)}${t > p.lastScan ? `, ${Math.round(t - p.lastScan)} days after the last scan (forecast)` : ", measured period"}`);
    // markers on each visible curve at the playhead
    let m = "";
    if (t >= p.lastScan) {
      const vis = p.scenarioKeys.filter((k) => this.data.visible.has(k) || k === this.data.active);
      for (const k of vis) {
        const s = p.scenarios[k];
        const v = interp(s.day, s.burden, t);
        if (!isNum(v) || v <= 0) continue;
        const si = scenInfo(k, 0);
        const act = k === this.data.active;
        m += `<g transform="translate(${x.toFixed(1)},${this.Y(v).toFixed(1)})" style="color:${si.color}">${markerSvg(si.marker, act ? 5.5 : 4, `fill="var(--surface)" stroke="currentColor" stroke-width="2"`)}</g>`;
      }
    } else {
      const si = p.observed.reduce((a, o, i) => (o.day <= t + 1e-6 ? i : a), 0);
      const o = p.observed[si];
      if (isNum(o.seed) && o.seed > 0) m += `<circle cx="${this.X(o.day).toFixed(1)}" cy="${this.Y(o.seed).toFixed(1)}" r="7" fill="none" stroke="var(--teal)" stroke-width="2"/>`;
    }
    this.svg.querySelector("#ph-marks").innerHTML = m;
  }
}
