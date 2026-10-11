"""Clinical sheets -> per-patient scan days and treatment schedules.

Only treatment *recorded in the dataset's clinical sheet* is used.  Anything the
sheet does not say is either left out or, where a standard-of-care assumption is
needed, listed in ``PatientTreatment.assumptions`` so the UI and the model card can
state it.

MU-Glioma-Post gives start/end days from diagnosis for each course, so its
schedules are the ones the effect sizes are fitted on.  UCSD-PTGBM gives, for each
scan, days from that scan back to the first radiation, first chemo start and last
bevacizumab; end dates and doses are not recorded, so its schedules carry
assumptions and are used for validation only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .schedule import DrugCourse, RadiationCourse, Schedule

AVASTIN_RE = re.compile(r"avastin|bevacizumab", re.I)
TMZ_RE = re.compile(r"temozolomide|temodar|\btmz\b", re.I)
LOMUSTINE_RE = re.compile(r"lomustine|ccnu|\bpcv\b|procarbazine", re.I)
IMMUNO_RE = re.compile(r"keytruda|pembrolizumab|retifanlimab|nivolumab|opdivo", re.I)
TTF_RE = re.compile(r"optune|ttf|tumor treating", re.I)


@dataclass
class PatientTreatment:
    patient_id: str
    dataset: str
    scan_days: dict[int, float]           # timepoint index -> day relative to the first scan
    schedule: Schedule                    # days relative to the first scan
    assumptions: list[str] = field(default_factory=list)
    surgery_days: list[float] = field(default_factory=list)   # known surgeries, relative to first scan
    multiple_surgeries: bool | None = None
    extra: dict = field(default_factory=dict)


def _num(v):
    try:
        x = pd.to_numeric(v, errors="coerce")
    except Exception:
        return np.nan
    return float(x) if pd.notna(x) else np.nan


def _agent_for(name: str) -> str | None:
    if not isinstance(name, str):
        return None
    if AVASTIN_RE.search(name):
        return "bevacizumab"
    if TMZ_RE.search(name):
        return "temozolomide"
    if LOMUSTINE_RE.search(name):
        return "lomustine_pcv"
    if IMMUNO_RE.search(name):
        return "immunotherapy"
    if TTF_RE.search(name):
        return "ttfields"
    return None


def _dose_gy(v) -> float:
    m = re.search(r"([\d.]+)\s*gy", str(v), re.I)
    return float(m.group(1)) if m else np.nan


# --------------------------------------------------------------------------
# MU-Glioma-Post
# --------------------------------------------------------------------------
def load_mu_clinical(xlsx_path) -> pd.DataFrame:
    d = pd.read_excel(xlsx_path, sheet_name="MU Glioma Post")
    d.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in d.columns]
    return d


def _mu_course(row, name_col, start_col, end_col, cycle_col=None, n_cycles_col=None, on_days=None,
               assumptions=None, label=""):
    agent_name = row.get(name_col)
    start = _num(row.get(start_col))
    if not isinstance(agent_name, str) or np.isnan(start):
        return []
    end = _num(row.get(end_col)) if end_col else np.nan
    cycle = _num(row.get(cycle_col)) if cycle_col else np.nan
    n_cyc = _num(row.get(n_cycles_col)) if n_cycles_col else np.nan
    agents = []
    low = agent_name.lower()
    # "Temozolomide w/ Avastin", "Avastin w/ Temodar": both agents are given together
    for rx, key in ((TMZ_RE, "temozolomide"), (AVASTIN_RE, "bevacizumab"), (LOMUSTINE_RE, "lomustine_pcv"),
                    (IMMUNO_RE, "immunotherapy"), (TTF_RE, "ttfields")):
        if rx.search(low):
            agents.append(key)
    out = []
    for key in agents or []:
        s, e = start, end
        if np.isnan(e):
            if not np.isnan(n_cyc) and not np.isnan(cycle):
                e = s + n_cyc * cycle
            else:
                e = s + (cycle if not np.isnan(cycle) else 1.0)
                if assumptions is not None:
                    assumptions.append(f"{label} {key}: end day not recorded, assumed one cycle")
        c = None if np.isnan(cycle) else float(cycle)
        on = on_days if (key in ("temozolomide", "lomustine_pcv") and c) else None
        out.append(DrugCourse(key, float(s), float(e), cycle_days=c, on_days=on))
    return out


def mu_patient_treatments(df: pd.DataFrame) -> dict[str, PatientTreatment]:
    """One PatientTreatment per patient; days are relative to the first available scan."""
    tp_cols = {int(re.search(r"Timepoint_(\d+)", c).group(1)): c for c in df.columns if "MRI (Timepoint_" in c}
    out: dict[str, PatientTreatment] = {}
    for _, r in df.iterrows():
        pid = str(r["Patient_ID"])
        dx_days = {k: _num(r[c]) for k, c in tp_cols.items()}
        dx_days = {k: v for k, v in dx_days.items() if not np.isnan(v)}
        if not dx_days:
            continue
        t0 = min(dx_days.values())
        scan_days = {k: v - t0 for k, v in dx_days.items()}
        notes: list[str] = []
        courses: list[DrugCourse] = []

        # concomitant chemo (given with radiation: daily)
        courses += _mu_course(r, "Name of Initial Chemo Therapy",
                              "Number of days from Diagnosis to Initial Chemo Therapy Start date",
                              "Number of days from Diagnosis to Initial Chemo Therapy end date",
                              assumptions=notes, label="initial chemo")
        # adjuvant cycles (TMZ 5 days of every 28)
        courses += _mu_course(r, "Additional Therapy", "Number of Days from Diagnosis to Starting Additional Therapy",
                              "Number of Days from Diagnosis to Complete Additional Therapy",
                              "Cycle length of Additional Therapy (q days)", "Number of Cycles of Additional Therapy",
                              on_days=5.0, assumptions=notes, label="additional therapy")
        courses += _mu_course(r, "2nd_Additional Therapy",
                              "Number of Days from Diagnosis to Starting 2nd_Additional Therapy",
                              "Number of Days from Dagnosis to Complete 2nd_Additional Therapy",
                              "Cycle length of 2nd_Additional Therapy (q days)",
                              "Number of Cycles of 2nd_Additional Therapy", on_days=5.0,
                              assumptions=notes, label="2nd additional therapy")
        courses += _mu_course(r, "Immuno therapy", "Number of Days from Diagnosis to Start Immunotherapy",
                              "Number of Days from Diagnosis to Complete Immunotherapy",
                              "Cycle length of Immunotherapy (q days)", "Number of Cycles of Immunotherapy",
                              assumptions=notes, label="immunotherapy")
        # device / other: no cycle info; if no end date it is assumed to run until the last scan
        other_name = r.get("Other Types of Therapy (LITT, more chemo, proton therapy)")
        if isinstance(other_name, str) and TTF_RE.search(other_name):
            s = _num(r.get("Number of Days from Diagnosis to Start Other Additional Therapy"))
            e = _num(r.get("Number of Days from Diagnosis to Complete Other Additional Therapy"))
            if not np.isnan(s):
                if np.isnan(e):
                    e = max(dx_days.values())
                    notes.append("ttfields: end day not recorded, assumed to continue to the last scan")
                courses.append(DrugCourse("ttfields", float(s), float(max(e, s))))
        # radiation
        rad: list[RadiationCourse] = []
        if isinstance(r.get("Radiation Therapy"), str) and r["Radiation Therapy"].strip().lower() in ("yes", "proton therapy"):
            s = _num(r.get("Number of days from Diagnosis to Radiation Therapy Start date"))
            e = _num(r.get("Number of days from Diagnosis to Radiation Therapy end date"))
            dose = _dose_gy(r.get("Dose"))
            nfx = _num(r.get("Number of Fractions"))
            if not np.isnan(s) and not np.isnan(e):
                if np.isnan(dose):
                    dose = 60.0
                    notes.append("radiation: dose not recorded, assumed 60 Gy")
                if np.isnan(nfx):
                    nfx = 30.0
                    notes.append("radiation: fractions not recorded, assumed 30")
                rad.append(RadiationCourse(float(s), float(e), float(dose), int(nfx)))

        # shift to first-scan-relative days
        def sh(c):
            if isinstance(c, DrugCourse):
                return DrugCourse(c.agent, c.start_day - t0, c.end_day - t0, c.cycle_days, c.on_days, c.intensity)
            return RadiationCourse(c.start_day - t0, c.end_day - t0, c.total_dose_gy, c.fractions)

        surgery = []
        s1 = _num(r.get("Number of days from Diagnosis to First surgery or procedure"))
        if not np.isnan(s1):
            surgery.append(s1 - t0)
        ms = _num(r.get("Multiple surgeries"))
        out[pid] = PatientTreatment(
            pid, "mu_glioma_post", scan_days,
            Schedule([sh(c) for c in courses], [sh(c) for c in rad]),
            assumptions=notes, surgery_days=surgery,
            multiple_surgeries=None if np.isnan(ms) else bool(ms),
            extra={"dx_day_of_first_scan": t0},
        )
    return out


# --------------------------------------------------------------------------
# UCSD-PTGBM
# --------------------------------------------------------------------------
def load_ucsd_clinical(xlsx_path) -> pd.DataFrame:
    d = pd.read_excel(xlsx_path, sheet_name="Cinical info")
    d.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in d.columns]
    return d


UCSD_ID_RE = re.compile(r"UCSD-PTGBM-(\d+)_(\d+)")


def ucsd_patient_treatments(df: pd.DataFrame) -> dict[str, PatientTreatment]:
    """Schedules for validation.  Doses and end dates are assumptions, listed per patient."""
    col = lambda sub: next(c for c in df.columns if sub.lower() in c.lower())
    c_surg = col("initial surgery")
    c_rt1, c_rtl = col("first radiation"), col("last radiation prior")
    c_chemo_type, c_chemo_start = col("1st Chemo type"), col("1st chemo start")
    c_av, c_av_last = col("Avastin® (bevacizumab)"), col("last Avastin")
    c_rtn = col("Number of radiation courses")
    c_death = col("Date of death")
    c_follow = col("Date of last follow-up")
    rows: dict[str, list] = {}
    for _, r in df.iterrows():
        m = UCSD_ID_RE.match(str(r["ID"]))
        if not m:
            continue
        rows.setdefault(m.group(1), []).append((int(m.group(2)), r))
    out: dict[str, PatientTreatment] = {}
    for pid, lst in rows.items():
        lst.sort(key=lambda x: x[0])
        # Each column "days from acquisition to <fixed date>" gives acq_k - date = -value, so any such
        # column that is present for every scan of the patient anchors the scan spacing.  Use the first
        # one available (initial surgery, first radiation, chemo start, last follow-up, death).
        a = {}
        for anchor in (c_surg, c_rt1, c_chemo_start, c_follow, c_death):
            vals = {k: _num(r[anchor]) for k, r in lst}
            if all(not np.isnan(v) for v in vals.values()):
                a = {k: -v for k, v in vals.items()}
                break
        if not a:
            vals = {k: _num(r[c_surg]) for k, r in lst}
            a = {k: -v for k, v in vals.items() if not np.isnan(v)}
        if not a:
            continue
        base = min(a.values())
        scan_days = {k: v - base for k, v in a.items()}
        k0 = min(a, key=a.get)
        r0 = dict(lst)[k0]
        notes: list[str] = ["doses, cycle structure and end dates are not recorded in this dataset; standard-of-care assumed"]
        courses, rad = [], []
        # absolute (surgery-referenced) day = acq_k + (days from acq_k to event); events are given per scan, take the last scan's view for dates
        last_k, rl = lst[-1]
        acq_last = a.get(last_k, a[k0])
        off = lambda d: acq_last + d - base          # event day relative to first scan
        rt1 = _num(rl[c_rt1]); rtl = _num(rl[c_rtl])
        if str(rl[col("Radiation")]).strip().lower() == "yes" and not (np.isnan(rt1) and np.isnan(rtl)):
            # "first" and "last" radiation days are not always ordered; take the earlier as the start
            # and assume a standard 6-week course
            start = off(min(d for d in (rt1, rtl) if not np.isnan(d)))
            rad.append(RadiationCourse(start, start + 40.0, 60.0, 30))
            notes.append("radiation: start taken as the earliest recorded date; 60 Gy / 30 fractions over 6 weeks assumed")
        ch1 = _num(rl[c_chemo_start])
        if isinstance(rl[c_chemo_type], str) and not np.isnan(ch1):
            key = _agent_for(rl[c_chemo_type]) or "temozolomide"
            s = off(ch1)
            courses.append(DrugCourse(key, s, s + 42))                                    # concomitant
            courses.append(DrugCourse(key, s + 70, s + 70 + 6 * 28, cycle_days=28.0, on_days=5.0))  # 6 adjuvant cycles
        if str(rl[c_av]).strip().lower() == "yes":
            lastav = _num(rl[c_av_last])
            if not np.isnan(lastav):
                e = off(lastav)
                courses.append(DrugCourse("bevacizumab", e - 14.0, e, cycle_days=14.0))
                notes.append("bevacizumab: only the last dose before the scan is dated; one cycle assumed")
        dead = _num(rl[c_death])
        out[pid] = PatientTreatment(pid, "ucsd_ptgbm", scan_days, Schedule(courses, rad), assumptions=notes,
                                    extra={"ucsd_ids": {k: str(r["ID"]) for k, r in lst},
                                           "days_to_death_from_last_scan": None if np.isnan(dead) else dead})
    return out
