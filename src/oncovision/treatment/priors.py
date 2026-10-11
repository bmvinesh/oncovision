"""Literature-derived treatment effect priors for the what-if simulation.

Why priors: in the retrospective cohorts (see ``fit.py`` and
``docs/runbooks/treatment-effects.md``) on-treatment intervals do not show slower
growth than off-treatment ones, because therapy is started *because* the tumour is
progressing.  Interval volume changes therefore cannot identify drug effects, and
the what-if uses effect sizes taken from randomised trials instead.

Mapping used (an assumption, stated in the UI): if a tumour grows exponentially
until it crosses a fixed progression threshold, time to progression scales with the
inverse of the growth rate.  A trial median-PFS ratio R (treated / control) then
corresponds to a growth-rate suppression ``s = 1 - 1/R`` while the agent is active.

``verified`` records whether the figure was confirmed against a source during this
project; unverified numbers must be re-checked against the paper before external
use.
"""
from __future__ import annotations

PRIORS: dict[str, dict] = {
    "temozolomide": {
        "suppress": 1 - 5.0 / 6.9,
        "evidence": "Stupp et al., NEJM 2005 (EORTC 26981/NCIC CE.3): RT+TMZ vs RT alone; median PFS 6.9 vs 5.0 "
                    "months (overall survival HR 0.63 confirmed)",
        "verified": False,
    },
    "ttfields": {
        "suppress": 1 - 4.0 / 6.7,
        "evidence": "Stupp et al., JAMA 2017 (EF-14): TTFields + TMZ vs TMZ; median PFS 6.7 vs 4.0 months (HR 0.63)",
        "verified": True,
    },
    "bevacizumab": {
        "suppress": 1 - 6.2 / 10.6,
        "evidence": "Chinot et al., NEJM 2014 (AVAglio): median PFS 10.6 vs 6.2 months (HR 0.64); no overall-survival "
                    "benefit (16.8 vs 16.7 months). Part of the PFS gain is imaging pseudo-response.",
        "verified": True,
    },
    "lomustine_pcv": {"suppress": 0.0, "evidence": "no trial-derived figure used; effect not modelled", "verified": False},
    "immunotherapy": {"suppress": 0.0, "evidence": "no benefit shown in randomised glioblastoma trials; not modelled",
                      "verified": False},
}

# Radiation: no PFS-ratio prior is used.  The default log-kill per Gy is a placeholder chosen so that a
# 60 Gy course removes about half of the tumour burden before regrowth; it is labelled illustrative.
RT_K_PER_GY = 0.0115
RT_EVIDENCE = "illustrative placeholder (60 Gy ~ 50% burden reduction); not a measured effect"


def prior_effects() -> dict:
    """Effects dict for ``oncovision.treatment.schedule.step_arrays``."""
    out = {a: {"kill": 0.0, "suppress": float(p["suppress"])} for a, p in PRIORS.items()}
    out["radiation"] = {"k_per_gy": RT_K_PER_GY}
    return out


def no_effects() -> dict:
    return {}
