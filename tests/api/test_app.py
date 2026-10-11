import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from oncovision.api.app import create_app  # noqa: E402
from oncovision.llm import debate as D  # noqa: E402

CASE = {"uid": "mu_glioma_post:PatientID_0034", "status": "ok", "rho": 0.0129, "clinical": {"age_years": 53},
        "observed": [{"day": 0, "seed_burden_mm3": 1416}, {"day": 527, "seed_burden_mm3": 4168}],
        "scenarios": {"no_further_treatment": {"day": [527, 707], "burden_mm3": [4168, 5000]}}}


class FakeModel:
    def __init__(self):
        self.calls = 0

    def __call__(self, system, user, model=None, timeout=None, temperature=None):
        self.calls += 1
        if system is D.SKEPTIC_SYSTEM:
            n = user.count("\n") and len([l for l in user.split("SENTENCES:")[1].splitlines() if l[:1].isdigit()])
            return json.dumps({"audit": [{"i": i, "problem": "none"} for i in range(1, n + 1)]})
        if system is D.REVISE_SYSTEM:
            return json.dumps({"revised": []})
        return json.dumps({"sentences": [
            {"text": "Patient 0034 had 2 scans over 527 days.", "cites": ["c1"]},
            {"text": "The modelled burden was 1416 mm3 at the first scan and 4168 mm3 at the last.", "cites": ["c2"]},
            {"text": "The growth rate was fitted to this patient's own scans at 0.013 per day.", "cites": ["c3"]},
            {"text": "With no further treatment the burden after 180 days is higher than at the last scan.", "cites": ["c4"]}]})


@pytest.fixture()
def client(tmp_path):
    p = tmp_path / "cases.json"
    p.write_text(json.dumps({"effects": {"values": {}}, "cases": [CASE]}))
    fake = FakeModel()
    app = create_app(cases_path=str(p), cache_dir=str(tmp_path / "cache"), web_dir=str(tmp_path / "none"), caller=fake)
    c = TestClient(app)
    c.fake = fake
    return c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["cases"] == 1 and r.json()["explanations"]["judge"] == "rule-based"


def test_narrative_runs_then_is_cached_and_rerun_runs_again(client):
    r = client.post("/api/narrative", json={"uid": "PatientID_0034"})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "llm" and body["verification"] == "debate-verified"
    assert body["text"].endswith("not medical advice.")
    n = client.fake.calls
    assert client.post("/api/narrative", json={"uid": CASE["uid"]}).json()["cached"] is True
    assert client.fake.calls == n
    again = client.post("/api/narrative", json={"uid": CASE["uid"], "rerun": True}).json()
    assert client.fake.calls > n and not again.get("cached")


def test_unknown_patient_and_bad_kind(client):
    assert client.post("/api/narrative", json={"uid": "nope"}).status_code == 404
    assert client.post("/api/narrative", json={"uid": "x", "kind": "weird"}).status_code == 422
    assert client.post("/api/narrative", json={"kind": "results"}).status_code == 404


def test_stored_narrative_is_returned_without_calling_the_model(tmp_path):
    case = dict(CASE, narrative={"text": "Stored.", "source": "llm", "verification": "debate-verified"})
    p = tmp_path / "cases.json"
    p.write_text(json.dumps({"cases": [case]}))
    fake = FakeModel()
    c = TestClient(create_app(cases_path=str(p), cache_dir=str(tmp_path / "c"), web_dir=str(tmp_path / "x"), caller=fake))
    assert c.post("/api/narrative", json={"uid": CASE["uid"]}).json()["text"] == "Stored."
    assert fake.calls == 0
