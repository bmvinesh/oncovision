import numpy as np
import pytest

from oncovision.sim.solver import solve_numpy, solve_torch, simulate_patient, n_steps_for
from oncovision.treatment.schedule import DrugCourse, RadiationCourse, Schedule

torch = pytest.importorskip("torch")
DEVICES = ["cpu"] + (["cuda"] if torch.cuda.is_available() else [])


def blob(shape=(24, 24, 24), r=4.0, peak=0.8, shift=(0, 0, 0)):
    z, y, x = np.indices(shape).astype(np.float32)
    c = [s / 2 + sh for s, sh in zip(shape, shift)]
    d2 = (z - c[0]) ** 2 + (y - c[1]) ** 2 + (x - c[2]) ** 2
    return (peak * np.exp(-d2 / (2 * r * r))).astype(np.float32)


@pytest.mark.parametrize("device", DEVICES)
def test_torch_matches_numpy(device):
    u0 = np.stack([blob(), blob(r=3.0, shift=(2, 0, -1))])
    kw = dict(rho=np.array([0.05, 0.03]), D=np.array([0.1, 0.15]), dx=1.0, n_steps=50, dt=1.0,
              kill=np.full((2, 50), 0.002), growth=np.full((2, 50), 0.9), n_frames=3)
    a = solve_numpy(u0, **kw)
    b = solve_torch(u0, device=device, **kw)
    assert np.abs(a.frames - b.frames).max() < 1e-4
    assert np.allclose(a.burden, b.burden, rtol=1e-4)


def test_batch_equals_singles():
    u0 = np.stack([blob(), blob(r=3.0)])
    kw = dict(n_steps=20, dt=1.0)
    both = solve_torch(u0, rho=[0.05, 0.08], D=[0.1, 0.1], **kw)
    one = solve_torch(u0[1:], rho=[0.08], D=[0.1], **kw)
    assert np.allclose(both.burden[1], one.burden[0], rtol=1e-5)


def test_zero_diffusion_is_logistic():
    u0 = np.full((4, 4, 4), 0.1, np.float32)
    rho, T, dt = 0.05, 100, 0.1
    res = solve_numpy(u0, rho=rho, D=0.0, n_steps=T, dt=dt)
    t = T * dt
    exact = 1.0 / (1.0 + (1 / 0.1 - 1) * np.exp(-rho * t))
    got = res.burden[0, -1] / u0.size
    assert abs(got - exact) < 5e-3


def test_density_stays_in_unit_interval():
    u0 = blob(peak=1.0)
    res = solve_numpy(u0[None], rho=0.5, D=0.15, n_steps=40, dt=1.0, n_frames=2)
    assert res.frames.min() >= 0.0 and res.frames.max() <= 1.0


def test_cfl_violation_raises():
    with pytest.raises(ValueError):
        solve_numpy(blob()[None], rho=0.1, D=1.0, n_steps=2, dt=1.0)


def test_kill_and_radiation_reduce_burden():
    u0 = blob(peak=0.9)
    base = simulate_patient(u0, rho=0.03, D=0.1, dx=1.0, days=60, n_frames=0)
    sched = Schedule(drugs=[DrugCourse("temozolomide", 0, 40)],
                     radiation=[RadiationCourse(0, 40, 60.0, 30)])
    eff = {"temozolomide": {"kill": 0.02, "suppress": 0.2}, "radiation": {"k_per_gy": 0.03}}
    tr = simulate_patient(u0, rho=0.03, D=0.1, dx=1.0, days=60, schedule=sched, effects=eff, n_frames=0)
    assert tr["burden_mm3"][-1] < 0.5 * base["burden_mm3"][-1]
    assert base["burden_mm3"][-1] > base["burden_mm3"][0]


def test_n_steps_respects_cfl():
    n, dt = n_steps_for(100, D=0.5, dx=1.0)
    assert dt <= 1.0 / (6 * 0.5) and abs(n * dt - 100) < 1e-9
