# Plan — GoldenGraph ER→answer ablation across the ambiguity sweep

**Spec:** `docs/superpowers/specs/2026-07-07-goldengraph-er-answer-ablation-design.md`.
**Root:** `packages/python/goldenmatch/benchmarks/er-kg-bench/` (the `erkgbench` module;
tests run with CWD = this dir so `erkgbench` + `dataset` import).

## Reuse / delta from the spec (READ FIRST)

The spec said "add `answer_ablation.py`". On implementation audit the per-ambiguity
answer-match ablation **already exists** and is the wrong thing to rebuild:

- `qa_e2e/scorecard_llm.py::answer_match_ablation(corpus, g, typ_of, llm) -> dict` —
  runs all four dials (`oracle`/`goldengraph`/`name_only`/`none` via `ablation._build_store_obj`
  + `dials`), returns per-dial `{"answer_match": {"mean",...}, "bridge_recall": {"mean",...}}`
  at ONE ambiguity (the corpus's).
- `qa_e2e/scorecard_llm.py::tracking_verdict(am_by_dial, br_by_dial) -> (label, passed)` —
  does the answer-match dial-ordering equal the bridge-recall ordering (does ER convert),
  at one ambiguity.
- `qa_e2e/scorecard_llm.py::run_scorecard(*, seed, n_questions, ambiguity, max_hops,
  inner_llm, budget_usd)` — the budget-capped orchestrator (`_BudgetedLLM`, `gpt-4o-mini`
  default, `llm.exhausted` short-circuit) + `render_scorecard_md`; CLI `run_scorecard.py`.
- Tests already exist: `tests/test_qa_answer_match_ablation.py` (`tracking_verdict` +
  a `_FixedLLM` e2e).

**So the real gap is a thin layer, not an engine:** (1) sweep the existing primitive over
ambiguity, (2) compute the **delta-vs-ambiguity trend** + the **World-A/B verdict** (the
one thing nothing computes today — `tracking_verdict` only checks ordering at a single
ambiguity, not whether the ER→answer delta *survives rising ambiguity*), (3) a runner, (4)
the results doc. Headline metric stays `answer_match` (what the primitive computes); the
LLM-judge / entity-subset overlay is an optional enhancement (Task 6), not core.

## TDD tasks (failing test → minimal impl → commit)

### Task 1 — sweep aggregation (pure, LLM-free)
- **Test (red):** `tests/test_qa_answer_ablation_sweep.py::test_aggregate_sweep_shapes`.
  Feed a hand-built `dict[float, dict]` (ambiguity → an `answer_match_ablation`-shaped dict
  for 2 ambiguities × 4 dials) into `aggregate_sweep`; assert the result exposes, per dial,
  `answer_match[amb]` + `bridge_recall[amb]` curves, and `delta_oracle[amb]` /
  `delta_goldengraph[amb]` = `am[dial][amb] − am["none"][amb]`.
- **Impl (green):** new `qa_e2e/answer_ablation_sweep.py`:
  - `@dataclass AnswerAblationSweep`: `answer_match: dict[str, dict[float, float]]`,
    `bridge_recall: dict[str, dict[float, float]]`, `delta: dict[str, dict[float, float]]`,
    `ambiguities: tuple[float, ...]`.
  - `aggregate_sweep(per_ambiguity: dict[float, dict]) -> AnswerAblationSweep` — pure
    reshape of the existing per-dial means; `delta[d][a] = am[d][a] - am["none"][a]`.
- **Commit:** `test+feat(erkgbench): ER-answer sweep aggregation`.

### Task 2 — the World-A/B verdict (pure; the load-bearing new logic)
- **Test (red):** `test_qa_answer_ablation_sweep.py` with two synthetic sweeps:
  - `test_verdict_world_a_delta_holds`: `delta_oracle` roughly flat/rising across ambiguity
    → verdict "ER converts (moat holds under ambiguity)" passes.
  - `test_verdict_world_b_delta_collapses`: `delta_oracle` decays toward 0 as ambiguity→1
    → verdict flags "delta collapses under ambiguity (reposition)".
  - `test_verdict_monotonic_per_ambiguity`: at each ambiguity `oracle ≥ goldengraph ≥
    name_only ≥ none` (reuse `tracking_verdict`'s ordering idea).
- **Impl (green):** in `answer_ablation_sweep.py`:
  - `sweep_verdict(sweep) -> list[tuple[str, bool, bool]]` (label, passed, is_hard),
    mirroring `ablation.evaluate_assertions`. Assertions:
    1. monotonic-in-ER at every ambiguity (HARD — it's the sanity check).
    2. `delta_oracle(max_amb) >= DELTA_HOLD_FRAC * delta_oracle(min_amb)` with
       `DELTA_HOLD_FRAC = 0.5` (SOFT/reporting — this is the World-A/B *finding*, not a
       regression gate; a "fail" here is the answer, not a broken build).
  - Keep `DELTA_HOLD_FRAC` a module constant with a comment tying it to the spec's decision
    criterion.
- **Commit:** `feat(erkgbench): World-A/B verdict over the ambiguity sweep`.

### Task 3 — markdown render (pure)
- **Test (red):** `test_render_sweep_md_has_table_and_verdict` — assert the rendered string
  contains a dial × ambiguity table, a `delta` row, and the verdict lines (PASS/WARN tags
  like `ablation.render_ablation_md`).
- **Impl (green):** `render_sweep_md(sweep, *, model: str) -> str` in `answer_ablation_sweep.py`.
- **Commit:** `feat(erkgbench): render the ER-answer sweep markdown`.
- **Run:** `cd packages/python/goldenmatch/benchmarks/er-kg-bench && python -m pytest
  tests/test_qa_answer_ablation_sweep.py -q` (all pure, no wheel/network).

### Task 4 — the sweep runner (wheel + budget-capped LLM), opt-in
- **Test (red):** `tests/test_qa_answer_ablation_sweep_e2e.py::test_sweep_e2e_fixed_llm` —
  `pytest.importorskip("goldengraph_native")`; a `_FixedLLM` (returns `"Answer: X"`, mirror
  `test_answer_match_ablation_e2e`); call `run_answer_ablation_sweep(seed=7, n_questions=20,
  ambiguities=(0.0, 1.0), max_hops=4, llm=_FixedLLM())`; assert an `AnswerAblationSweep`
  with both ambiguities populated for all four dials. No network.
- **Impl (green):** in `answer_ablation_sweep.py`, `run_answer_ablation_sweep(*, seed,
  n_questions, ambiguities, max_hops, llm) -> AnswerAblationSweep`: for each ambiguity →
  `generate_engineered(...)` → `GoldGraph.from_corpus` → `ablation._typ_of(g)` →
  `scorecard_llm.answer_match_ablation(corpus, g, typ_of, llm)`; collect into the
  ambiguity→dict map → `aggregate_sweep`. **One shared `llm` across the whole sweep** so a
  single cost cap spans all ambiguities (the `llm.exhausted` short-circuit already lives in
  `answer_match_ablation`'s per-question loop).
- **Commit:** `feat(erkgbench): ER-answer ablation sweep runner`.

### Task 5 — CLI + results doc
- **Impl:** `qa_e2e/run_answer_ablation_sweep.py` (mirror `run_ablation.py` / `run_scorecard.py`):
  args `--seed 7`, `--n-questions 80`, `--ambiguity-sweep 0,0.25,0.5,0.75,1.0`,
  `--max-hops 4`, `--model gpt-4o-mini`, `--max-cost-usd 5`, `--out-md`. Builds the shared
  budget-capped `_BudgetedLLM` (reuse `run_scorecard`'s tracker), calls
  `run_answer_ablation_sweep`, writes `render_sweep_md` to `--out-md` (default
  `results/RESULTS_ER_ANSWER_ABLATION.md`), and exits non-zero only on a HARD assertion
  (monotonicity) — the delta-verdict prints WARN and never gates.
- **No committed fake numbers.** The real doc is machine-generated by an opt-in run with a
  key (like `RESULTS_QA_E2E.md`); Task 5 ships the generator, not baked results.
- **Test:** none new (covered by Tasks 1-4); manual smoke `--help`.
- **Commit:** `feat(erkgbench): CLI for the ER-answer ablation sweep`.

### Task 6 (optional enhancement) — LLM-judge + entity-answerable overlay
- Only if the delta comes back within noise on `answer_match`. Add a judge-scored variant
  (`metrics.judge_prompt`/`parse_judge`) + the entity-answerable subset
  (`metrics.is_entity_answer`) as extra rows in the sweep. Defer until a first run shows the
  headline metric is ambiguous. Not required for the World-A/B call.

### Task 7 — opt-in CI (do LAST, after a first real run validates the harness)
- Add a `run_answer_ablation=true` input + job to `.github/workflows/bench-graphrag-qa.yml`
  (real `OPENAI_API_KEY`, hard `--max-cost-usd`, `workflow_dispatch` only, **NOT** `ci-required`).
  Mirror the existing qa-e2e opt-in posture; upload/commit `RESULTS_ER_ANSWER_ABLATION.md`.

## Decisions (resolving the spec's open questions)
1. **Judge:** headline = `answer_match` (what the primitive computes); judge is Task 6 (opt).
2. **K samples/question:** K=1 for the first run; revisit only if the delta is within noise.
3. **Ambiguity grid:** the RESULTS_QA_E2E five points `0,0.25,0.5,0.75,1.0` (comparable).
4. **CI:** script-first (Tasks 1-5); the `workflow_dispatch` job is Task 7, after a validating run.

## Definition of done
- Tasks 1-5 merged; `python -m pytest tests/test_qa_answer_ablation_sweep*.py -q` green
  (pure tests always; the e2e test runs when the `goldengraph_native` wheel is present).
- One real opt-in run produces `results/RESULTS_ER_ANSWER_ABLATION.md` with the dial ×
  ambiguity table + delta curves + the World-A/B verdict — the number that decides the
  positioning question.

## Out of scope (carried from the spec)
Full 4-engine head-to-head re-run; the real-world-corpus swap (2WikiMultiHopQA — a separate
slice); the positioning decision itself (this produces the number).

## Reviewer pass
Run `writing-plans` reviewer + a `spec-document-reviewer` diff of this plan vs the spec's
§6 deliverables; reconcile the "new module → thin sweep layer" delta noted above into the
spec if it should read as built-upon-existing.
