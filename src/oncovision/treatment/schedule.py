"""Treatment schedules: events on a day axis and the exposure curves derived from them.

Time is days relative to the patient's first scan (day 0).  Events may start before
day 0 (treatment given before the first scan), which matters because the effect of
a course persists for a while after it ends.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .agents import AGENTS


@dataclass(frozen=True)
class DrugCourse:
    """One continuous or cycled dosing course of a non-radiation agent."""
    agent: str
    start_day: float
    end_day: float
    cycle_days: float | None = None    # q days; None = continuous between start and end
    on_days: float | None = None       # dosing days at the start of each cycle (default: whole cycle)
    intensity: float = 1.0             # 0..1, dose relative to the planned dose

    def __post_init__(self):
        if self.agent not in AGENTS or AGENTS[self.agent].kind == "radiation":
            raise ValueError(f"unknown drug agent {self.agent!r}")
        if self.end_day < self.start_day:
            raise ValueError("end_day before start_day")


@dataclass(frozen=True)
class RadiationCourse:
    start_day: float
    end_day: float
    total_dose_gy: float
    fractions: int = 30

    def fraction_days(self) -> np.ndarray:
        """Treatment days: fractions spread over weekdays (5 of every 7 days)."""
        n = max(int(self.fractions), 1)
        span = max(self.end_day - self.start_day, 0.0)
        if n == 1 or span == 0:
            return np.full(n, self.start_day, dtype=float)
        # n fractions at weekday spacing: day index k -> start + 7*(k//5) + k%5
        k = np.arange(n)
        d = self.start_day + 7.0 * (k // 5) + (k % 5)
        # rescale if the recorded span disagrees with 5-on/2-off spacing
        nat = d[-1] - d[0]
        if nat > 0:
            d = self.start_day + (d - d[0]) * (span / nat)
        return d

    @property
    def dose_per_fraction(self) -> float:
        return self.total_dose_gy / max(int(self.fractions), 1)


@dataclass
class Schedule:
    drugs: list[DrugCourse] = field(default_factory=list)
    radiation: list[RadiationCourse] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.drugs and not self.radiation

    # ---- exposure ---------------------------------------------------------
    def drug_on_curve(self, agent: str, days: np.ndarray) -> np.ndarray:
        """Dosing indicator * intensity on a daily grid (1 day resolution)."""
        out = np.zeros(len(days), dtype=np.float64)
        for c in self.drugs:
            if c.agent != agent:
                continue
            if c.cycle_days and c.cycle_days > 0:
                on = c.on_days if c.on_days else c.cycle_days
                rel = (days - c.start_day)
                inside = (days >= c.start_day) & (days <= c.end_day)
                phase = np.mod(rel, c.cycle_days)
                out = np.maximum(out, np.where(inside & (phase < on), c.intensity, 0.0))
            else:
                inside = (days >= c.start_day) & (days <= c.end_day)
                out = np.maximum(out, np.where(inside, c.intensity, 0.0))
        return out

    def exposure(self, agent: str, days: np.ndarray) -> np.ndarray:
        """Effective exposure in [0, 1]: dosing curve convolved with exponential
        pharmacodynamic persistence (``pd_halflife_days``)."""
        days = np.asarray(days, dtype=np.float64)
        spec = AGENTS[agent]
        t0 = min([c.start_day for c in self.drugs if c.agent == agent] + [days.min()])
        grid = np.arange(np.floor(t0), np.ceil(days.max()) + 1.0)
        on = self.drug_on_curve(agent, grid)
        if spec.pd_halflife_days > 0:
            a = 0.5 ** (1.0 / spec.pd_halflife_days)       # per-day retention
            e = np.empty_like(on)
            acc = 0.0
            for i, v in enumerate(on):
                acc = max(v, acc * a)                       # persistence, capped at 1
                e[i] = acc
        else:
            e = on
        return np.interp(days, grid, e)

    def fraction_events(self) -> list[tuple[float, float]]:
        """(day, dose_Gy) for every radiation fraction."""
        out: list[tuple[float, float]] = []
        for r in self.radiation:
            dpf = r.dose_per_fraction
            out.extend((float(d), dpf) for d in r.fraction_days())
        return sorted(out)

    def radiation_dose_between(self, t0: float, t1: float) -> float:
        return float(sum(d for day, d in self.fraction_events() if t0 <= day < t1))

    def exposure_days_between(self, agent: str, t0: float, t1: float) -> float:
        """Integral of effective exposure over [t0, t1) in days."""
        days = np.arange(np.floor(t0), np.ceil(t1) + 1.0)
        e = self.exposure(agent, days)
        sel = (days >= t0) & (days < t1)
        return float(e[sel].sum())

    def agents_present(self) -> list[str]:
        out = sorted({c.agent for c in self.drugs})
        if self.radiation:
            out.append("radiation")
        return out

    # ---- serialisation ----------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "drugs": [vars(c) for c in self.drugs],
            "radiation": [vars(r) for r in self.radiation],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Schedule":
        return cls([DrugCourse(**x) for x in d.get("drugs", [])],
                   [RadiationCourse(**x) for x in d.get("radiation", [])])


def step_arrays(schedule: Schedule, n_steps: int, dt: float, effects: dict, t_start: float = 0.0):
    """Per-step solver inputs for one patient.

    ``effects`` maps agent -> {"kill": per-day, "suppress": fraction} and may hold
    ``"radiation": {"k_per_gy": ...}``.

    Returns (kill[T], growth_scale[T], survival[T]) for steps 0..T-1.  Step i spans
    [t_start + i*dt, t_start + (i+1)*dt).
    """
    t = t_start + (np.arange(n_steps) + 0.5) * dt
    kill = np.zeros(n_steps)
    growth = np.ones(n_steps)
    for agent in {c.agent for c in schedule.drugs}:
        eff = effects.get(agent)
        if not eff:
            continue
        e = schedule.exposure(agent, t)
        kill += float(eff.get("kill", 0.0)) * e
        growth *= 1.0 - np.clip(float(eff.get("suppress", 0.0)) * e, 0.0, 0.95)
    surv = np.ones(n_steps)
    rt = effects.get("radiation")
    if rt and schedule.radiation:
        k = float(rt.get("k_per_gy", 0.0))
        for day, dose in schedule.fraction_events():
            i = int(np.floor((day - t_start) / dt))
            if 0 <= i < n_steps:
                surv[i] *= np.exp(-k * dose)
    return kill, growth, surv
