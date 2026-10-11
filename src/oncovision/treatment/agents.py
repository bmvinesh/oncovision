"""Treatment agents and their model parameters.

Two kinds of effect enter the growth PDE (see ``oncovision.sim.solver``):

* ``kill``   : cytotoxic rate (per day at full exposure).  du/dt -= kill * c(t) * u
* ``suppress``: fractional reduction of the proliferation rate rho at full exposure.

Radiation is applied as an instantaneous survival factor on each fraction day,
``exp(-k_rt * dose_Gy)`` (log-linear in dose; the quadratic term is absorbed in k_rt).

The numbers in ``PRIORS`` are weak priors used only to regularise the fit in
``oncovision.treatment.fit``.  They are not measured drug effects.  Whatever the
viewer shows should come from ``treatment_effects.json`` written by the fit, and
must be labelled as model-fitted association, not a clinical prediction.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentSpec:
    key: str
    label: str
    kind: str                 # "cytotoxic" | "antiangiogenic" | "device" | "immuno" | "radiation"
    pd_halflife_days: float   # how long the effect persists after a dosing window ends
    prior_kill: float         # per day at exposure 1.0
    prior_suppress: float     # fractional reduction of rho at exposure 1.0
    imaging_only_note: str = ""


AGENTS: dict[str, AgentSpec] = {
    "temozolomide": AgentSpec("temozolomide", "Temozolomide (TMZ)", "cytotoxic", 3.0, 0.004, 0.10),
    "lomustine_pcv": AgentSpec("lomustine_pcv", "Lomustine / PCV", "cytotoxic", 7.0, 0.004, 0.10),
    "bevacizumab": AgentSpec(
        "bevacizumab", "Bevacizumab (Avastin)", "antiangiogenic", 14.0, 0.001, 0.15,
        "Reduces enhancement and oedema on MRI; part of the imaging response is not cell kill.",
    ),
    "ttfields": AgentSpec("ttfields", "Tumor Treating Fields (Optune)", "device", 3.0, 0.002, 0.10),
    "immunotherapy": AgentSpec("immunotherapy", "Checkpoint immunotherapy", "immuno", 21.0, 0.001, 0.05),
    "radiation": AgentSpec("radiation", "Radiation therapy", "radiation", 0.0, 0.0, 0.0),
}

# Radiation: log-kill per Gy delivered.  Prior only.
RT_PRIOR_K_PER_GY = 0.01

DRUG_KEYS = [k for k, a in AGENTS.items() if a.kind != "radiation"]
ALL_KEYS = list(AGENTS)
