"""Plain-language explanations of results from a local Ollama model, with a grounding check.

    python -m oncovision.llm.narrative --cases whatif_cases.json --out whatif_cases_narrated.json \\
        --model nemotron3:33b

Two things are explained: each patient's what-if case, and the model card (what was and was not
learned).  The model is told to use only the facts it is given.  Every number in its reply must
match a number in the facts (allowing rounding); otherwise, or when Ollama is unreachable or slow,
a deterministic template built from the same facts is returned and marked ``source: "template"``.
Nothing leaves the machine; no key is needed.
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
    "You explain research simulation results to a non-specialist. Use ONLY the facts in the JSON you are given. "
    "Do not introduce any number that is not in the facts. Do not give medical advice, treatment recommendations "
    "or prognosis for a person. Say plainly what is measured, what is a model assumption, and what the evidence "
    "does not show. Write 4 to 6 short sentences, no headings, no lists."
)

_NUM = re.compile(r"(?<![\w.])-?\d+(?:[.,]\d+)?")


def _flatten_numbers(obj, out: list[float]):
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, str):
        for m in _NUM.finditer(obj):
            try:
                out.append(float(m.group(0).replace(",", "")))
            except ValueError:
                pass
    elif isinstance(obj, dict):
        for v in obj.values():
            _flatten_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _flatten_numbers(v, out)


def _decimals(s: str) -> int:
    return len(s.split(".")[1]) if "." in s else 0


def ungrounded_numbers(text: str, facts: dict) -> list[str]:
    """Numbers in ``text`` that do not correspond to any number in ``facts`` (allowing rounding,
    percentages and per-mille style rescalings by 100)."""
    pool: list[float] = []
    _flatten_numbers(facts, pool)
    bad = []
    for m in _NUM.finditer(text):
        tok = m.group(0).replace(",", "")
        x = float(tok)
        if abs(x) <= 12 and float(x).is_integer():       # counts like "three scans", "2 of 3"
            continue
        d = _decimals(tok)
        ok = False
        for p in pool:
            for q in (p, p * 100.0, p / 100.0, -p, abs(p)):
                tol = max(0.5 * 10 ** (-d), 1e-9 + 0.01 * abs(q))
                if abs(round(q, d) - x) <= 0.5 * 10 ** (-d) + 1e-12 or abs(q - x) <= tol:
                    ok = True
                    break
            if ok:
                break
        if not ok:
            bad.append(tok)
    return bad


def _strip_think(s: str) -> str:
    s = re.sub(r"<think>.*?</think>", "", s, flags=re.S | re.I)
    return s.strip()


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


# ------------------------------------------------------------------ facts
def case_facts(case: dict, effects: dict | None = None, forecast: dict | None = None) -> dict:
    obs = case["observed"]
    first, last = obs[0], obs[-1]
    scen = {}
    t_last = last["day"]
    for name, s in case["scenarios"].items():
        end = s["burden_mm3"][-1]
        scen[name] = {"horizon_days": round(s["day"][-1] - t_last),
                      "change_vs_last_scan_pct": round(100 * (end / last["seed_burden_mm3"] - 1), 1)}
    f = {
        "patient": case["uid"].split(":")[1],
        "clinical": {k: v for k, v in (case.get("clinical") or {}).items() if v is not None},
        "scans": len(obs), "span_days": round(last["day"]),
        "burden_first_scan_mm3": round(first["seed_burden_mm3"]), "burden_last_scan_mm3": round(last["seed_burden_mm3"]),
        "calibration_status": case["status"],
        "growth_rate_per_day": round(case["rho"], 4),
        "scenarios_change_in_burden_after_last_scan": scen,
        "treatment_recorded": [c["agent"] for c in case["recorded_schedule"]["drugs"]]
        + (["radiation"] if case["recorded_schedule"]["radiation"] else []),
        "schedule_assumptions": case.get("assumptions") or [],
    }
    if effects:
        f["effect_sizes_are_from_trials_not_this_cohort"] = {
            a: f"growth suppression {round(100 * v['suppress'])}% while active"
            for a, v in effects.items() if isinstance(v, dict) and v.get("suppress")}
    if forecast:
        f["how_well_forecasts_work"] = {
            "mean_error_model_with_treatment": round(forecast["with_treatment"]["mae_log_ratio"], 2),
            "mean_error_cohort_median_rate": round(forecast["cohort_median_rate"]["mae_log_ratio"], 2),
            "number_of_forecasts": forecast.get("n_forecasts")}
    return f


def results_facts(model_card: dict) -> dict:
    tf, rk, fc = model_card.get("treatment_fit", {}), model_card.get("risk", {}), model_card.get("forecast", {})
    kill = {a: {"fitted_per_day": round(v["value"], 4), "ci95": [round(x, 4) for x in (v.get("ci95") or [])]}
            for a, v in (tf.get("kill_per_day") or {}).items()}
    return {
        "treatment_effect_fit": {"intervals": tf.get("n_intervals"), "patients": tf.get("n_patients"),
                                 "fitted_kill_rates": kill, "radiation_ci95_per_gy": tf.get("radiation_k_per_gy", {}).get("ci95"),
                                 "accuracy_vs_ignoring_treatment_pct": tf.get("cv_vs_no_treatment_baseline_pct"),
                                 "caveat": tf.get("caveat")},
        "forecast_check": {k: (round(v["mae_log_ratio"], 2) if isinstance(v, dict) and "mae_log_ratio" in v else v)
                           for k, v in fc.items() if k != "note"},
        "growth_rate_autocorrelation": model_card.get("growth_rate_autocorrelation"),
        "risk_model_auc": {"pooled": (rk.get("pooled") or {}).get("auc"),
                           "unseen_cohort": {k: round(v["auc"], 2) for k, v in (rk.get("leave_one_dataset_out") or {}).items()}},
    }


# ------------------------------------------------------------------ templates (deterministic fallback)
def case_template(f: dict) -> str:
    sc = f["scenarios_change_in_burden_after_last_scan"]
    parts = [f"Patient {f['patient']} has {f['scans']} scans over {f['span_days']} days."]
    if "no_further_treatment" in sc and "tmz_6_cycles" in sc:
        parts.append(
            f"In the simulation, with no further treatment the modelled tumour burden changes by "
            f"{sc['no_further_treatment']['change_vs_last_scan_pct']}% over the next "
            f"{sc['no_further_treatment']['horizon_days']} days, and with six cycles of temozolomide by "
            f"{sc['tmz_6_cycles']['change_vs_last_scan_pct']}%.")
    parts.append("The treatment effects are taken from published trials, not learned from this patient group, "
                 "so this shows how a tumour could respond, not what it will do.")
    if f.get("how_well_forecasts_work"):
        h = f["how_well_forecasts_work"]
        parts.append(f"Across the cohort, the model's forecasts were less accurate (error {h['mean_error_model_with_treatment']}) "
                     f"than simply assuming the average growth rate (error {h['mean_error_cohort_median_rate']}).")
    return " ".join(parts)


def results_template(f: dict) -> str:
    t = f["treatment_effect_fit"]
    return (f"From {t['intervals']} scan intervals in {t['patients']} patients, the fitted drug effects were not "
            f"distinguishable from zero, and a model using treatment predicted volume change "
            f"{abs(t['accuracy_vs_ignoring_treatment_pct'])}% {'worse' if (t['accuracy_vs_ignoring_treatment_pct'] or 0) < 0 else 'better'} "
            f"than one that ignored it. Treatment is usually started because a tumour is growing, which hides any benefit "
            f"in retrospective data. The simulation therefore uses effect sizes from clinical trials and is illustrative.")


# ------------------------------------------------------------------ main entry points
def explain(facts: dict, kind: str, model: str = DEFAULT_MODEL, cache_dir: str | None = None,
            timeout: float = 120.0, caller=call_ollama) -> dict:
    key = hashlib.sha256(json.dumps([kind, facts, model], sort_keys=True).encode()).hexdigest()[:20]
    cp = Path(cache_dir) / f"{key}.json" if cache_dir else None
    if cp and cp.exists():
        return json.loads(cp.read_text())
    template = case_template(facts) if kind == "case" else results_template(facts)
    t0 = time.time()
    prompt = ("Explain these simulation results.\nFACTS (JSON):\n" + json.dumps(facts, indent=1)
              + "\n\nWrite the explanation now.")
    source, text, note = "template", template, None
    try:
        out = caller(prompt, model=model, timeout=timeout)
        if not out.strip():
            note = "empty reply"
        else:
            bad = ungrounded_numbers(out, facts)
            if bad:
                note = f"numbers not in facts: {bad[:5]}"
            else:
                source, text = "llm", out.strip()
    except Exception as e:  # network, timeout, model missing
        note = f"{type(e).__name__}: {e}"
    res = {"text": text, "source": source, "model": model if source == "llm" else None,
           "elapsed_ms": int(1000 * (time.time() - t0)), "fallback_reason": note if source == "template" else None}
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
        c["narrative"] = explain(case_facts(c, eff, fm), "case", a.model, a.cache, a.timeout)
        n_llm += c["narrative"]["source"] == "llm"
        print(c["uid"], c["narrative"]["source"], c["narrative"]["elapsed_ms"], "ms", flush=True)
    if doc.get("model_card"):
        doc["model_card"]["explainer"] = explain(results_facts(doc["model_card"]), "results", a.model, a.cache, a.timeout)
        print("results explainer:", doc["model_card"]["explainer"]["source"],
              doc["model_card"]["explainer"]["elapsed_ms"], "ms")
    Path(a.out).write_text(json.dumps(doc))
    print(f"{n_llm}/{len(doc['cases'])} case narratives from the model")


if __name__ == "__main__":
    main()
