# goldengraph SP6 — head-to-head eval implementation plan

> **For agentic workers:** use superpowers:subagent-driven-development or
> executing-plans. Steps use `- [ ]` checkboxes. TDD: failing test → minimal impl →
> run → commit.

**Goal:** Add goldengraph to ER-KG-Bench (engine row) and a deterministic
fact-completeness eval that measures resolution's downstream win, per
`docs/superpowers/specs/2026-06-20-goldengraph-sp6-headtohead-eval-design.md`.

**Architecture:** Reuse ER-KG-Bench (`packages/python/goldenmatch/benchmarks/er-kg-bench/`).
Half 1 = a new adapter mirroring `goldenmatch_adapter.py`. Half 2 = extend the
demo KG model with facts + a `qa_eval.py` that scores fact co-location (resolved
vs exact-match floor) via the `demo/narrative.under_merge_answer` reachability
model. New informational `bench-er-kg.yml` lane (deterministic gate; LLM +
real-framework opt-in).

**Tech stack:** Python (polars, goldenmatch, goldengraph), the existing
`erkgbench` harness, pytest, GitHub Actions.

**Paths** (all under `packages/python/goldenmatch/benchmarks/er-kg-bench/` unless noted):
`erkgbench/adapters/base.py` (Record/AdapterBase — read-only), `erkgbench/adapters/goldenmatch_adapter.py`
(mirror), `erkgbench/run.py` (adapter list ~line 120), `demo/kg.py` (Node/build_kg),
`demo/narrative.py` (under_merge_answer), `demo/test_demo.py` (must stay green),
`erkgbench/metrics.py` (read-only), goldengraph pkg `packages/python/goldengraph/goldengraph/`
(resolve API).

---

### Task 1: `Node.facts` + `build_kg` unions facts
**Files:** Modify `demo/kg.py`; Test `demo/test_demo.py` (add case).

- [ ] **Step 1 — failing test.** In `demo/test_demo.py`, add `test_build_kg_unions_facts`: build a 2-record cluster whose records carry facts `["f1"]` and `["f2"]`; assert the resulting `Node.facts == ("f1","f2")` (sorted, deduped) and that an empty-facts build yields `Node.facts == ()`.
- [ ] **Step 2 — run, expect fail** (`Node` has no `facts`): `cd packages/python/goldenmatch/benchmarks/er-kg-bench && python -m pytest demo/test_demo.py -q`.
- [ ] **Step 3 — implement.** Add `facts: tuple[str, ...] = ()` to the `Node` dataclass (frozen; default keeps existing constructions valid). Add a `facts: dict[int, list[str]] | None = None` param to `build_kg`; when present, set each node's `facts = tuple(sorted({f for i in idxs for f in facts.get(i, [])}))`. Default `None` → `()` (existing callers + `demo/test_demo.py` unchanged).
- [ ] **Step 4 — run, expect pass** (new test + all existing demo tests green).
- [ ] **Step 5 — commit:** `feat(er-kg-bench): Node.facts + build_kg fact union`.

### Task 2 (Half 1): goldengraph adapter + parity lock
**Files:** Create `erkgbench/adapters/goldengraph_adapter.py`; Modify `erkgbench/run.py`; Test `erkgbench/tests/test_goldengraph_adapter.py`.

- [ ] **Step 1 — failing test.** `test_goldengraph_partition_parity`: build ~6 `Record`s (two clear dup groups), run `GoldenGraphAdapter().resolve(records)` AND `goldenmatch.dedupe_df` on the **same fields goldengraph.resolve uses** — `{name, typ}` (NOT context: `goldengraph/resolve.py::resolve` builds a `{name, typ}` frame and calls `gm.dedupe_df`). Assert the two partitions (frozensets of frozensets over indices) are EQUAL. Parity holds BY CONSTRUCTION because goldengraph.resolve wraps `gm.dedupe_df` — the test locks that the engine wiring doesn't drift.
- [ ] **Step 2 — run, expect fail** (adapter missing).
- [ ] **Step 3 — implement.** `GoldenGraphAdapter(AdapterBase)` with `name="goldengraph"`, `fidelity="real"`, `deterministic=True`, `defaults="goldengraph engine: resolve (Provided mode) -> store; wraps goldenmatch dedupe_df on name+type"`. In `resolve(records)`: order by `.index`; build `goldengraph.Mention`s from `(mention, entity_type)`; call `goldengraph.resolve(mentions) -> list[ResolvedEntity]`; group record indices by the resolved entity (each `ResolvedEntity` carries its member mention indices) → return clusters in record-index space. If goldengraph isn't importable, raise a clear ImportError (the runner records it as an error row, never fatal). **NOTE:** this is the Provided-mode path (= goldenmatch resolver). The goldengraph-core NATIVE resolver (`ResolutionMode::Native`, score-core/graph-core) is a DIFFERENT algorithm whose quality is NOT parity-locked to gm.dedupe_df — add it as a separate `goldengraph(native-kernel)` row only as a follow-up with its own (non-parity) measurement, not in this task.
- [ ] **Step 4 — register.** In `run.py`, import `GoldenGraphAdapter` and append `GoldenGraphAdapter()` to the `adapters = [...]` list (~line 120), after the goldenmatch rows.
- [ ] **Step 5 — run, expect pass:** `python -m pytest erkgbench/tests/test_goldengraph_adapter.py -q`.
- [ ] **Step 6 — commit:** `feat(er-kg-bench): goldengraph engine adapter + parity lock`.

### Task 3 (Half 2): authored QA corpus + loader
**Files:** Create `dataset/qa.jsonl`; Create `erkgbench/qa_loader.py`; Test `erkgbench/tests/test_qa_loader.py`.

- [ ] **Step 1 — failing test.** `test_qa_loader`: load `qa.jsonl`; assert ≥8 items; each has `qa_id, entity_id, question, seed_surface, gold_facts (non-empty list), gold_answer`; every `entity_id` exists in `dataset/records.csv`; every `seed_surface` matches a `mention` of that entity_id in records.csv.
- [ ] **Step 2 — run, expect fail** (no qa.jsonl / loader).
- [ ] **Step 3 — author the corpus.** Write `dataset/qa.jsonl`: pick ≥8 entities from `records.csv` with multi-surface / cross-document structure (abbreviation, xling, xdoc, suffix classes). For each, attach 2-4 distinct `gold_facts` to DIFFERENT surface forms (record indices) of that entity, a `question` whose answer needs all of them, a `seed_surface` (one of the surface forms), and a `gold_answer`. Add `dataset/qa_facts.csv` (or inline in qa.jsonl) mapping `record_id -> fact` so a fact is attached to a specific surface-form record. Implement `qa_loader.py::load_qa() -> list[QAItem]` + `load_qa_facts() -> dict[int, list[str]]`.
- [ ] **Step 4 — run, expect pass.**
- [ ] **Step 5 — commit:** `feat(er-kg-bench): authored QA fact corpus + loader`.

### Task 4 (Half 2): fact-completeness eval (the deterministic gate)
**Files:** Create `erkgbench/qa_eval.py`; Test `erkgbench/tests/test_qa_eval.py`.

- [ ] **Step 1 — failing test.** `test_completeness_resolved_vs_split` on a tiny in-memory fixture: one entity, 2 surface forms, facts `["a"]` on form-1 and `["b"]` on form-2, a QA item with `gold_facts=["a","b"]`, `seed_surface=form-1`. RESOLVED partition `[[0,1]]` → completeness == 1.0. SPLIT partition `[[0],[1]]` → completeness == 0.5 (seed lands on form-1's node only). Assert both.
- [ ] **Step 2 — run, expect fail.**
- [ ] **Step 3 — implement `qa_eval.py`.**
  - `engine_completeness(partition, qa_items, facts_by_record) -> dict` : for each QA item, `build_kg(partition, mentions, types, contexts, facts=facts_by_record)`; select the **landed cluster** using the SAME selection `under_merge_answer` uses — `landed = next(c for c in partition if any(mentions[i] == seed_surface for i in c), [])` — then `retrieved = set(union of facts over landed's records)` (note: `under_merge_answer` returns *name*-reachability; SP6 applies its landed-cluster selection to *facts*, so factor the selection into a shared helper or replicate the one-liner — do NOT expect under_merge_answer to return facts). `completeness = |gold ∩ retrieved| / |gold|`. Return mean + per-failure-class breakdown.
  - `run_qa_eval(adapters, records, qa_items, facts_by_record) -> rows` : run each adapter's `resolve` → partition → `engine_completeness`. Use the landed-cluster selection above (do NOT use `kg.retrieve` — see spec).
- [ ] **Step 4 — run, expect pass.**
- [ ] **Step 5 — commit:** `feat(er-kg-bench): deterministic fact-completeness eval`.

### Task 5 (Half 2): opt-in LLM-judged correctness
**Files:** Modify `erkgbench/qa_eval.py`; Test `erkgbench/tests/test_qa_eval.py` (stub).

- [ ] **Step 1 — failing test.** `test_llm_judge_plumbing`: inject a stub `answer_fn` (returns a fixed string) + stub `judge_fn` (returns 1 if gold_facts substring-present else 0); assert `run_qa_eval(..., judge=stub)` adds a `correctness` field per row and the stub was called once per QA item. Assert plumbing, NOT accuracy (goldenmatch-kg posture).
- [ ] **Step 2 — run, expect fail.**
- [ ] **Step 3 — implement.** Optional `judge` arg; when set, build an answer from the retrieved facts via `demo/agent.py::answer` (inject `llm_fn`) and score via `judge`. Default `None` → skip (deterministic path unchanged). Real OpenAI judge/answerer wired only when `OPENAI_API_KEY` present (lazy).
- [ ] **Step 4 — run, expect pass.**
- [ ] **Step 5 — commit:** `feat(er-kg-bench): opt-in LLM-judged answer correctness`.

### Task 6 (Half 2): RESULTS_QA.md + a CLI entrypoint
**Files:** Modify `erkgbench/qa_eval.py` (add `main`/render); Create `results/RESULTS_QA.md` (generated); Test `erkgbench/tests/test_qa_eval.py` (render).

- [ ] **Step 1 — failing test.** `test_render_results_qa`: given rows, `render_results_qa(rows)` returns markdown with a per-engine completeness column, a per-failure-class breakdown, and the authored/synthetic honesty disclaimer line.
- [ ] **Step 2 — run, expect fail.**
- [ ] **Step 3 — implement** `render_results_qa` + a `python -m erkgbench.qa_eval` entrypoint (loads corpus + qa, runs goldengraph + exact-match-floor adapters, writes `results/RESULTS_QA.md`). Include the disclaimer ("authored synthetic QA layer; measures fact co-location, not real-world QA accuracy; the (ER_accuracy)^hops exponent is not measured").
- [ ] **Step 4 — run, expect pass;** then run the entrypoint to generate `results/RESULTS_QA.md` and commit it.
- [ ] **Step 5 — commit:** `feat(er-kg-bench): RESULTS_QA.md + qa_eval entrypoint`.

### Task 7 (Half 2, opt-in): real-framework baseline (best-effort)
**Files:** Modify `erkgbench/qa_eval.py`; reuse `erkgbench/adapters/real/`.

- [ ] **Step 1 — failing test.** `test_framework_baseline_skips_when_absent`: with the framework deps absent, `run_qa_eval(..., with_frameworks=True)` records a `skipped` row, never raises.
- [ ] **Step 2 — run, expect fail.**
- [ ] **Step 3 — implement.** Behind `with_frameworks`, build a real neo4j-graphrag and/or LlamaIndex KG (prefer the `goldenmatch-kg` shims if importable; else stand the framework up directly per the spec) and score completeness. Import-guarded → `skipped` if deps missing. NOT on the gate.
- [ ] **Step 4 — run, expect pass.**
- [ ] **Step 5 — commit:** `feat(er-kg-bench): opt-in real-framework completeness baseline`.

### Task 8: CI lane `bench-er-kg.yml`
**Files:** Create `.github/workflows/bench-er-kg.yml` (repo root).

- [ ] **Step 1 — write the workflow.** `workflow_dispatch` (inputs `with_llm`, `with_frameworks`), `permissions: contents: read`, informational (NOT in `ci-required`). **gate job** (always, `ubuntu-latest`): checkout → setup-python 3.12 → build goldengraph native (`maturin develop` in the goldengraph-native crate into a venv) → install goldenmatch + goldengraph → run Half-1 scoring (goldengraph adapter via `erkgbench/run.py` or a targeted invocation) + Half-2 `python -m erkgbench.qa_eval` (deterministic) → **assert** goldengraph mean completeness − exact-match-floor mean completeness ≥ a concrete margin (e.g. 0.25) and the parity test passes. **opt-in steps** gated on inputs: `with_llm` (needs `OPENAI_API_KEY` secret), `with_frameworks` (isolated venv, best-effort).
- [ ] **Step 2 — validate YAML** locally (`python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/bench-er-kg.yml'))"`).
- [ ] **Step 3 — commit:** `ci(er-kg-bench): bench-er-kg lane (deterministic gate + opt-in llm/frameworks)`.

### Finish
- [ ] Run the full er-kg-bench test set locally: `python -m pytest demo/ erkgbench/tests/ -q`.
- [ ] Push; dispatch `bench-er-kg.yml` on the branch; confirm the gate job green (it builds native + runs the deterministic eval — the surface's validator).
- [ ] PR; arm auto-merge after the lane is green; use superpowers:finishing-a-development-branch.

## Notes / risks
- **Determinism:** the gate is deterministic (fixed corpus + authored facts + exact set membership + deterministic resolver). goldenmatch auto-config has mild EM-order non-determinism — the parity test compares partitions on a small frame where the result is stable; if it flakes, pin via an explicit small config or assert cluster-equivalence with a tolerance, and note it (the goldenmatch_adapter already documents this).
- **goldengraph resolve entrypoint:** confirm the exact call at implementation time (`goldengraph` pkg `resolve` vs `goldengraph-native` build_graph Provided/native mode); the adapter just needs records→partition.
- **Don't fork the KG model** — extend `demo/kg.py::Node`; keep `demo/test_demo.py` green.
- **Local Windows:** native build flakiness is documented; the CI gate is the validator of record. Run the pure-Python tests (Tasks 1,3,4,5,6,7 logic) locally; Task 2 parity + the gate run in CI.
