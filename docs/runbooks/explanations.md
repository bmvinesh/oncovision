# Plain-language explanations: two agents and a rule-based judge

Every explanation shown in the viewer is written and checked on the device by one local model
(`nemotron3:33b` through Ollama) and a set of rules. Nothing is sent anywhere.

## Why a debate, and why the judge is code

Two agents built from the same model share blind spots, so their agreement is not evidence. The facts
therefore come from outside the model, and the final say is deterministic.

| Role | What it is | Job |
| --- | --- | --- |
| Claim ledger | `case_claims`, `results_ledger` (code) | Verified statements with ids, numbers, directions and a subject label |
| Advocate | the local model | Writes the explanation as sentences, each citing claim ids |
| Skeptic | the same model, a different prompt and temperature | Audits every sentence with typed objections and an exact quote |
| Judge | `judge_sentence`, `arbitrate` (code) | Checks citations, numbers, directions, scenario and agent labels, banned inference and comparison wording, sentence length; verifies each Skeptic quote; overrules number/direction objections it can check itself; upholds semantic ones |

The Skeptic can send a sentence back or remove it. It can never add a fact. A sentence still disputed after
the last round is replaced by the verified claim text it cites, or dropped. The closing safety line and the
"why" statements (why drug effects are not read from the records, why the scenarios are what-if comparisons)
are appended verbatim by code, so no model can drop or soften them.

## Verification levels

| `verification` | Meaning |
| --- | --- |
| `debate-verified` | Every sentence passed the Judge and was audited by the Skeptic with no upheld objection |
| `partially-verified` | Some sentences were replaced by verified claim text or dropped |
| `judge-only` | The Skeptic did not audit every sentence (the rules still checked all of them) |
| `template` (`source`) | Too little survived, or the Advocate failed: the verified claims joined as text |

## Run it

```bash
# batch: bake explanations into a cases file
python -m oncovision.llm.debate --cases whatif_cases.json --out whatif_cases_debated.json \
    --cache ~/.cache/oncovision/debate --model nemotron3:33b

# service + viewer from one origin (loopback by default)
ONCOVISION_DATA_DIR=~/data/oncovision/processed/viewer python -m oncovision.api.app --port 8090

# measure what each layer catches
python -m oncovision.llm.debate_eval --cases whatif_cases.json --out docs/results/narrative_debate.json --limit 24
```

`POST /api/narrative {"uid": "...", "rerun": true}` runs the agents now; without `rerun` it returns the
stored or cached text. `{"kind": "results"}` explains the cohort-level model card.

## Things learned the hard way

* A model asked to audit "each sentence" audits the first one and stops. The prompt states the count and the
  Judge re-asks for missing indices; a draft is not called debate-verified unless every sentence was audited.
* The Skeptic miscounts (it numbered from 1 while the code numbered from 0) and objects while stating that the
  claim matches. Quotes are therefore verified by string match, and an objection whose quote sits in a
  different sentence is re-targeted to that sentence.
* The first judge passed three sentences that read plausibly: a number moved to the wrong scenario label, a
  false comparison between two scenarios, and "indicating no significant effect". The fix was label binding
  per claim (`binds` / `excludes`), banned inference and comparison wording, and regression tests built from the
  real sentences. Read generated text, not just the verification label.
* Several scenarios can share one number (as recorded and no further treatment often coincide); a clause is
  accepted if it satisfies any claim carrying that number.

## Measured (24 MU-Glioma-Post patients, `nemotron3:33b`, `docs/results/narrative_debate.json`)

**Natural run.** The earlier single-pass explainer kept the model's wording for 67% of patients (the rest fell
back to the template) at a median 3.9 s. The debate kept model wording for 24 of 24, all `debate-verified`,
median 10.6 s, p95 14.8 s. On these drafts the Judge flagged nothing and no objection was upheld: the Advocate
mostly restates the claims, so the checking rarely has anything to remove. Four of 24 runs needed a retry
because a Skeptic reply was not valid JSON.

**Clean control.** On 144 verbatim-claim sentences the Skeptic raised 20 objections on its own (13.9%); the
Judge overruled all of them, so 0 reached the output. The Skeptic alone is noisy, which is why it cannot have
the last word.

**Injected errors** (456 sentences, 19 error types, one wrong sentence added to a clean draft per run):

| Group | n | Old number/direction check | Judge alone | Skeptic upheld | Removed from final text |
| --- | --- | --- | --- | --- | --- |
| Visible to rules (flipped direction, changed or swapped number, fabricated figure, wrong scenario label, scenario-versus-scenario comparison, inferred effect, prediction, advice, over-claim) | 336 | 20% | 99.7% | 94.6% | **100%** |
| Only a reader can see (soft advice, "resistant", "aggressive", "usually do poorly", "do very little") | 120 | 0% | 0% | 93.3% | **88.3%** |

Overall 442 of 456 (96.9%) were removed; 14 survived. All 14 were the soft characterisation types: "aggressive
and concerning" survived in 7 of 24 runs, "resistant" in 3, "usually do poorly" in 2, "do very little" in 2.

**Read these numbers with care.**
* The injections are synthetic and written by the same people who wrote the rules, so the 100% on rule-visible
  errors is partly by design. They do not give the rate at which the model errs unprompted; the natural run is
  the closest thing to that and it was clean.
* The first judge missed three real, plausible-looking errors before this measurement (wrong scenario label,
  false comparison, "indicating no significant effect"). Those were found by reading text, not by this test.
* After seeing the survivors, wording for characterisation and outcome ("aggressive", "concerning", "poorly",
  "resistant", "appears to", "very little") was added to the Judge. That fix is **not** re-measured, and it is
  tuned to what was seen, so the next measurement should use fresh injections.
* One model plays both agents. The Skeptic's value is catching what rules cannot, at the price of noise; the
  Judge's value is that nothing it checks can be talked into passing.
