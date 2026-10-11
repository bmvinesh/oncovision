import numpy as np

from oncovision.treatment.schedule import DrugCourse, RadiationCourse, Schedule, step_arrays


def test_cycled_course_pattern():
    s = Schedule([DrugCourse("temozolomide", 0, 83, cycle_days=28, on_days=5)])
    on = s.drug_on_curve("temozolomide", np.arange(0, 84))
    assert on[:5].all() and not on[5:28].any() and on[28:33].all()


def test_exposure_persists_then_decays():
    s = Schedule([DrugCourse("bevacizumab", 0, 10)])
    e = s.exposure("bevacizumab", np.array([5.0, 10.0, 24.0, 38.0]))
    assert e[0] == 1.0 and e[1] == 1.0
    assert 0.45 < e[2] < 0.55           # one pd half-life (14 d) after the end
    assert e[3] < 0.3


def test_pre_baseline_courses_still_act_after_day_zero():
    s = Schedule([DrugCourse("temozolomide", -30, -2)])
    assert s.exposure("temozolomide", np.array([0.0]))[0] > 0.3


def test_radiation_fractions_and_dose():
    r = RadiationCourse(0, 40, 60.0, 30)
    s = Schedule(radiation=[r])
    ev = s.fraction_events()
    assert len(ev) == 30 and abs(sum(d for _, d in ev) - 60.0) < 1e-9
    assert ev[0][0] == 0 and abs(ev[-1][0] - 40) < 1e-6
    assert abs(s.radiation_dose_between(0, 20) - 2.0 * sum(1 for d, _ in ev if d < 20)) < 1e-9


def test_step_arrays_apply_effects():
    s = Schedule([DrugCourse("temozolomide", 0, 10)], [RadiationCourse(0, 4, 10.0, 5)])
    eff = {"temozolomide": {"kill": 0.1, "suppress": 0.5}, "radiation": {"k_per_gy": 0.1}}
    kill, growth, surv = step_arrays(s, 20, 1.0, eff)
    assert kill[0] == 0.1 and growth[0] == 0.5 and kill[19] < kill[0]
    assert (surv[:5] < 1).all() and (surv[5:] == 1).all()


def test_roundtrip_dict():
    s = Schedule([DrugCourse("ttfields", 3, 90, cycle_days=None)], [RadiationCourse(0, 40, 60, 30)])
    assert Schedule.from_dict(s.to_dict()).to_dict() == s.to_dict()
