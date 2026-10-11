from oncovision.llm import narrative as N

CLAIMS = [
    N._claim("Predicting volume change with treatment information was 6.6% worse than ignoring treatment.", "6.6",
             must=N.WORSE, must_not=N.BETTER),
    N._claim("With no further treatment, the modelled burden after 180 days is 12.4% higher than at the last scan.",
             "12.4", must=N.UP, must_not=N.DOWN),
    N._claim("With six cycles of temozolomide, the modelled burden after 180 days is 3.1% lower than at the last scan.",
             "3.1", must=N.DOWN, must_not=N.UP),
    N._claim("The modelled burden was 40556 mm3 at the first scan and 45613 mm3 at the last.", "45613"),
]


def ex(reply):
    return N.explain(CLAIMS, "case", caller=lambda p, model, timeout: reply)


def test_grounded_and_correct_direction_is_accepted():
    r = ex("Using treatment information made predictions 6.6% worse. Without treatment the burden is 12.4% higher after 180 days; "
           "with temozolomide it is 3.1% lower.")
    assert r["source"] == "llm", r["fallback_reason"]


def test_wrong_direction_is_rejected():
    r = ex("The model's accuracy was 6.6 percentage points higher than ignoring treatment.")
    assert r["source"] == "template" and "direction" in r["fallback_reason"]


def test_swapped_scenario_direction_is_rejected():
    r = ex("With temozolomide the burden grows by 3.1%.")
    assert r["source"] == "template"


def test_fabricated_number_is_rejected():
    r = ex("Survival improves by 37%.")
    assert r["source"] == "template" and "37" in r["fallback_reason"]


def test_small_counts_and_rounding_allowed():
    assert N.ungrounded_numbers("Two of the 3 scans; 12.4%, roughly 12%.", CLAIMS) == []


def test_fallback_on_timeout_and_empty():
    def boom(p, model, timeout):
        raise TimeoutError("slow")
    assert N.explain(CLAIMS, "case", caller=boom)["source"] == "template"
    assert ex("  ")["source"] == "template"


def test_cache_returns_same_object(tmp_path):
    calls = []
    def c(p, model, timeout):
        calls.append(1); return "Using treatment information made predictions 6.6% worse."
    a = N.explain(CLAIMS, "case", cache_dir=str(tmp_path), caller=c)
    b = N.explain(CLAIMS, "case", cache_dir=str(tmp_path), caller=c)
    assert a == b and len(calls) == 1


def test_think_blocks_are_stripped():
    assert N._strip_think("<think>hmm</think>Answer 12.4.") == "Answer 12.4."


def test_patient_identifier_is_not_a_fabricated_number():
    cl = [N._claim("Patient PatientID_0034 had 6 scans over 527 days.")]
    assert N.ungrounded_numbers("Patient 0034 had 6 scans over 527 days.", cl) == []
