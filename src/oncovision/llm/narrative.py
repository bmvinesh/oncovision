"""Plain-language explanations of results from a local Ollama model, with a grounding check.

    python -m oncovision.llm.narrative --cases whatif_cases.json --out whatif_cases_narrated.json \\
        --model nemotron3:33b

Design (after a first version that only checked numbers let the model write "6.6 percentage points
higher" for a result that is 6.6% worse):

* the facts are pre-verbalised as short **claims**, each already stating direction and unit, so the model
  rephrases rather than interprets signed numbers;
* a reply is accepted only if (1) every number in it matches a number in the claims (allowing rounding), and
  (2) every sentence that quotes a claim's key number also carries that claim's direction words and none of
  the opposite ones;
* otherwise, or when Ollama is unreachable or slow, the deterministic template (the claims themselves,
  joined) is returned and marked ``source: "template"``.

The check cannot prove the wording is right; the viewer labels LLM text as AI-written.  Nothing leaves the
machine; no key is needed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "nemotron3:33b"

SYSTEM = (
    "You rewrite a list of verified claims about a research simulation as a short plain-language explanation "
    "for a non-specialist. Every claim is true as written. Use only the claims. Do not combine numbers from "
    "different claims, do not change a number, unit or direction (higher/lower, better/worse), do not add "
    "numbers, and do not give medical advice or say what will happen to a person. Keep every caveat. "
    "Write 4 to 6 short sentences, no headings, no lists."
)

_NUM = re.compile(r"(?<![\w.])-?\d+(?:[.,]\d+)?")

UP = ["higher", "increase", "grow", "rise", "rises", "larger", "bigger", "up "]
DOWN = ["lower", "decrease", "shrink", "fall", "drop", "smaller", "reduc"]
WORSE = ["worse", "less accurate", "less well", "poorer", "lower accuracy", "did not beat", "not better", "higher error",
         "larger error", "more error", "greater error", "above"]
BETTER = ["better", "more accurate", "improv", "beat ", "outperform", "lower error", "smaller error"]


def _claim(text: str, number: str | None = None, must: list[str] | None = None, must_not: list[str] | None = None) -> dict:
    return {"text": text, "number": number, "must": must or [], "must_not": must_not or []}


# ------------------------------------------------------------------ claims
SCENARIO_LABEL = {
    "no_further_treatment": "with no further treatment", "as_recorded": "with the treatment recorded in the chart",
    "tmz_6_cycles": "with six cycles of temozolomide", "tmz_plus_ttfields": "with temozolomide plus tumour treating fields",
    "bevacizumab": "with bevacizumab",
}


def case_claims(case: dict, effects: dict | None = None, forecast: dict | None = None) -> list[dict]:
    obs = case["observed"]
    first, last = obs[0], obs[-1]
    cl = []
    clin = {k: v for k, v in (case.get("clinical") or {}).items() if v is not None}
    who = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in clin.items() if k in ("age_years", "sex", "who_grade", "idh"))
    cl.append(_claim(f"Patient {case['uid'].split(':')[1]}" + (f" ({who})" if who else "")
                     + f" had {len(obs)} scans over {round(last['day'])} days."))
    b0, b1 = round(first["seed_burden_mm3"]), round(last["seed_burden_mm3"])
    cl.append(_claim(f"The modelled tumour burden (a cell-equivalent volume built from the segmentation, not a "
                     f"measured cell count) was {b0} mm3 at the first scan and {b1} mm3 at the last.", str(b1)))
    if case["status"] == "ok":
        cl.append(_claim(f"The simulation's growth rate was fitted to this patient's own scans at {case['rho']:.3f} per day."))
    elif case["status"] == "shrinking":
        cl.append(_claim("The tumour shrank more between scans than the model's growth term can explain, so a loss rate "
                         "was fitted instead of a growth rate."))
    else:
        cl.append(_claim("The model could not be fitted to this patient's scans, so its forecast is unreliable."))
    t_last = last["day"]
    for name, s in case["scenarios"].items():
        if name not in SCENARIO_LABEL:
            continue
        pct = 100 * (s["burden_mm3"][-1] / last["seed_burden_mm3"] - 1)
        h = round(s["day"][-1] - t_last)
        up = pct >= 0
        cl.append(_claim(
            f"{SCENARIO_LABEL[name].capitalize()}, the modelled burden after {h} days is {abs(pct):.1f}% "
            f"{'higher' if up else 'lower'} than at the last scan.", f"{abs(pct):.1f}",
            must=UP if up else DOWN, must_not=DOWN if up else UP))
    if effects:
        parts = [f"{a} {round(100 * v['suppress'])}%" for a, v in effects.items()
                 if isinstance(v, dict) and v.get("suppress")]
        cl.append(_claim("The treatment effects in these scenarios are growth suppression while the drug is active, "
                         "taken from published trials and not measured in this patient group: " + ", ".join(parts) + "."))
    if forecast and "with_treatment" in forecast:
        a, b = forecast["with_treatment"]["mae_log_ratio"], forecast["cohort_median_rate"]["mae_log_ratio"]
        worse = a > b
        cl.append(_claim(
            f"Across {forecast.get('n_forecasts')} forecasts in {forecast.get('n_patients')} patients, the model's average "
            f"error was {a:.2f} against {b:.2f} for simply assuming the cohort's median growth rate, so the model was "
            f"{'less' if worse else 'more'} accurate.", f"{a:.2f}",
            must=WORSE if worse else BETTER, must_not=BETTER if worse else WORSE))
    cl.append(_claim("This is an illustrative research simulation, not a prediction for this patient and not medical advice."))
    return cl


def results_claims(mc: dict) -> list[dict]:
    tf, rk, fc = mc.get("treatment_fit", {}), mc.get("risk", {}), mc.get("forecast", {})
    cl = []
    kill = tf.get("kill_per_day") or {}
    zero = [a for a, v in kill.items() if v.get("ci95") and v["ci95"][0] <= 0 <= v["ci95"][1]]
    if tf.get("n_intervals"):
        cl.append(_claim(
            f"From {tf['n_intervals']} scan intervals in {tf['n_patients']} patients, the fitted drug effects "
            f"({', '.join(sorted(kill))}, and radiation) were not distinguishable from zero"
            + ("" if len(zero) == len(kill) else " except where noted") + "."))
    pct = tf.get("cv_vs_no_treatment_baseline_pct")
    if pct is not None:
        worse = pct < 0
        cl.append(_claim(
            f"Predicting volume change with treatment information was {abs(pct):.1f}% {'worse' if worse else 'better'} "
            f"than ignoring treatment.", f"{abs(pct):.1f}",
            must=WORSE if worse else BETTER, must_not=BETTER if worse else WORSE))
    cl.append(_claim("Treatment is usually started because a tumour is growing, which hides any benefit in this kind of "
                     "retrospective data; the what-if therefore uses effect sizes from clinical trials."))
    ac = mc.get("growth_rate_autocorrelation") or {}
    if ac.get("r") is not None:
        cl.append(_claim(f"A patient's growth rate in one interval is essentially uncorrelated with the next "
                         f"(correlation {ac['r']:.2f} over {ac['n_pairs']} pairs), so extrapolating a patient's own history "
                         f"does not work here."))
    if fc.get("with_treatment") and fc.get("cohort_median_rate"):
        a, b = fc["with_treatment"]["mae_log_ratio"], fc["cohort_median_rate"]["mae_log_ratio"]
        worse = a > b
        cl.append(_claim(
            f"The simulation's forecasts had an average error of {a:.2f}, against {b:.2f} for assuming the cohort median "
            f"growth rate, so they were {'less' if worse else 'more'} accurate.", f"{a:.2f}",
            must=WORSE if worse else BETTER, must_not=BETTER if worse else WORSE))
    p = (rk.get("pooled") or {}).get("auc")
    lo = rk.get("leave_one_dataset_out") or {}
    if p:
        cl.append(_claim(
            f"The mortality risk model reached an AUC of {p:.2f} within the pooled data, but on a cohort it had not seen "
            f"the AUC was {', '.join(f'{v['auc']:.2f}' for v in lo.values())} (0.5 is chance), so it transfers poorly."))
    cl.append(_claim("All of this is a research demonstration, not a clinical tool."))
    return cl


def template(claims: list[dict]) -> str:
    return " ".join(c["text"] for c in claims)


# ------------------------------------------------------------------ checking
def _numbers_in(text: str) -> list[float]:
    out = []
    for m in _NUM.finditer(text):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            pass
    return out


def _decimals(s: str) -> int:
    return len(s.split(".")[1]) if "." in s else 0


def ungrounded_numbers(text: str, claims: list[dict]) -> list[str]:
    """Numbers in ``text`` that match no number in the claims (allowing rounding)."""
    pool = [x for c in claims for x in _numbers_in(c["text"])]
    bad = []
    for m in _NUM.finditer(text):
        tok = m.group(0).replace(",", "")
        x = float(tok)
        if abs(x) <= 12 and float(x).is_integer():       # counts like "three scans", "2 of 3"
            continue
        d = _decimals(tok)
        ok = any(abs(round(q, d) - x) <= 0.5 * 10 ** (-d) + 1e-12 or abs(q - x) <= 0.01 * abs(q) + 1e-9
                 for p in pool for q in (p, abs(p)))
        if not ok:
            bad.append(tok)
    return bad


_CLAUSE = re.compile(r"(?<=[a-zA-Z%\)\"])[.!?]\s+|\n+|;|,?\s+(?:while|whereas|but|versus|compared with)\s+|,\s+and\s+(?:with|without)\s+")


def _sentences(text: str) -> list[str]:
    """Clauses: split on sentence ends, semicolons and contrast words so that two scenarios quoted in one
    sentence are checked separately."""
    return [s for s in _CLAUSE.split(text) if s and s.strip()]


def polarity_violations(text: str, claims: list[dict]) -> list[str]:
    """For each claim with a direction, any sentence quoting its number must carry the direction words
    and none of the opposite ones."""
    bad = []
    for c in claims:
        if not c.get("number") or not (c["must"] or c["must_not"]):
            continue
        for s in _sentences(text):
            if re.search(rf"(?<![\d.]){re.escape(c['number'])}(?!\d)", s):
                low = s.lower() + " "
                if c["must"] and not any(w in low for w in c["must"]):
                    bad.append(f"{c['number']}: missing direction in '{s.strip()[:80]}'")
                elif any(w in low for w in c["must_not"]):
                    bad.append(f"{c['number']}: opposite direction in '{s.strip()[:80]}'")
    return bad


def _strip_think(s: str) -> str:
    return re.sub(r"<think>.*?</think>", "", s, flags=re.S | re.I).strip()


def call_ollama(prompt: str, system: str = SYSTEM, model: str = DEFAULT_MODEL, timeout: float = 120.0,
                max_tokens: int = 400, url: str = OLLAMA_URL) -> str:
    def post(body):
        req = urllib.request.Request(f"{url}/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    body = {"model": model, "stream": False, "think": False,
            "options": {"temperature": 0.1, "num_predict": max_tokens},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    try:
        resp = post(body)
    except urllib.error.HTTPError as e:
        if e.code != 400:
            raise
        body.pop("think")
        resp = post(body)
    return _strip_think(resp.get("message", {}).get("content", ""))


def explain(claims: list[dict], kind: str, model: str = DEFAULT_MODEL, cache_dir: str | None = None,
            timeout: float = 120.0, caller=call_ollama) -> dict:
    key = hashlib.sha256(json.dumps([kind, claims, model], sort_keys=True).encode()).hexdigest()[:20]
    cp = Path(cache_dir) / f"{key}.json" if cache_dir else None
    if cp and cp.exists():
        return json.loads(cp.read_text())
    t0 = time.time()
    prompt = "CLAIMS (each is true; rewrite them as a short explanation):\n" + "\n".join(f"- {c['text']}" for c in claims)
    source, text, note = "template", template(claims), None
    try:
        out = caller(prompt, model=model, timeout=timeout)
        if not out.strip():
            note = "empty reply"
        else:
            bad = ungrounded_numbers(out, claims)
            pol = polarity_violations(out, claims)
            if bad:
                note = f"numbers not in claims: {bad[:5]}"
            elif pol:
                note = f"direction check failed: {pol[:2]}"
            else:
                source, text = "llm", out.strip()
    except Exception as e:  # network, timeout, model missing
        note = f"{type(e).__name__}: {e}"
    res = {"text": text, "source": source, "model": model if source == "llm" else None,
           "elapsed_ms": int(1000 * (time.time() - t0)), "fallback_reason": note if source == "template" else None,
           "claims": [c["text"] for c in claims]}
    if cp:
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps(res))
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--cache", default=None)
    ap.add_argument("--timeout", type=float, default=180.0)
    a = ap.parse_args(argv)
    doc = json.loads(Path(a.cases).read_text())
    eff = {k: v for k, v in (doc.get("effects", {}).get("values") or {}).items()}
    fm = doc.get("forecast_metrics")
    n_llm = 0
    for c in doc["cases"]:
        c["narrative"] = explain(case_claims(c, eff, fm), "case", a.model, a.cache, a.timeout)
        n_llm += c["narrative"]["source"] == "llm"
        print(c["uid"], c["narrative"]["source"], c["narrative"]["elapsed_ms"], "ms",
              c["narrative"]["fallback_reason"] or "", flush=True)
    if doc.get("model_card"):
        mc = dict(doc["model_card"])
        doc["model_card"]["explainer"] = explain(results_claims(mc), "results", a.model, a.cache, a.timeout)
        e = doc["model_card"]["explainer"]
        print("results explainer:", e["source"], e["elapsed_ms"], "ms", e["fallback_reason"] or "")
    Path(a.out).write_text(json.dumps(doc))
    print(f"{n_llm}/{len(doc['cases'])} case narratives from the model")


if __name__ == "__main__":
    main()
