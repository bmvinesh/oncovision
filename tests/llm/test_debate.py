import json

from oncovision.llm import debate as D
from oncovision.llm import narrative as N

LEDGER = D.with_ids([
    N._claim("Patient 0034 had 6 scans over 527 days."),
    N._claim("With no further treatment, the modelled burden after 180 days is 12.4% higher than at the last scan.",
             "12.4", must=N.UP, must_not=N.DOWN),
    N._claim("With six cycles of temozolomide, the modelled burden after 180 days is 3.1% lower than at the last scan.",
             "3.1", must=N.DOWN, must_not=N.UP),
    N._claim("The treatment effects are growth suppression while the drug is active, taken from published trials."),
    N._claim("This is an illustrative research simulation, not a prediction for this patient and not medical advice."),
])

GOOD = [
    {"text": "Patient 0034 had 6 scans over 527 days.", "cites": ["c1"]},
    {"text": "With no further treatment the modelled burden is 12.4% higher after 180 days.", "cites": ["c2"]},
    {"text": "With six cycles of temozolomide it is 3.1% lower after 180 days.", "cites": ["c3"]},
    {"text": "The drug effects are growth suppression taken from published trials.", "cites": ["c4"]},
]


class Script:
    """Fake model: replies per role from queues, records the calls."""

    def __init__(self, advocate=(), skeptic=(), revise=()):
        self.q = {"advocate": list(advocate), "skeptic": list(skeptic), "revise": list(revise)}
        self.calls = []

    def __call__(self, system, user, model=None, timeout=None, temperature=None):
        role = "revise" if system is D.REVISE_SYSTEM else "skeptic" if system is D.SKEPTIC_SYSTEM else "advocate"
        self.calls.append(role)
        item = self.q[role].pop(0)
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, str) else json.dumps(item)


def audit(n, bad=None):
    """Skeptic reply with one entry per sentence; ``bad`` maps index -> (problem, quote, reason)."""
    bad = bad or {}
    return {"audit": [{"i": i + 1, "problem": bad[i][0] if i in bad else "none", "quote": bad[i][1] if i in bad else "",
                       "reason": bad[i][2] if i in bad else ""} for i in range(n)]}


def sup(n):
    return audit(n)


def run(script, **kw):
    return D.debate(LEDGER, "case", model="m", caller=script, **kw)


def test_clean_draft_passes_and_safety_line_is_appended_verbatim():
    s = Script(advocate=[{"sentences": GOOD}], skeptic=[sup(4)])
    r = run(s)
    assert r["source"] == "llm" and r["verification"] == "debate-verified"
    assert r["text"].endswith(LEDGER[-1]["text"])
    assert s.calls == ["advocate", "skeptic"]
    assert r["trace"]["agents"]["judge"]["type"] == "rule-based"


def test_wrong_direction_is_sent_back_and_fixed():
    bad = [dict(GOOD[0]), {"text": "With no further treatment the burden is 12.4% lower after 180 days.", "cites": ["c2"]},
           GOOD[2], GOOD[3]]
    fixed = {"revised": [{"i": 2, "text": GOOD[1]["text"], "cites": ["c2"]}]}
    s = Script(advocate=[{"sentences": bad}], skeptic=[sup(4), sup(4)], revise=[fixed])
    r = run(s)
    assert r["verification"] == "debate-verified"
    assert "12.4% higher" in r["text"] and "12.4% lower" not in r["text"]
    assert r["trace"]["rounds"][0]["flagged"] == [1] and r["trace"]["rounds"][0]["revised"] == [1]


def test_unfixed_sentence_is_replaced_by_the_cited_claim_text():
    bad = [GOOD[0], {"text": "With no further treatment the burden is 12.4% lower after 180 days.", "cites": ["c2"]},
           GOOD[2], GOOD[3]]
    s = Script(advocate=[{"sentences": bad}], skeptic=[sup(4), sup(4)], revise=[{"revised": []}])
    r = run(s)
    assert r["verification"] == "partially-verified"
    assert LEDGER[1]["text"] in r["text"] and "12.4% lower" not in r["text"]


def test_skeptic_objection_the_judge_cannot_see_still_removes_the_sentence():
    vague = [GOOD[0], {"text": "With no further treatment the burden is 12.4% higher, a worrying sign.", "cites": ["c2"]},
             GOOD[2], GOOD[3]]
    verdict = audit(4, {1: ("certainty", "a worrying sign", "not in claim")})
    s = Script(advocate=[{"sentences": vague}], skeptic=[verdict, verdict], revise=[{"revised": []}])
    r = run(s)
    assert "worrying" not in r["text"] and LEDGER[1]["text"] in r["text"]
    assert r["verification"] == "partially-verified"


def test_fabricated_number_and_forbidden_wording_are_caught_by_the_judge_alone():
    assert D.judge_sentence({"text": "Survival improves by 37%.", "cites": ["c2"]}, LEDGER)
    assert any("wording" in x for x in D.judge_sentence(
        {"text": "Temozolomide will extend the patient's life.", "cites": ["c4"]}, LEDGER))
    assert D.judge_sentence({"text": "It grows.", "cites": ["c99"]}, LEDGER)
    assert D.judge_sentence({"text": "Anything at all.", "cites": []}, LEDGER)
    assert D.judge_sentence(GOOD[1], LEDGER) == []


def test_number_from_an_uncited_claim_is_rejected():
    s = {"text": "The burden is 12.4% higher and 3.1% lower.", "cites": ["c2"]}
    assert any("numbers" in x for x in D.judge_sentence(s, LEDGER))


def test_citing_the_safety_claim_does_not_license_its_content():
    assert D.judge_sentence({"text": "This is not medical advice.", "cites": ["c5"]}, LEDGER)   # safety is not a fact


def test_advocate_failure_falls_back_to_template():
    r = run(Script(advocate=["not json at all"]))
    assert r["source"] == "template" and r["text"] == N.template(LEDGER)
    r = run(Script(advocate=[RuntimeError("down")]))
    assert r["source"] == "template" and "advocate failed" in r["fallback_reason"]


def test_skeptic_failure_degrades_to_judge_only_not_to_unchecked():
    s = Script(advocate=[{"sentences": GOOD}], skeptic=[RuntimeError("down"), RuntimeError("down")])
    r = run(s)
    assert r["verification"] == "judge-only" and r["source"] == "llm"


def test_too_few_surviving_sentences_use_the_template():
    s = Script(advocate=[{"sentences": GOOD[:2]}], skeptic=[sup(2)])
    r = run(s)
    assert r["source"] == "template"


def test_cache_and_rerun(tmp_path):
    s = Script(advocate=[{"sentences": GOOD}] * 2, skeptic=[sup(4)] * 2)
    a = run(s, cache_dir=str(tmp_path))
    b = run(s, cache_dir=str(tmp_path))
    assert b["cached"] and b["text"] == a["text"] and s.calls == ["advocate", "skeptic"]
    c = run(s, cache_dir=str(tmp_path), rerun=True)
    assert not c.get("cached") and len(s.calls) == 4


def test_case_ledger_has_ids_one_safety_claim_and_no_baseline_comparison():
    case = {"uid": "mu_glioma_post:PatientID_0034", "status": "ok", "rho": 0.0129, "clinical": {"age_years": 53},
            "observed": [{"day": 0, "seed_burden_mm3": 1416}, {"day": 527, "seed_burden_mm3": 4168}],
            "scenarios": {"no_further_treatment": {"day": [527, 707], "burden_mm3": [4168, 5000]}}}
    led = D.case_ledger(case, {"temozolomide": {"suppress": 0.28}})
    assert [c["id"] for c in led] == [f"c{i}" for i in range(1, len(led) + 1)]
    assert sum(c["kind"] == "safety" for c in led) == 1 and led[-1]["kind"] == "safety"
    assert not any("cohort's median" in c["text"] for c in led)


def test_skeptic_number_objection_is_overruled_when_the_judge_has_verified_the_numbers():
    s = Script(advocate=[{"sentences": GOOD}], skeptic=[audit(4, {1: ("number", "12.4% higher", "seems wrong")})])
    r = run(s)
    assert r["verification"] == "debate-verified" and s.calls == ["advocate", "skeptic"]
    assert r["trace"]["rounds"][0]["skeptic"]["1"]["outcome"].startswith("overruled")
    assert r["trace"]["rounds"][0]["flagged"] == []


def test_direction_objection_stands_when_the_sentence_has_no_number_to_check():
    sent = {"text": "With temozolomide the tumour grows faster.", "cites": ["c3"]}
    o = {"type": "direction", "quote": "grows faster", "reason": "claim says lower"}
    assert D.arbitrate(o, sent, [], LEDGER) == "upheld"


def test_quote_must_be_in_the_sentence():
    o = {"type": "certainty", "quote": "definitely cures", "reason": "x"}
    assert D.arbitrate(o, GOOD[1], [], LEDGER).startswith("overruled")


def test_semantic_objection_on_wording_taken_from_the_claims_is_overruled():
    o = {"type": "added_cause", "quote": "growth suppression taken from published trials", "reason": "x"}
    assert D.arbitrate(o, GOOD[3], [], LEDGER).startswith("overruled")


def test_citation_tags_are_stripped_from_sentences():
    obj = {"sentences": [{"text": "It was run on 150 patients (c1). Another (c2, c3) [c4].", "cites": ["c1"]}]}
    assert D._sentences(obj)[0]["text"] == "It was run on 150 patients. Another."


def test_suppression_percentage_described_as_an_increase_is_caught():
    led = D.with_ids(D.effect_claims({"temozolomide": {"suppress": 0.2754}}))
    assert D.judge_sentence({"text": "Temozolomide is 28% higher.", "cites": ["c1"]}, led)
    assert D.judge_sentence({"text": "Temozolomide suppresses growth by 28% while active.", "cites": ["c1"]}, led) == []


def test_skeptic_error_is_recorded_in_the_trace():
    s = Script(advocate=[{"sentences": GOOD}], skeptic=["garbled", "garbled"])
    r = run(s)
    assert r["verification"] == "judge-only" and r["trace"]["rounds"][0]["skeptic_error"]


def test_skeptic_that_skips_sentences_is_re_asked_then_marked_not_fully_checked():
    partial = audit(2)                                          # 4 sentences, entries for 2
    s = Script(advocate=[{"sentences": GOOD}], skeptic=[partial, partial, partial])
    r = run(s)
    assert s.calls.count("skeptic") == 3
    assert r["verification"] == "judge-only" and "did not audit" in r["trace"]["rounds"][0]["skeptic_error"]


def test_skipped_sentences_are_filled_in_by_the_second_ask():
    s = Script(advocate=[{"sentences": GOOD}], skeptic=[audit(2), {"audit": [
        {"i": 3, "problem": "none", "quote": "", "reason": ""}, {"i": 4, "problem": "none", "quote": "", "reason": ""}]}])
    r = run(s)
    assert r["verification"] == "debate-verified" and s.calls.count("skeptic") == 2


# ---- regression: sentences that passed the first version of the checks (found by reading the baked text)
def _real_case():
    def sc(end):
        return {"day": [527, 707], "burden_mm3": [4168.0, 4168.0 * (1 + end / 100)]}
    return {"uid": "mu_glioma_post:PatientID_0034", "status": "ok", "rho": 0.0129, "clinical": {},
            "observed": [{"day": 0, "seed_burden_mm3": 1416}, {"day": 527, "seed_burden_mm3": 4168}],
            "scenarios": {"no_further_treatment": sc(529.1), "as_recorded": sc(529.1), "tmz_6_cycles": sc(452.2),
                          "tmz_plus_ttfields": sc(197.2), "bevacizumab": sc(216.6)}}


def _ids(led):
    return {c["id"]: c for c in led}


def test_scenario_number_moved_to_the_wrong_label_is_caught():
    led = D.case_ledger(_real_case(), {"temozolomide": {"suppress": 0.28}})
    by = {c["text"][:40]: c["id"] for c in led}
    rec = [c["id"] for c in led if "recorded in the chart" in c["text"]][0]
    bad = {"text": "With temozolomide treatment, the burden is still 529.1% higher after 180 days.", "cites": [rec]}
    assert any(x.startswith("label") for x in D.judge_sentence(bad, led))
    good = {"text": "With the treatment recorded in the chart the burden is 529.1% higher after 180 days.", "cites": [rec]}
    assert D.judge_sentence(good, led) == []


def test_inference_and_cross_scenario_comparison_are_caught():
    led = D.case_ledger(_real_case(), {"temozolomide": {"suppress": 0.28}})
    bev = [c["id"] for c in led if c["text"].startswith("With bevacizumab")][0]
    tmz = [c["id"] for c in led if "six cycles" in c["text"]][0]
    s1 = {"text": "Bevacizumab results in a 216.6% higher burden, showing less suppression than temozolomide alone.",
          "cites": [bev]}
    assert D.judge_sentence(s1, led)
    s2 = {"text": "The burden is still 452.2% higher after 180 days, indicating no significant effect.", "cites": [tmz]}
    assert any("wording" in x for x in D.judge_sentence(s2, led))
    s3 = {"text": "Without any treatment, the model predicts a 529.1% higher burden after 180 days.",
          "cites": [[c["id"] for c in led if "no further treatment" in c["text"]][0]]}
    assert any("wording" in x for x in D.judge_sentence(s3, led))
    s4 = {"text": "Six cycles of temozolomide give a 452.2% higher burden than at the last scan.", "cites": [tmz]}
    assert D.judge_sentence(s4, led) == []


def test_effect_size_attributed_to_the_wrong_agent_is_caught():
    led = D.with_ids(D.effect_claims({"temozolomide": {"suppress": 0.2754}, "bevacizumab": {"suppress": 0.415}}))
    assert D.judge_sentence({"text": "Bevacizumab is modelled as suppressing growth by 28%.", "cites": ["c1"]}, led)
    assert D.judge_sentence({"text": "Temozolomide is modelled as suppressing growth by 28%.", "cites": ["c1"]}, led) == []


def test_skeptic_that_miscounts_is_retargeted_by_its_quote():
    bad = [GOOD[0], {"text": "With no further treatment the burden is 12.4% higher, a worrying sign.", "cites": ["c2"]},
           GOOD[2], GOOD[3]]
    wrong_index = {"audit": [{"i": 1, "problem": "none"}, {"i": 2, "problem": "none"}, {"i": 3, "problem": "certainty",
                   "quote": "a worrying sign", "reason": "not in the claim"}, {"i": 4, "problem": "none"}]}
    s = Script(advocate=[{"sentences": bad}], skeptic=[wrong_index, wrong_index], revise=[{"revised": []}])
    r = run(s)
    assert "worrying" not in r["text"] and r["verification"] == "partially-verified"


def test_required_claims_are_appended_verbatim_when_the_model_leaves_them_out():
    led = D.with_ids([
        N._claim("The simulation was run on 150 patients."),
        N._claim("The next claim."),
        N._claim("Records alone cannot separate drug effects, because treatment is started on growing tumours."),
        N._claim("A third claim."),
        N._claim("This is an illustrative research simulation, not a prediction for this patient and not medical advice."),
    ])
    led[2]["required"] = True
    draft = [{"text": "The simulation was run on 150 patients.", "cites": ["c1"]},
             {"text": "The next claim.", "cites": ["c2"]}, {"text": "A third claim.", "cites": ["c4"]}]
    s = Script(advocate=[{"sentences": draft}], skeptic=[audit(3)])
    r = D.debate(led, "results", model="m", caller=s)
    assert led[2]["text"] in r["text"] and r["text"].endswith(led[-1]["text"])
    assert r["verification"] == "debate-verified"


def test_required_claim_that_the_model_paraphrased_is_restored_verbatim():
    led = D.with_ids([
        N._claim("The simulation was run on 150 patients."),
        N._claim("Records cannot separate drug effects, because treatment is started on growing tumours."),
        N._claim("A third claim."), N._claim("A fourth claim."),
        N._claim("This is an illustrative research simulation, not a prediction for this patient and not medical advice."),
    ])
    led[1]["required"] = True
    draft = [{"text": "The simulation was run on 150 patients.", "cites": ["c1"]},
             {"text": "Drug effects could not be separated.", "cites": ["c2"]},
             {"text": "A third claim.", "cites": ["c3"]}, {"text": "A fourth claim.", "cites": ["c4"]}]
    r = D.debate(led, "results", model="m", caller=Script(advocate=[{"sentences": draft}], skeptic=[audit(4)]))
    assert led[1]["text"] in r["text"] and "could not be separated." not in r["text"]
