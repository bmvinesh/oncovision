import json
from pathlib import Path

from oncovision.llm import narrative as N

FACTS = {"patient": "0036", "scans": 3, "span_days": 406, "burden_last_scan_mm3": 45600,
         "scenarios_change_in_burden_after_last_scan": {
             "no_further_treatment": {"horizon_days": 180, "change_vs_last_scan_pct": 12.4},
             "tmz_6_cycles": {"horizon_days": 180, "change_vs_last_scan_pct": -3.1}}}


def test_numbers_in_facts_pass_with_rounding():
    assert N.ungrounded_numbers("Burden is 45,600 mm3, up 12.4% (about 12%).", FACTS) == []
    assert N.ungrounded_numbers("It falls by 3.1%, 3.1 percent.", FACTS) == []


def test_fabricated_number_is_caught():
    assert N.ungrounded_numbers("Survival improves by 37%.", FACTS) == ["37"]


def test_small_counts_are_allowed():
    assert N.ungrounded_numbers("Two of the 3 scans show growth.", FACTS) == []


def test_llm_reply_used_when_grounded():
    r = N.explain(FACTS, "case", caller=lambda p, model, timeout: "Burden rises 12.4% without treatment.")
    assert r["source"] == "llm" and r["fallback_reason"] is None


def test_falls_back_on_fabricated_number():
    r = N.explain(FACTS, "case", caller=lambda p, model, timeout: "It drops 99.5% with treatment.")
    assert r["source"] == "template" and "99.5" in r["fallback_reason"]


def test_falls_back_on_timeout_and_empty():
    def boom(p, model, timeout):
        raise TimeoutError("slow")
    assert N.explain(FACTS, "case", caller=boom)["source"] == "template"
    assert N.explain(FACTS, "case", caller=lambda p, model, timeout: "  ")["source"] == "template"


def test_cache_returns_same_object(tmp_path):
    calls = []
    def c(p, model, timeout):
        calls.append(1); return "Burden rises 12.4%."
    a = N.explain(FACTS, "case", cache_dir=str(tmp_path), caller=c)
    b = N.explain(FACTS, "case", cache_dir=str(tmp_path), caller=c)
    assert a == b and len(calls) == 1


def test_think_blocks_are_stripped():
    assert N._strip_think("<think>hmm</think>Answer 12.4.") == "Answer 12.4."
