"""Two-agent debate with a rule-based judge for the plain-language explanations.

    python -m oncovision.llm.debate --cases whatif_cases.json --out whatif_cases_debated.json \\
        --model nemotron3:33b --cache /tmp/debate_cache

One local model plays two roles with different instructions and temperatures:

* **Advocate** writes the explanation as sentences, each citing the ids of the claims it relies on.
* **Skeptic** audits every sentence against the claims and files typed objections with an exact quote
  (a changed number or direction, merged claims, an added cause, a prediction, extra certainty).
* **Judge** is code, not a model. It re-checks citations, numbers, directions, forbidden wording and length,
  verifies each Skeptic quote, overrules number/direction objections it can check itself, and upholds the
  semantic ones. A sentence survives only if the Judge passes it and no upheld objection remains.

Two agents built from the same model share blind spots, so their agreement is not treated as evidence.
Truth comes from the claim ledger and the Judge; the Skeptic can only remove or send back sentences, never add
facts.  A sentence that is still disputed after the last round is replaced by the verified claim text it cites
(or dropped), and the closing safety line is appended verbatim by code, so the output is always grounded.

Result shape matches :func:`oncovision.llm.narrative.explain` (``text``, ``source``, ``model``, ``elapsed_ms``,
``fallback_reason``, ``claims``) plus ``verification`` and ``trace``.  ``source`` is ``llm`` whenever model
wording survived the checks, ``template`` otherwise.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import narrative as N

MAX_ROUNDS = 2          # critique rounds; at most one revision between them
MAX_WORDS = 50          # per sentence; long sentences are where claims get merged
MIN_SENTENCES = 3       # fewer surviving sentences than this falls back to the template
PROTOCOL = "debate-v3"

SAFETY_MARKERS = ("This is an illustrative research simulation", "All of this is a research demonstration")

# Wording the explanations may never introduce on their own (it is allowed only if a cited claim contains it).
FORBIDDEN = re.compile(
    r"\b(will|would|cures?|cured|recommend\w*|should|prescri\w+|proves?|proven|demonstrat\w+|validated|"
    r"generali[sz]\w*|outperform\w*|more accurate than|accurately predict\w*|diagnos\w+|guarantee\w*|"
    r"survive|survival|life expectancy|prognos\w+|clinically (?:useful|proven|validated)|"
    r"indicat\w+|significan\w+|suggest\w+|impl(?:y|ies|ying)|therefore|thus|hence|because|due to|"
    r"effective\w*|ineffective|benefit\w*|superior|inferior|better than|worse than|"
    r"(?:more|less) suppress\w+|predict\w*|likely|unlikely|probabl\w+|clearly|obviously|undoubtedly|"
    r"aggressive|concerning|worrying|alarming|poor|poorly|resistan\w+|malignan\w+|dangerous|"
    r"little to no|very little|no effect|appears? to|seems? to)\b", re.I)

# One scenario set against another: no claim states such a comparison, so none may be made.
CROSS_COMPARE = re.compile(
    r"\bthan\b(?!\s+(?:at|the\s+(?:last|first)|before))[^.;]{0,30}\b(?:temozolomide|bevacizumab|treating fields|"
    r"ttfields|no further treatment|untreated|without|recorded)\b", re.I)

ADVOCATE_SYSTEM = (
    "You are the Advocate in a two-agent review. Explain verified claims about a research simulation to a "
    "non-specialist. Rules: use only the claims shown; every sentence cites the ids of the claims it relies on; "
    "never change a number, unit or direction; repeat the direction word (higher or lower) next to every "
    "percentage you quote; you may group the scenario claims into one or two sentences; no predictions about a "
    "person, no medical advice, no causes the claims do not state, no conclusions such as 'indicating' or "
    "'significant', no comparison of one scenario with another and none with other methods; describe each "
    "scenario with its own label and its own number. Write calmly and plainly, 4 to 6 sentences, each under "
    "50 words. Reply with JSON only: "
    '{"sentences":[{"text":"...","cites":["c1","c3"]}]}'
)

SKEPTIC_TYPES = ("number", "direction", "merged", "added_cause", "prediction", "advice", "certainty")
SEMANTIC_TYPES = {"added_cause", "prediction", "advice", "certainty"}   # only a reader can judge these

SKEPTIC_SYSTEM = (
    "You are the Skeptic in a two-agent review. Another agent wrote the numbered sentences from the verified "
    "claims. The sentences are numbered from 1. Audit EVERY sentence, one entry each. Set problem to none, or to one of: number (differs from the "
    "cited claim), direction (higher/lower or better/worse differs, or a suppression is described as an "
    "increase), merged (a number attributed to the wrong claim), added_cause (a reason or mechanism no claim "
    "states), prediction (what will happen), advice, certainty (stronger than the claims). Restating a claim in "
    "other words, grouping claims, or citing two claims is not a problem. For a problem, quote the exact words "
    "from the sentence and say in one line what the claims actually state. Do not invent problems. Return "
    "exactly one entry per sentence, in order, and nothing else. Reply with JSON only, for example "
    '{"audit":[{"i":1,"problem":"none"},{"i":2,"problem":"direction","quote":"...","reason":"..."}]}'
)

REVISE_SYSTEM = (
    "You are the Advocate. Objections were raised against some of your sentences. Rewrite only those sentences "
    "so that they are fully supported by the cited claims, keeping the same rules as before. Sentences are "
    "numbered from 1. If a sentence "
    "cannot be supported, return it with empty text to delete it. Reply with JSON only: "
    '{"revised":[{"i":3,"text":"...","cites":["c1"]}]}'
)


# ------------------------------------------------------------------ claim ledger
def with_ids(claims: list[dict]) -> list[dict]:
    """Copy claims, adding ``id`` (c1, c2, ...) and ``kind`` (``fact`` or ``safety``)."""
    out = []
    for i, c in enumerate(claims, 1):
        d = dict(c)
        d["id"] = f"c{i}"
        d["kind"] = "safety" if d["text"].startswith(SAFETY_MARKERS) else "fact"
        out.append(d)
    return out


SUPPRESS_NOT = ["higher", "increas", "raise", "larger", "bigger", "more than", "above", "faster", "boost"]
_AGENT_NAME = {"temozolomide": "Temozolomide", "ttfields": "Tumour treating fields", "bevacizumab": "Bevacizumab",
               "lomustine_pcv": "Lomustine/PCV", "immunotherapy": "Immunotherapy"}


_AGENT_KW = {"temozolomide": ["temozolomide"], "ttfields": ["treating fields", "ttfields"],
             "bevacizumab": ["bevacizumab"]}


def effect_claims(effects: dict | None) -> list[dict]:
    """One claim per agent so each percentage carries its own direction (suppression is not an increase)."""
    out = []
    for a, v in (effects or {}).items():
        if isinstance(v, dict) and v.get("suppress"):
            pct = round(100 * v["suppress"])
            own = _AGENT_KW.get(a, [a.lower()])
            others = [k for b, kws in _AGENT_KW.items() if b != a for k in kws if k not in own]
            out.append(_claim(f"{_AGENT_NAME.get(a, a)} is modelled as suppressing growth by {pct}% while the drug "
                              f"is active, an effect size taken from a published trial.", str(pct),
                              must_not=SUPPRESS_NOT, binds=own, excludes=others))
    return out


def case_ledger(case: dict, effects: dict | None = None) -> list[dict]:
    """Per-patient claims.  The model-versus-baseline comparison lives in the evidence card, not in this text."""
    cl = [c for c in N.case_claims(case, None, None)]          # effects are re-added per agent below
    safety = [c for c in cl if c["text"].startswith(SAFETY_MARKERS)]
    return with_ids([c for c in cl if c not in safety] + effect_claims(effects) + safety)


def _claim(text, number=None, must=None, must_not=None, binds=None, excludes=None):
    return N._claim(text, number, must, must_not, binds, excludes)


def results_ledger(mc: dict, effects: dict | None = None) -> list[dict]:
    """Cohort-level claims: what was run, why the what-if layer uses trial effect sizes, what the numbers may mean."""
    tf, rk = mc.get("treatment_fit", {}), mc.get("risk", {})
    cal, ac = mc.get("calibration") or {}, mc.get("growth_rate_autocorrelation") or {}
    cl = []
    if cal.get("n_patients"):
        cl.append(_claim(f"The simulation was run on {cal['n_patients']} patients, each seeded from their own tumour "
                         f"segmentation."))
        if cal.get("n_shrinking"):
            cl.append(_claim(f"For {cal['n_shrinking']} of them the tumour was smaller on the second scan, so a loss "
                             f"rate was fitted instead of a growth rate."))
    if tf.get("n_intervals"):
        cl.append(dict(_claim(
            f"In {tf['n_intervals']} scan intervals from {tf['n_patients']} patients, drug effects fitted from the "
            f"records could not be separated from zero, because treatment is usually started when a tumour is "
            f"already growing. For that reason the scenarios use growth-suppression effect sizes from published "
            f"trials."), required=True))
    cl += effect_claims(effects)
    if ac.get("r") is not None:
        cl.append(dict(_claim(
            f"A patient's growth rate in one interval was nearly unrelated to the next (correlation {ac['r']:.2f} "
            f"over {ac['n_pairs']} pairs), which may reflect measurement variation between scans; the scenarios "
            f"are therefore what-if comparisons, not forecasts of one patient's course."), required=True))
    p = (rk.get("pooled") or {}).get("auc")
    if p:
        w = [v["auc"] for v in (rk.get("within_dataset") or {}).values() if v.get("auc")]
        rng = f" Within single datasets it ranged from {min(w):.2f} to {max(w):.2f}." if w else ""
        cl.append(_claim(f"A mortality risk model trained on {rk.get('n')} patients from three public datasets "
                         f"reached an AUC of {p:.2f} in cross-validation (0.5 is chance, 1.0 is perfect)." + rng))
    cl.append(_claim("All of this is a research demonstration, not a clinical tool."))
    return with_ids(cl)


# ------------------------------------------------------------------ model access
def ollama_caller(system: str, user: str, model: str = N.DEFAULT_MODEL, timeout: float = 120.0,
                  temperature: float = 0.1, max_tokens: int = 600, url: str = N.OLLAMA_URL) -> str:
    body = {"model": model, "stream": False, "think": False, "format": "json",
            "options": {"temperature": temperature, "num_predict": max_tokens},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    last = None
    for drop in (None, "think", "format"):     # older builds reject one of the optional fields
        if drop:
            body.pop(drop, None)
        req = urllib.request.Request(f"{url}/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return N._strip_think(json.loads(r.read()).get("message", {}).get("content", ""))
        except urllib.error.HTTPError as e:
            if e.code != 400:
                raise
            last = e
    raise last


def _json(text: str) -> dict:
    m = re.search(r"\{.*\}", text.strip(), re.S)
    if not m:
        raise ValueError("no JSON object in reply")
    obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise ValueError("reply is not a JSON object")
    return obj


_CITE_TAG = re.compile(r"\s*[\(\[]\s*c\d+(?:\s*[,;]\s*c\d+)*\s*[\)\]]")


def _sentences(obj: dict, key: str = "sentences") -> list[dict]:
    out = []
    for s in obj.get(key) or []:
        if not isinstance(s, dict):
            continue
        text = _CITE_TAG.sub("", str(s.get("text", ""))).strip()
        cites = [c for c in (s.get("cites") or []) if isinstance(c, str)]
        out.append({"i": s.get("i"), "text": text, "cites": cites})
    return out


# ------------------------------------------------------------------ the judge (no model involved)
def judge_sentence(sent: dict, ledger: list[dict]) -> list[str]:
    by_id = {c["id"]: c for c in ledger}
    facts = [c for c in ledger if c["kind"] == "fact"]
    text, issues = sent["text"], []
    unknown = [c for c in sent["cites"] if c not in by_id]
    cited = [by_id[c] for c in sent["cites"] if c in by_id and by_id[c]["kind"] == "fact"]
    if unknown:
        issues.append(f"cites unknown claim {unknown}")
    if not cited:
        issues.append("cites no claim")
    bad = N.ungrounded_numbers(text, cited)
    if bad:
        issues.append(f"numbers not in the cited claims: {bad[:4]}")
    pol = N.polarity_violations(text, facts)
    if pol:
        issues.append(f"direction: {pol[0]}")
    bind = N.binding_violations(text, facts)
    if bind:
        issues.append(f"label: {bind[0]}")
    if CROSS_COMPARE.search(text):
        issues.append("compares one scenario with another; no claim does")
    cited_text = " ".join(c["text"].lower() for c in cited)
    for m in FORBIDDEN.finditer(text):
        if m.group(0).lower() not in cited_text:
            issues.append(f"wording not allowed: '{m.group(0)}'")
            break
    if len(text.split()) > MAX_WORDS:
        issues.append(f"over {MAX_WORDS} words")
    return issues


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t.strip().lower())


def arbitrate(obj: dict, sent: dict, judge_issues: list[str], ledger: list[dict]) -> str:
    """Decide a Skeptic objection.  Returns ``upheld`` or the reason it was set aside.

    The Judge only trusts what it can verify: the quote must really be in the sentence, and an objection about
    a number or direction stands only if the Judge agrees or cannot check (no number in the sentence)."""
    q = _norm(obj.get("quote", ""))
    if obj.get("type") not in SKEPTIC_TYPES:
        return "overruled: unknown objection type"
    if len(q) < 3 or q not in _norm(sent["text"]):
        return "overruled: quote is not in the sentence"
    if obj["type"] in SEMANTIC_TYPES:
        cited = " ".join(c["text"].lower() for c in ledger if c["id"] in sent["cites"])
        words = {w for w in re.findall(r"[a-z]{4,}", q)}
        if words and all(w in cited for w in words):
            return "overruled: the quoted words are all in the cited claims"
        return "upheld"
    if judge_issues:
        return "upheld"
    if N._numbers_in(sent["text"]):
        return "overruled: the Judge checked the numbers and directions and they match"
    return "upheld"


# ------------------------------------------------------------------ the debate
def _claims_block(ledger: list[dict]) -> str:
    return "\n".join(f"[{c['id']}] {c['text']}" for c in ledger if c["kind"] == "fact")


def _numbered(sents: list[dict]) -> str:
    return "\n".join(f"{i}. {s['text']}  (cites {', '.join(s['cites']) or 'nothing'})" for i, s in enumerate(sents, 1))


def debate(claims: list[dict], kind: str = "case", model: str = N.DEFAULT_MODEL, cache_dir: str | None = None,
           timeout: float = 120.0, caller=ollama_caller, rerun: bool = False) -> dict:
    """``claims`` must already carry ids (:func:`case_ledger`, :func:`results_ledger`)."""
    key = hashlib.sha256(json.dumps([PROTOCOL, kind, claims, model], sort_keys=True).encode()).hexdigest()[:20]
    cp = Path(cache_dir) / f"{key}.json" if cache_dir else None
    if cp and cp.exists() and not rerun:
        res = json.loads(cp.read_text())
        res["cached"] = True
        return res
    t_start = time.time()
    by_id = {c["id"]: c for c in claims}
    safety = [c["text"] for c in claims if c["kind"] == "safety"]
    ms = {"advocate": 0, "skeptic": 0}
    calls = {"advocate": 0, "skeptic": 0}

    def ask(role, system, user, temperature):
        t0 = time.time()
        try:
            return caller(system, user, model=model, timeout=timeout, temperature=temperature)
        finally:
            ms[role] += int(1000 * (time.time() - t0))
            calls[role] += 1

    def finish(text, source, note, verification, rounds, final=None):
        res = {"text": text, "source": source, "model": model if source == "llm" else None,
               "elapsed_ms": int(1000 * (time.time() - t_start)), "fallback_reason": note,
               "claims": [c["text"] for c in claims], "verification": verification,
               "trace": {"protocol": PROTOCOL, "rounds": rounds, "final": final or [],
                         "agents": {"advocate": {"model": model, "calls": calls["advocate"], "ms": ms["advocate"]},
                                    "skeptic": {"model": model, "calls": calls["skeptic"], "ms": ms["skeptic"]},
                                    "judge": {"type": "rule-based", "model": None}}}}
        if cp:
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps(res))
        return res

    template = N.template(claims)
    block = _claims_block(claims)
    try:                                                      # ---- round 0: the Advocate drafts
        raw = ask("advocate", ADVOCATE_SYSTEM, f"CLAIMS (each is true):\n{block}\n\nWrite the explanation.", 0.2)
        sents = [dict(s, origin="advocate") for s in _sentences(_json(raw)) if s["text"]]
        if not sents:
            raise ValueError("empty draft")
    except Exception as e:
        return finish(template, "template", f"advocate failed: {type(e).__name__}: {e}", "template", [])

    rounds, flagged, skeptic_ok = [], {}, True
    for r in range(1, MAX_ROUNDS + 1):
        j = [judge_sentence(s, claims) for s in sents]
        objections: dict[int, dict] = {}
        skeptic_error, entries = None, {}
        for attempt in range(3):                              # replies that are garbled or skip sentences are re-asked
            missing = [i for i in range(len(sents)) if i not in entries]
            if not missing:
                break
            ask_for = (f"\n\nThere are {len(sents)} sentences, numbered 1 to {len(sents)}. Return exactly "
                       f"{len(sents)} entries" + ("." if attempt == 0 else
                                                  f"; you have not yet covered {[m + 1 for m in missing]}."))
            try:
                sk = _json(ask("skeptic", SKEPTIC_SYSTEM,
                               f"CLAIMS:\n{block}\n\nSENTENCES:\n{_numbered(sents)}{ask_for}", 0.0))
            except Exception as e:
                skeptic_error = f"{type(e).__name__}: {str(e)[:120]}"
                continue
            for v in sk.get("audit") or []:
                if isinstance(v, dict) and isinstance(v.get("i"), int) and 1 <= v["i"] <= len(sents):
                    entries.setdefault(v["i"] - 1, v)
        unaudited = [i for i in range(len(sents)) if i not in entries]
        if unaudited:
            skeptic_ok = False
            skeptic_error = skeptic_error or f"skeptic did not audit sentences {unaudited}"
        for i, v in entries.items():
            if str(v.get("problem", "none")).lower() in ("none", ""):
                continue
            o = {"type": str(v.get("problem", "")).lower(), "quote": str(v.get("quote", ""))[:200],
                 "reason": str(v.get("reason", ""))[:240]}
            # a quote found in exactly one other sentence belongs to that sentence (the model miscounted)
            here = _norm(o["quote"]) in _norm(sents[i]["text"]) and len(o["quote"]) >= 3
            elsewhere = [k for k in range(len(sents)) if k != i and len(o["quote"]) >= 3
                         and _norm(o["quote"]) in _norm(sents[k]["text"])]
            tgt = i if here or len(elsewhere) != 1 else elsewhere[0]
            o["outcome"] = arbitrate(o, sents[tgt], j[tgt], claims)
            if tgt not in objections or o["outcome"] == "upheld":
                objections[tgt] = o
        flagged = {}
        for i in range(len(sents)):
            why = [f"judge: {x}" for x in j[i]]
            o = objections.get(i)
            if o and o["outcome"] == "upheld":
                why.append(f"skeptic ({o['type']}): {o['reason']} '{o['quote']}'")
            if why:
                flagged[i] = why
        rounds.append({"round": r, "sentences": [dict(s) for s in sents],
                       "judge": {str(i): x for i, x in enumerate(j) if x},
                       "skeptic": {str(i): o for i, o in objections.items()}, "skeptic_error": skeptic_error,
                       "flagged": sorted(flagged)})
        if not flagged or r == MAX_ROUNDS:
            break
        try:                                                  # ---- the Advocate answers the objections
            obj = "\n".join(f"{i + 1}. " + " | ".join(w) for i, w in flagged.items())
            raw = ask("advocate", REVISE_SYSTEM,
                      f"CLAIMS:\n{block}\n\nSENTENCES:\n{_numbered(sents)}\n\nOBJECTIONS:\n{obj}", 0.1)
            for s in _sentences(_json(raw), "revised"):
                k = s["i"] - 1 if isinstance(s["i"], int) else -1
                if k in flagged and 0 <= k < len(sents):
                    sents[k] = {"text": s["text"], "cites": s["cites"], "origin": "revised"}
            rounds[-1]["revised"] = sorted(flagged)
        except Exception:
            rounds[-1]["revised"] = []                        # keep the draft; the final pass still rules

    # ---- final: the Judge's last word.  Disputed sentences become their cited claim text, or are dropped.
    kept_cites = {c for i, s in enumerate(sents) if i not in flagged for c in s["cites"]}
    final, substituted, dropped = [], 0, 0
    for i, s in enumerate(sents):
        if i not in flagged and s["text"]:
            final.append({"text": s["text"], "cites": s["cites"], "origin": s["origin"]})
            continue
        cited = [by_id[c] for c in dict.fromkeys(s["cites"]) if c in by_id and by_id[c]["kind"] == "fact"]
        if not cited:
            dropped += 1
            continue
        for c in cited:
            if c["id"] not in kept_cites:
                final.append({"text": c["text"], "cites": [c["id"]], "origin": "ledger"})
                kept_cites.add(c["id"])
        substituted += 1
    if len(final) < MIN_SENTENCES or all(f["origin"] == "ledger" for f in final):
        return finish(template, "template", "too few sentences survived the checks", "template", rounds, final)
    # The "why" statements are never left to the model: a sentence that rests only on required claims is
    # replaced by the claim text, and any required claim nobody cited is appended.
    required = {c["id"] for c in claims if c.get("required")}
    placed, fixed = set(), []
    for f in final:
        if f["origin"] != "ledger" and f["cites"] and set(f["cites"]) <= required:
            for cid in dict.fromkeys(f["cites"]):
                if cid not in placed:
                    fixed.append({"text": by_id[cid]["text"], "cites": [cid], "origin": "ledger"})
                    placed.add(cid)
        else:
            fixed.append(f)
            if f["origin"] == "ledger":
                placed.update(f["cites"])
    final = fixed
    covered = {c for f in final for c in f["cites"]}
    for c in claims:
        if c.get("required") and c["id"] not in covered:
            final.append({"text": c["text"], "cites": [c["id"]], "origin": "ledger"})
    text = " ".join([f["text"] for f in final] + safety)
    # "judge-only": at least one sentence was not audited by the Skeptic, so only the rules checked it
    verification = ("judge-only" if not skeptic_ok else
                    "partially-verified" if (substituted or dropped) else "debate-verified")
    return finish(text, "llm", None, verification, rounds, final)


# ------------------------------------------------------------------ CLI
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default=N.DEFAULT_MODEL)
    ap.add_argument("--cache", default=None)
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--rerun", action="store_true", help="ignore cached results")
    a = ap.parse_args(argv)
    doc = json.loads(Path(a.cases).read_text())
    eff = (doc.get("effects") or {}).get("values") or {}
    cases = doc["cases"][: a.limit] if a.limit else doc["cases"]

    def one(c):
        return debate(case_ledger(c, eff), "case", a.model, a.cache, a.timeout, rerun=a.rerun)

    with ThreadPoolExecutor(max_workers=max(1, a.workers)) as ex:
        for c, res in zip(cases, ex.map(one, cases)):
            c["narrative"] = res
            print(c["uid"], res["source"], res["verification"], f"{res['elapsed_ms']} ms",
                  f"rounds={len(res['trace']['rounds'])}", res["fallback_reason"] or "", flush=True)
    if doc.get("model_card"):
        res = debate(results_ledger(doc["model_card"], eff), "results", a.model, a.cache, a.timeout, rerun=a.rerun)
        doc["model_card"]["explainer"] = res
        print("results explainer:", res["source"], res["verification"], f"{res['elapsed_ms']} ms")
    doc["cases"] = cases
    Path(a.out).write_text(json.dumps(doc))
    n = sum(c["narrative"]["verification"] == "debate-verified" for c in cases)
    print(f"{n}/{len(cases)} fully debate-verified")


if __name__ == "__main__":
    main()
