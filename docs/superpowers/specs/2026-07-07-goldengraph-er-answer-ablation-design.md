# GoldenGraph ER→answer ablation across the ambiguity sweep — design

**Status:** design draft 2026-07-07. Awaiting approval → plan.
**Owner:** ER platform.
**Related:** evidence-program slice #2 ("ER→answer delta"), `2026-06-26-goldengraph-er-ablation-scorecard-design.md`, the head-to-head result `benchmarks/er-kg-bench/results/RESULTS_QA_E2E.md`, `2026-06-20-goldengraph-program-roadmap.md` (SP6).

## 1. The question this experiment answers

The head-to-head (`RESULTS_QA_E2E.md`, run 27958804649) surfaced an anomaly that
contradicts GoldenGraph's north star. The program predicted entity resolution would
make answer-accuracy decay **slower** under ambiguity; the measured curve does the
opposite — goldengraph decays **0.416 → 0.040** across ambiguity 0.0→1.0 while
LightRAG decays gentler **0.285 → 0.094**. The doc's own caveat: *"goldengraph wins
on clean-data strength + multi-hop + cost, NOT ambiguity-resilience."*

That result is confounded: it compares whole engines (different extraction, retrieval,
synthesis, corpus construction), so it cannot say **whether ER itself converts to
answer quality under ambiguity**. Two worlds produce the same curve:

- **World A — the moat is real but masked.** ER strands fewer facts, but retrieval-depth
  / synthesis choices eat the gain before the answer. (Plausible: `RESULTS_QA_E2E.md`
  notes two pipeline fixes already flipped the first headline 0.101→0.174.)
- **World B — the moat doesn't convert on this task.** Better cross-doc resolution does
  not yield better answers under noise.

**The decision that hinges on this** (from the roadmap): keep the ER-differentiator
positioning (World A → chase the masking factor) vs. reposition to the cost/multi-hop
win already in hand (World B). This experiment is the deterministic-ish, cheap probe
that separates the two — it is a *diagnostic*, not a product decision.

## 2. Key insight — the isolation already exists at the retrieval layer

`benchmarks/er-kg-bench/erkgbench/qa_e2e/ablation.py::run_ablation` already runs a
**resolution-isolated A/B** across an `ambiguity` parameter, over four ER dials
(`qa_e2e/dials.py`):

| dial | record-key policy | ER strength |
|---|---|---|
| `oracle` | key = canonical id (all surfaces merge) | perfect ER |
| `goldengraph` | key = `dedupe_df` fuzzy cluster (name+type, offline) | real GoldenGraph ER |
| `name_only` | key = exact surface (only identical surfaces merge) | exact-match baseline |
| `none` | unique key per mention (nothing merges) | maximal under-merge |

It builds the store **from gold triples** (bypassing LLM extraction — `_build_store_obj`
synthesizes an `Extraction` per gold edge and attaches the dial's `record_key`), so the
ONLY thing that varies across dials is **cross-document identity**. Extraction is held
constant to gold; retrieval is oracle-seeded. It then measures **bridge-recall by hop**
— "can the resolved+retrieved subgraph WALK the gold answer chain" — and gates on HARD
assertions (monotonic in ER quality; oracle−none gap widens with hops).

**So ER→retrieval is already proven and CI-gated. The gap is exactly one layer: does
that retrieval advantage survive real synthesis into the ANSWER, and does it hold as
ambiguity rises?** That is the missing piece the RESULTS_QA_E2E anomaly lives in.

## 3. Design — extend the ablation from bridge-recall to answer-match

Reuse the dial machinery verbatim; add a synthesis+scoring step on the store each dial
already builds. `_build_store_obj` **already returns the live `store`** ("for the
slice-4c GoldenGraph facade") — the exact object `goldengraph.answer.ask` consumes.

Per (dial × ambiguity × question):
1. Build the dial store from gold triples at that ambiguity (reuse `_build_store_obj` /
   `dials._KEYFN`) — **no extraction LLM**.
2. Answer the question by running the REAL synthesis path — `goldengraph.answer.ask(question,
   store, llm=synth_llm, embedder, valid_t=_AS_OF, tx_t=_AS_OF, hops, node_budget)` — the
   same call `engines/goldengraph.py::GoldenGraphQAEngine.answer` makes.
3. Score with the existing pure metrics (`qa_e2e/metrics.py`): `answer_match` (headline),
   `token_f1`, and the LLM-judge (`judge_prompt`/`parse_judge`) applied uniformly to
   dodge `answer_match`'s format-sensitivity. Bucket each result by the question's
   `ambiguity` and `hop_count`.

**Why this isolation beats running the full `GoldenGraphQAEngine` per dial:** the full
engine runs LLM extraction (~400 nondeterministic, costly calls/run) whose variance would
confound the ER signal, and "oracle" resolution isn't reachable from LLM-extracted
mentions. Holding extraction to gold makes resolution the *only* independent variable —
the clean A/B — and makes the whole sweep cost only **synthesis** (~N calls per cell), an
order of magnitude cheaper than the 4-engine head-to-head.

### The output that answers the question

A **dial × ambiguity answer-match table** plus the **ER→answer delta** curves:

```
delta_oracle(amb)      = answer_match[oracle][amb]      − answer_match[none][amb]
delta_goldengraph(amb) = answer_match[goldengraph][amb] − answer_match[none][amb]
```

**Decision criterion:**
- **World A** if, at every ambiguity, `answer_match` is monotonic in ER
  (`oracle ≥ goldengraph ≥ name_only ≥ none`) AND `delta_oracle(amb) > 0` holds (ideally
  non-shrinking) as ambiguity → 1. Then ER *does* convert to answers; the head-to-head
  ambiguity loss is a retrieval/synthesis-tuning artifact → chase synthesis, keep the
  positioning.
- **World B** if the dials do NOT separate at the answer layer, or `delta_oracle(amb)`
  collapses toward 0 as ambiguity rises. Then the moat does not convert under noise on
  this instrument → reposition to the cost/multi-hop headline.

The *shape of delta vs ambiguity* is the whole deliverable — it localizes the
RESULTS_QA_E2E anomaly to resolution (B) or exonerates it (A).

## 4. Confounds to hold fixed (spec-level requirements)

- **Retrieval budget is IDENTICAL across dials** — pin `hops` + `node_budget`
  (`GOLDENGRAPH_QA_RETRIEVAL_HOPS` / `_NODE_BUDGET`) so resolution is the only variable.
  A dial comparison at different budgets is not an ER isolation.
- **Synthesis determinism** — temperature 0; record model + `n_questions`. The
  deterministic bridge-recall lane (§2) stays the zero-variance backbone; the answer lane
  is the LLM-dependent overlay. Optionally average K synthesis samples per question and
  report variance.
- **`answer_match` format-sensitivity** (documented in `metrics.py`: essays score
  generously, terse entity answers harshly) — report `answer_match` AND the LLM-judge
  AND the **entity-answerable subset** (`metrics.is_entity_answer`), since an entity
  graph can only emit entity answers.
- **`goldengraph` dial** stays the offline `dedupe_df` (name+type, no HF rerank) — same
  policy the retrieval lane uses, so the two lanes are consistent.

## 5. Two lanes (mirror the existing split)

1. **Retrieval lane (EXISTS, unchanged):** `run_ablation` bridge-recall — deterministic,
   $0, CI-gateable. Stays the gate.
2. **Answer lane (NEW):** opt-in, synthesis-LLM only, cost-capped, `workflow_dispatch`
   — mirrors the existing `bench-graphrag-qa.yml` posture (real LLM, hard cost cap, never
   `ci-required`). Records partial results rather than overspending (the qa_e2e pattern).

## 6. Deliverables (for the plan phase)

- `qa_e2e/answer_ablation.py` — `run_answer_ablation(*, seed, n_questions, ambiguity_sweep,
  hops, node_budget, llm, embedder) -> AnswerAblationResult`; reuses `ablation._build_store_obj`,
  `dials`, `metrics`. `AnswerAblationResult` + `render_answer_ablation_md` + `evaluate_assertions`
  (monotonic-in-ER at the answer layer; delta-vs-ambiguity trend) are **wheel-free / LLM-free**
  and unit-testable, exactly like `ablation.py`'s tail.
- `qa_e2e/run_answer_ablation.py` — CLI mirroring `run_ablation.py`: `--ambiguity-sweep
  0,0.25,0.5,0.75,1.0`, `--n-questions`, `--model`, `--max-cost-usd`, `--hops`,
  `--node-budget`, `--out-md`.
- `results/RESULTS_ER_ANSWER_ABLATION.md` — the dial × ambiguity answer-match table +
  delta curves + the World-A/B verdict (machine-generated header, like RESULTS_QA_E2E.md).
- Unit tests (`tests/`) on tiny fixtures with a **stub LLM** (fixed answers) — cover the
  metrics wiring, the markdown render, and the monotonic/delta assertions. The wheel-gated
  store build is already covered by the existing ablation tests.
- Optional: a `run_answer_ablation=true` input on `bench-graphrag-qa.yml` (or a sibling
  `workflow_dispatch` job) — opt-in, cost-capped, NOT `ci-required`.

## 7. Cost estimate

No extraction LLM (gold-triple store). Synthesis only: `4 dials × 5 ambiguity × N`
answer calls. At N=80 = 1,600 synthesis calls on a cheap model (`gpt-4o-mini`), a small
fraction of the full 4-engine head-to-head (which pays extraction on every engine). Hard
`--max-cost-usd` cap + partial-result recording bound the spend.

## 8. Out of scope

- The full 4-engine head-to-head re-run (`RESULTS_QA_E2E.md` owns that).
- Swapping the engineered corpus for a real-world multi-hop QA set (2WikiMultiHopQA) —
  a **separate, complementary** slice (the "real-corpus datapoint" recommendation); this
  experiment isolates ER *within* the engineered instrument, which is the right control
  for the A/B even though its absolute numbers are synthetic.
- The positioning decision itself — this produces the numbers; the World-A/B call is the
  owner's.

## 9. Open questions (resolve before the plan)

1. **Judge model** — reuse `gpt-4o-mini` as the judge, or a stronger fixed judge for the
   verdict? (Judge cost is ~1 call/answer; a stronger judge is affordable here.)
2. **K synthesis samples/question** — 1 (cheapest) vs 3 (variance bars on the delta).
   Recommend 1 for the first run, add K if the delta is within noise.
3. **Ambiguity grid** — the RESULTS_QA_E2E five points (0/0.25/0.5/0.75/1.0), or a finer
   grid near where the head-to-head curves cross (~0.5)?
4. **CI** — ship the answer lane as a `workflow_dispatch` job now, or keep it script-only
   until the first run validates the harness?
