// Patient picker: search, filters, sort and a virtualised listbox so hundreds of rows stay smooth.
import { esc, statusInfo, isNum, NA } from "./format.js";

const ROW = 52;
const OVERSCAN = 6;

function spark(p) {
  if (p._spark) return p._spark;
  const none = p.scenarios.no_further_treatment || p.scenarios[p.scenarioKeys[0]];
  const obs = p.observed.filter((o) => isNum(o.seed));
  const all = [...obs.map((o) => o.seed), ...none.burden.filter(isNum)];
  const lo = Math.min(...all), hi = Math.max(...all);
  const W = 64, H = 26, tEnd = p.tEnd || 1;
  const X = (t) => 2 + (t / tEnd) * (W - 4);
  const Y = (v) => (hi === lo ? H / 2 : H - 3 - ((v - lo) / (hi - lo)) * (H - 6));
  const o = obs.map((q) => `${X(q.day).toFixed(1)},${Y(q.seed).toFixed(1)}`).join(" ");
  const f = none.day.map((t, i) => (isNum(none.burden[i]) ? `${X(t).toFixed(1)},${Y(none.burden[i]).toFixed(1)}` : null)).filter(Boolean).join(" ");
  p._spark = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" aria-hidden="true"><polyline points="${o}" fill="none" stroke="var(--text-2)" stroke-width="1.5"/><polyline points="${f}" fill="none" stroke="var(--warn)" stroke-width="1.5" stroke-dasharray="3 2"/></svg>`;
  return p._spark;
}

export class Picker {
  constructor(els, { onSelect }) {
    this.els = els;
    this.onSelect = onSelect;
    this.all = [];
    this.rows = [];
    this.selected = null;
    this.cursor = 0;
    this.inner = document.createElement("div");
    this.inner.className = "plist-inner";
    els.list.appendChild(this.inner);
    els.list.addEventListener("scroll", () => this.paint());
    els.list.addEventListener("click", (e) => {
      const r = e.target.closest(".prow");
      if (r) { this.cursor = Number(r.dataset.i); this.choose(this.rows[this.cursor]); }
    });
    els.list.addEventListener("keydown", (e) => this.onKey(e));
    els.list.addEventListener("focus", () => this.paint());
    new ResizeObserver(() => this.paint()).observe(els.list);
    for (const k of ["q", "status", "sort", "frames", "tx", "idh", "grade"]) els[k].addEventListener(k === "q" ? "input" : "change", () => this.apply());
  }

  setPatients(list) {
    this.all = list;
    const sel = this.els.status;
    const seen = [...new Set(list.map((p) => p.status).filter(Boolean))];
    sel.innerHTML = `<option value="">Any</option>` + seen.map((s) => `<option value="${esc(s)}">${esc(statusInfo(s).short)}</option>`).join("");
    // IDH and WHO grade filters appear only when some patient has the field
    const fill = (select, wrap, values, label) => {
      wrap.hidden = values.length === 0;
      const hasMissing = list.some((p) => values.length && label(p) === null);
      select.innerHTML = `<option value="">Any</option>` + values.map((v) => `<option value="${esc(String(v))}">${esc(String(v))}</option>`).join("")
        + (hasMissing ? `<option value="__na">${NA}</option>` : "");
    };
    const idhs = [...new Set(list.map((p) => p.idh).filter(Boolean))].sort();
    const grades = [...new Set(list.map((p) => p.whoGrade).filter(isNum))].sort((a, b) => a - b);
    fill(this.els.idh, this.els.idhWrap, idhs, (p) => p.idh);
    fill(this.els.grade, this.els.gradeWrap, grades, (p) => (isNum(p.whoGrade) ? p.whoGrade : null));
    this.apply();
  }

  apply() {
    const { q, status, sort, frames, tx, idh, grade } = this.els;
    const needle = q.value.trim().toLowerCase();
    let r = this.all.filter((p) =>
      (!needle || p.id.toLowerCase().includes(needle) || p.uid.toLowerCase().includes(needle))
      && (!status.value || p.status === status.value)
      && (!frames.checked || p.hasFrames)
      && (!tx.checked || p.hasTreatment)
      && (!idh.value || (idh.value === "__na" ? !p.idh : p.idh === idh.value))
      && (!grade.value || (grade.value === "__na" ? !isNum(p.whoGrade) : String(p.whoGrade) === grade.value)));
    const by = sort.value;
    const num = (v, dir) => (isNum(v) ? v * dir : Infinity);
    r.sort((a, b) => {
      if (by === "last") return num(a.lastBurden, -1) - num(b.lastBurden, -1);
      if (by === "growth") return num(a.forecastChangePct, -1) - num(b.forecastChangePct, -1);
      if (by === "scans") return b.observed.length - a.observed.length;
      if (by === "age") return num(a.age, 1) - num(b.age, 1);
      if (by === "span") return b.lastScan - a.lastScan;
      return a.id.localeCompare(b.id, undefined, { numeric: true });
    });
    this.rows = r;
    this.els.count.textContent = `${r.length} of ${this.all.length} patients${this.all.some((p) => !p.hasFrames) ? ` · ${this.all.filter((p) => p.hasFrames).length} with 3D frames` : ""}`;
    this.inner.style.height = `${r.length * ROW}px`;
    this.els.list.style.setProperty("--n", String(Math.max(1, r.length))); // short lists do not leave an empty box
    this.cursor = Math.max(0, r.findIndex((p) => p.uid === this.selected));
    this.paint();
  }

  select(uid, scroll = true) {
    this.selected = uid;
    const i = this.rows.findIndex((p) => p.uid === uid);
    if (i >= 0) { this.cursor = i; if (scroll) this.reveal(i); }
    this.paint();
  }

  choose(p) { if (p) this.onSelect(p); }

  reveal(i) {
    const el = this.els.list;
    const top = i * ROW, bot = top + ROW;
    if (top < el.scrollTop) el.scrollTop = top;
    else if (bot > el.scrollTop + el.clientHeight) el.scrollTop = bot - el.clientHeight;
  }

  onKey(e) {
    const n = this.rows.length;
    if (!n) return;
    const page = Math.max(1, Math.floor(this.els.list.clientHeight / ROW) - 1);
    let c = this.cursor;
    if (e.key === "ArrowDown") c++;
    else if (e.key === "ArrowUp") c--;
    else if (e.key === "PageDown") c += page;
    else if (e.key === "PageUp") c -= page;
    else if (e.key === "Home") c = 0;
    else if (e.key === "End") c = n - 1;
    else if (e.key === "Enter" || e.key === " ") { this.choose(this.rows[this.cursor]); e.preventDefault(); return; }
    else return;
    e.preventDefault();
    this.cursor = Math.min(n - 1, Math.max(0, c));
    this.reveal(this.cursor);
    this.paint();
  }

  paint() {
    const el = this.els.list;
    const n = this.rows.length;
    const first = Math.max(0, Math.floor(el.scrollTop / ROW) - OVERSCAN);
    const last = Math.min(n, Math.ceil((el.scrollTop + (el.clientHeight || 400)) / ROW) + OVERSCAN);
    let h = "";
    for (let i = first; i < last; i++) {
      const p = this.rows[i];
      const st = statusInfo(p.status);
      const span = isNum(p.lastScan) ? `${Math.round(p.lastScan)} d` : NA;
      const sel = p.uid === this.selected;
      const cur = i === this.cursor && document.activeElement === el;
      h += `<div class="prow${cur ? " is-active-desc" : ""}" role="option" id="opt-${esc(p.id)}" data-i="${i}" aria-selected="${sel}" style="top:${i * ROW}px">`
        + `<span><span class="pid">${esc(p.id)}</span><span class="pmeta"><span class="${st.cls}"><span class="status-mark"></span>${esc(st.short)}</span> · ${p.observed.length} scans · ${span}${p.idh ? ` · IDH ${esc(p.idh)}` : ""}${p.hasFrames ? "" : " · no 3D"}</span></span>`
        + `${spark(p)}</div>`;
    }
    this.inner.innerHTML = h;
    const cp = this.rows[this.cursor];
    if (cp) el.setAttribute("aria-activedescendant", `opt-${cp.id}`); else el.removeAttribute("aria-activedescendant");
  }
}
