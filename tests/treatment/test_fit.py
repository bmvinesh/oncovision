import numpy as np
import pandas as pd

from oncovision.treatment.fit import build_intervals, fit_effects, cv_score, FLOOR_MM3
from oncovision.treatment.schedule import DrugCourse, RadiationCourse, Schedule

TRUE = {"temozolomide": 0.006, "bevacizumab": 0.004, "ttfields": 0.0}
TRUE_GY = 0.004


def synth(n=500, seed=1, noise=0.2):
    rng = np.random.default_rng(seed)
    tp_rows, sched = [], {}
    for i in range(n):
        uid = f"s:{i}"
        r = rng.normal(0.012, 0.006)
        s = Schedule()
        if rng.random() < 0.7:
            s.drugs.append(DrugCourse("temozolomide", rng.uniform(-30, 20), rng.uniform(60, 200), cycle_days=28, on_days=5))
        if rng.random() < 0.4:
            s.drugs.append(DrugCourse("bevacizumab", rng.uniform(0, 100), rng.uniform(150, 300), cycle_days=14))
        if rng.random() < 0.4:
            s.drugs.append(DrugCourse("ttfields", rng.uniform(0, 60), rng.uniform(150, 300)))
        if rng.random() < 0.6:
            a = rng.uniform(-20, 30)
            s.radiation.append(RadiationCourse(a, a + 40, 60.0, 30))
        sched[uid] = {"schedule": s.to_dict(), "surgery_days": []}
        days = np.cumsum(np.concatenate([[0], rng.uniform(30, 90, 3)]))
        v = 20000.0
        for k, d in enumerate(days):
            tp_rows.append({"patient_uid": uid, "tp_index": k, "day": d, "core_mm3": v, "rc_mm3": 0.0})
            if k + 1 < len(days):
                t0, t1 = d, days[k + 1]
                y = r * (t1 - t0)
                y -= sum(TRUE[a] * s.exposure_days_between(a, t0, t1) for a in TRUE)
                y -= TRUE_GY * s.radiation_dose_between(t0, t1)
                v = (v + FLOOR_MM3) * np.exp(y + rng.normal(0, noise)) - FLOOR_MM3
                v = max(v, 0.0)
    return pd.DataFrame(tp_rows), sched


def test_recovers_known_effects():
    tp, sched = synth()
    iv = build_intervals(tp, sched)
    fit = fit_effects(iv)
    assert abs(fit.kill["temozolomide"] - TRUE["temozolomide"]) < 0.0025
    assert abs(fit.kill["bevacizumab"] - TRUE["bevacizumab"]) < 0.0025
    assert fit.kill["ttfields"] < 0.003            # true effect is zero
    assert abs(fit.k_per_gy - TRUE_GY) < 0.0015


def test_cv_beats_no_treatment_baseline_when_effects_exist():
    tp, sched = synth(seed=2)
    cv = cv_score(build_intervals(tp, sched))
    assert cv["relative_improvement"] > 0.0


def test_surgery_intervals_are_excluded():
    tp, sched = synth(n=20, seed=3)
    sched["s:0"]["surgery_days"] = [10.0]
    iv = build_intervals(tp, sched)
    assert (iv[iv.patient_uid == "s:0"].qc.iloc[0]) == "surgery_in_interval"
