"""Counterfactual treatment scenarios built by editing a patient's recorded schedule."""
from __future__ import annotations

from .schedule import DrugCourse, RadiationCourse, Schedule


def copy(s: Schedule) -> Schedule:
    return Schedule.from_dict(s.to_dict())


def untreated() -> Schedule:
    return Schedule()


def adjuvant_tmz(start: float, cycles: int = 6) -> DrugCourse:
    """Standard adjuvant temozolomide: 5 days of every 28."""
    return DrugCourse("temozolomide", start, start + cycles * 28.0, cycle_days=28.0, on_days=5.0)


def with_course(s: Schedule, course: DrugCourse) -> Schedule:
    out = copy(s)
    out.drugs.append(course)
    return out


def without_agent(s: Schedule, agent: str) -> Schedule:
    out = copy(s)
    out.drugs = [c for c in out.drugs if c.agent != agent]
    return out


def future_only(s: Schedule, t_from: float) -> Schedule:
    """Keep only what happens after ``t_from`` (used to ask: what if nothing more is given?)."""
    out = Schedule()
    out.drugs = [c for c in s.drugs if c.end_day >= t_from]
    out.radiation = [r for r in s.radiation if r.end_day >= t_from]
    return out


def scenario_set(recorded: Schedule, t_from: float, horizon: float) -> dict[str, Schedule]:
    """Named scenarios for the forward window starting at ``t_from`` (the last observed scan).

    Past exposure is kept in every scenario (it still acts through persistence); only what is
    added or removed from ``t_from`` onwards differs.
    """
    base_past = Schedule([c for c in recorded.drugs if c.start_day < t_from],
                         [r for r in recorded.radiation if r.start_day < t_from])
    # truncate courses that run past t_from so "stop now" is a real counterfactual
    stop = Schedule([DrugCourse(c.agent, c.start_day, min(c.end_day, t_from), c.cycle_days, c.on_days, c.intensity)
                     for c in base_past.drugs], list(base_past.radiation))
    out = {
        "no_further_treatment": stop,
        "as_recorded": copy(recorded),
        "tmz_6_cycles": with_course(stop, adjuvant_tmz(t_from, 6)),
        "tmz_plus_ttfields": with_course(with_course(stop, adjuvant_tmz(t_from, 6)),
                                         DrugCourse("ttfields", t_from, t_from + horizon)),
        "bevacizumab": with_course(stop, DrugCourse("bevacizumab", t_from, t_from + horizon, cycle_days=14.0)),
    }
    return out
