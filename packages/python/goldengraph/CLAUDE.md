# GoldenGraph — Claude notes

Own-your-KG knowledge-graph engine: LLM extraction → goldenmatch entity
resolution → a durable bi-temporal store. The compute primitives (build_graph /
neighborhood / seeds_by_name / communities + the bi-temporal store) live in the
pyo3-free Rust `goldengraph-core` crate; the Python package is the orchestration
layer (extract / embed / route / answer / synthesize) on top.

## Workspace posture
- **EXCLUDED from the uv workspace** (root `pyproject.toml` `[tool.uv.workspace].exclude`) — it depends on the maturin-built `goldengraph-native` engine wheel + optional LLM extras. Standalone; its full suite runs in `.github/workflows/goldengraph-pipeline.yml`.
- Its Rust surfaces are still gated in the root `ci.yml`: the `goldengraph_wasm` lane (edge/WASM drift guard) and the `goldengraph_native` lane (the engine parity gate, in `ci-required`).

## Rust is the reference — cross-surface parity (2026-07-07)
GoldenGraph is **native-authoritative**: `goldengraph-core` is the reference impl,
and the SAME kernel runs on every surface over one shared **JSON boundary**
(`(json, args...) -> json`) — so all surfaces are byte-identical by construction:
- **Python native** — `goldengraph-native` (pyo3). Exposes the ergonomic
  `PyGraph`/`PyStore` pyclasses **and** the 7 JSON-boundary `wrap_pyfunction!`
  symbols (`build_graph_json`, `neighborhood_json`, `seeds_by_name_json`,
  `communities_json`, `store_append_json`, `store_as_of_json`,
  `store_history_json`) that mirror the wasm `*_impl` exactly.
- **Edge / TS / WASM** — `goldengraph-wasm` + `packages/typescript/goldengraph`.
- **C-ABI** — `goldengraph-cabi` (`gg_abi_version` + `no_mangle` externs).

### The gate + the single-oracle fixture
- `goldengraph/core/_native_loader.py` is the `GOLDENGRAPH_NATIVE` gate (`auto`/`0`/`1`, `_has_symbol`, discovery `goldengraph._native` → `goldengraph_native._native` → None) — mirrors the sibling loaders. `native_enabled(component)` reads `_COMPONENT_SYMBOLS` (the 7 JSON symbols).
- **No pure-Python fallback for these primitives** — the store/resolution engine is Rust-only. `GOLDENGRAPH_NATIVE=0` force-disables (callers with no fallback raise a clear error rather than silently degrade); `=1` requires native (CI parity lane).
- **One oracle, no second drift surface:** `packages/typescript/goldengraph/tests/parity/fixtures/goldengraph/queries.json` (9 cases, all 7 ops) is generated from the host boundary by `goldengraph-wasm/examples/gen_parity_fixtures.rs` and drift-guarded by the `fixture_drift` CI job. Both the TS parity test (`goldengraph-wasm.parity.test.ts`) and the Python parity test (`tests/test_native_parity.py`) read **that same file** — the Python test anchors to it via `Path(__file__).parents[4]` so it resolves from either CWD. Do NOT copy the fixture into the Python package (that would be a second thing to drift).

### `goldengraph_native` CI lane (in `ci-required`)
Builds the ext via `scripts/build_goldengraph_native.py` (cargo `--release` →
`goldengraph/_native.abi3.so`, gitignored) + `cargo clippy`/`test` on the core,
then runs the parity suite with `GOLDENGRAPH_NATIVE=1`. Because the engine is
native-only, this lane is the ONLY correctness signal for the store/resolution
path — hence it is a **blocking** gate, unlike the advisory `infermap_native` /
`analysis_native` lanes.

### Gotchas
- The abi3 init symbol is `PyInit__native` (pymodule name `_native`), so a
  file-path import MUST load the `.so` under the module name `_native` (the parity
  test does this to bypass `goldengraph/__init__` and its heavy deps like numpy).
- `goldengraph-native` is a **standalone cargo workspace** (empty `[workspace]`) —
  the `rust` job's `cargo test --workspace` never builds it; the `goldengraph_native`
  lane is what compiles + parity-checks it.
- Graph entity/edge ordering can fall out of hash-map order, so parity compares
  canonicalize both sides (entities by id, edges by subj/pred/obj, members/
  surface_names/source_refs sorted). Store snapshots ARE deterministic (compared raw).

## Agent surfaces (MCP / A2A / CLI) — deferred by design
GoldenGraph ships **no MCP server, no A2A AgentCard, and no CLI** today, and is
therefore **not in the `api_parity` gate** (no `parity/goldengraph.yaml`; absent
from `scripts/emit_ts_surface.mjs`). This is a *sequenced* deferral, not an
oversight — verified 2026-07-07:
- No `goldengraph/mcp/` module, no `server.json`, no MCP/FastMCP dep, no
  `[project.scripts]` entry point, no `Dockerfile.mcp` / `railway*.json` deploy
  scaffold. Not in `publish-mcp.yml`, the MCP Registry, or Smithery.
- Not one of the 4 A2A packages (goldenmatch/goldencheck/goldenflow/goldenpipe);
  the TS package is edge/WASM-only (no `src/node/` agent surface).
- **Rollout condition:** `docs/superpowers/specs/2026-06-20-goldengraph-sp4b-pipeline-design.md`
  lists "Publishing `goldengraph` to PyPI / MCP roster" as a later rollout "once
  the pipeline is real". GoldenGraph is pre-1.0 (`v0.1.0`), not yet on PyPI, and
  still building out the extract→resolve→store→answer pipeline (SP4b/SP4c).

When that bar is hit, stand up the agent surfaces together: a `goldengraph/mcp/`
FastMCP server (`build`/`ask`/`neighborhood`/`communities`) + `server.json` with
the first-line `mcp-name:` marker (mirror the infermap MCP layout), then add
`parity/goldengraph.yaml` + the `emit_ts_surface.mjs` roster entry so MCP/CLI/A2A
stay Python↔TS in lockstep. (Do NOT wire `api_parity` before a real surface
exists — the gate has nothing to compare.)

## Single gated engine entry point (2026-07-20)
Both runtime call sites now go through `goldengraph.core._native_loader` instead
of hard-importing the wheel ad-hoc, so the `GOLDENGRAPH_NATIVE=0/1` contract
governs the WHOLE engine, not just the JSON parity surface:
- `graph.py::_new_store` → `_native_loader.new_store()` (builds `PyStore` via
  `native_module()`; also picks up the in-tree build, which the old
  `from goldengraph_native import _native` never did). The test `store` fixture
  (`tests/conftest.py`) goes through it too.
- `profile.py::_engine` → `_native_loader.profile_resolve_json()` (the separate
  `goldenprofile-native` wheel, still lazily imported so importing the loader
  never requires it — but now under the same gate).
Both loader entry points raise a clear, actionable error on `=0` (force-disable:
no pure-Python fallback exists) and on a missing/unbuilt engine, rather than an
opaque `ImportError` at the call site. Gate logic is unit-tested wheel-free in
`tests/test_native_loader.py` (loads the loader by file path to dodge
`goldengraph/__init__`'s heavy deps, mirroring `test_native_parity`).

## Template-free NL multi-hop routing (2026-07-21)
`trace_chain` (answer.py) is the deterministic, LLM-free multi-hop walk, but it
only fired when a question matched the engineered `_CHAIN_RE` template ("Starting
from X, follow the relation R1, then R2."). Real questions ("Who is married to the
person who directed Inception?") fell through to LLM synthesis over the retrieved
ball — the diagnosed #1 answer-quality gap (a ball that CONTAINS the chain,
bridge-recall ~1.0, still answered only ~0.275; synthesis-given-gold-chain is 1.0,
so the loss is path-selection, not reasoning; the two cheap fixes — topology prune,
query-name embedding — were already refuted, see `results/RESULTS_PATH_AWARE_RETRIEVAL.md`).
- **`route._extract_nl_chain_slots`** recovers `(anchor, relation_chain)` from free
  NL, grounded in the slice's own vocab: anchor = the longest stored ENTITY NAME in
  the question; relations = PREDICATE ids whose salient token appears (bridged to
  the question's noun form by a `_stem_match` shared-≥5-char-stem rule, so
  "director"→"directed_by", "location"→"located_in"). Wired into `classify_query`
  for MULTI_HOP **and** LOOKUP intents (needs `entity_names`, threaded from
  `answer._slice_entity_names`); when the vocab is absent it's a no-op (back-compat).
- **Multiplicity, not a set:** each content word maps to one predicate occurrence,
  so a repeated relation ("the employer of the employer of X") yields a repeated
  hop. Dedup-to-set was the whole accuracy gap (repeat-relation chains 0%→97.6%).
- **Order is a HINT, the graph validates it:** the extracted order is proximity-to-
  anchor. `answer._trace_chain_any_order` first walks the HINT order and trusts it
  when it completes (that is the reading the question expressed); only if the hint
  fails does it try the other permutations, and then it returns a result ONLY when
  exactly one fallback order completes (distinct fallback terminals ⇒ ambiguous ⇒
  abstain to None). Requiring uniqueness across ALL orders including the hint was
  measured 96.8%→29.8% (the dense graph makes many orders complete differently and
  the hint is the correct one). `QueryProfile.chain_ordered` distinguishes the
  authoritative template order (single walk) from the NL hint (permute).
- **Conservative by construction — never worse than status quo:** fires only with a
  grounded anchor + ≥1 grounded predicate; a COMPLETENESS GUARD abstains when an
  unmapped content word sits before an "of"/"by" relation marker (an ungrounded hop
  like the pure synonym "spouse"→"married_to"), because a truncated chain would
  complete early and return a WRONG intermediate node (the None-fallthrough can't
  catch that). Abstaining routes to today's retrieval+synthesis path. Pure noun
  synonyms with no shared stem are the documented boundary (needs the embedding /
  `LLMQueryClassifier` tier).
- **Measured (engineered corpus, gold chains, StubGraph, LLM-FREE):** NL phrasings
  reach **96.8%** answer accuracy — parity with the engineered-template ceiling
  (96.8%) — up from **0%** LLM-free before (they fell to ~0.15–0.28 paid synthesis).
  By hop: 2-hop 100%, 3-hop 93%, 4-hop 97%. Tests: `tests/test_nl_chain.py`
  (wheel-free; fires, safe abstentions, ordering, multiplicity, end-to-end `ask`).

## NL chain routing — semantic synonym bridge (2026-07-21)
Follow-up to the template-free NL routing (#1966): the lexical stem rule grounds
morphological variants ("director"->"directed_by") but not pure synonyms
("spouse"->"married_to", no shared characters), which hit the completeness guard
and abstained. `route._extract_nl_chain_slots(..., embedder=)` now bridges exactly
those guard-triggering words (uncovered AND before an "of"/"by" marker) by embedding
cosine against the predicate phrases: `_embed_bridge_predicate` picks the closest
predicate above `GOLDENGRAPH_NL_EMBED_BRIDGE_MIN` (default 0.55). Scoped to the
guard words only (not every content word) so it lifts the synonym boundary without
a spurious-match surface; `embedder is None` is byte-identical to #1966 (all prior
tests unchanged). `embedder` threads `ask` -> `resolve_profile` -> `classify_query`
(the embedder `ask` already carries). Needs a SEMANTIC embedder to bridge true
synonyms -- the no-torch char-ngram embedder only shares the morphological cases the
stem rule already covers. Tests: `tests/test_nl_chain.py` (`_SynEmbedder` stub:
spouse~married_to fires; an orthogonal word stays below the floor and abstains).
NOTE the reachability gotcha (NOW RESOLVED, see next section): the bench's goldengraph
engine runs `ask` in `mode="local"` by default (`engines/goldengraph.py`), and the NL
routing (chain extraction) originally only ran in `mode="auto"`.

## NL chain routing is the DEFAULT local/hybrid path (2026-07-21)
Closes the reachability gotcha above: the template-free chain walk now fires for
`mode="local"` and `mode="hybrid"` BEFORE synthesis, so the routing win reaches the
DEFAULT answer path (the bench + most callers run `mode="local"`), not just `mode="auto"`.
- **`answer.ask`** — factored the auto path's routing into two shared helpers
  (`_resolve_and_plan` = resolve profile + schema-canon + plan; `_chain_answer_from_profile`
  = the ordered/any-order walk) so `auto` and the new local/hybrid attempt share ONE
  implementation. In the local/hybrid branch, before seed retrieval, it runs the chain
  attempt and returns on a completed walk; a None (no chain plan, or the walk hit a
  missing edge) falls through to today's retrieval+synthesis, unchanged. Skipped when we
  arrived via `auto` (it already tried the chain).
- **Gate `GOLDENGRAPH_QA_LOCAL_CHAIN` (default ON; `=0`/`false` restores the pre-change
  pure-synthesis local/hybrid path, byte-identical)** — `_local_chain_enabled()`. Off is
  the baseline arm of the A/B. A non-chain local query (no groundable relation) is never
  hijacked (plan.mode != "chain" -> falls through), so this only ADDS answers, never
  changes an existing synthesis answer.
- **Same-run local-vs-auto A/B** (`benchmarks/er-kg-bench/erkgbench/qa_e2e`):
  `harness.run_engine_ab` builds the KG ONCE and answers every question under BOTH modes
  against the identical graph (removing build variance), scoring per-arm via the
  single-sourced `_score_question`/`_build_scorecard` (the same metrics `run_engine` uses).
  Entry `run_local_vs_auto_ab.py` (`--self-test` CI-validates the plumbing); the
  goldengraph engine's `answer(handle, q, mode=)` gained a per-call mode override.
  Dispatchable via `bench-graphrag-qa.yml` mode `local_vs_auto_ab`. Tests:
  `tests/test_nl_chain.py` (mode=local/hybrid route to the LLM-free chain; gate-off +
  non-chain query fall through to synthesis) + `tests/test_qa_local_vs_auto_ab.py`
  (one shared build, aligned arms, delta).

### Post-#1969 hardening (2026-07-21, review-driven)
Three fixes on top of the semantic bridge, all in `_extract_nl_chain_slots`:
- **Synonym-ONLY hops now bridge.** The `if not hits: return None, None` early return
  ran BEFORE the bridge, so a 1-hop query whose sole relation word is a pure synonym
  ("Who is the spouse of Christopher Nolan?") had empty lexical `hits` and abstained
  even with a valid embedder — contradicting the tier's intent. The early return moved
  AFTER the bridge (now a post-guard `if not hits`), so the bridge runs with zero
  lexical hits; the no-embedder path stays byte-identical (empty hits still abstains).
- **The WHOLE bridge is exception-wrapped, not just `embed()`.** A malformed-but-right-
  length embedder return (scalar / `None` / non-numeric entries) raises inside
  `_cos()`/`_embed_bridge_predicate` during the similarity loop, NOT at `embed()` time.
  The `try/except` now spans the embed calls + length check + cosine loop, so any
  vector-shape failure degrades to "no bridge -> completeness guard abstains" (the
  advertised failure mode) instead of propagating and crashing the query.
- **`GOLDENGRAPH_NL_EMBED_BRIDGE_MIN` env parsing is test-locked** (`_embed_bridge_min`):
  non-float and non-finite (`nan`/`inf`) fall back to the 0.55 default; any finite value
  clamps into `[0, 1]` — so a misconfigured env var can neither silently disable the
  bridge (`nan`) nor make it bind anything (a negative floor).
Tests: `tests/test_nl_chain.py` (synonym-only fires with embedder / abstains without;
malformed-embedder degrades; env parse+clamp).

## Anti-shatter cross-doc linking is DEFAULT ON (2026-07-21)
The "multi-hop shatter" — the same entity mentioned in two documents living as two
un-merged graph nodes, so a multi-hop question can't traverse across docs — was the #1
addressable QA-failure class (measured 28% of MuSiQue N=100 misses; support_recall only
0.59). ROOT CAUSE was NOT a missing capability: the anti-shatter stack existed and was
well-built, but it was **off by default** (`GOLDENGRAPH_CROSS_DOC_LINK`/`PROFILE_LINK`
unset → cross-doc merge fell back to an exact `record_key`, which the docstring itself
says type/case jitter defeats ~97.6% of the time). The bench even *built the goldenprofile
wheel for the matcher and never set the flag.*
- **`resolve._key_payload` default is now `name_ci_type`** (case-folded name + COARSE
  canonical type; homograph-safe). `GOLDENGRAPH_XDOC_KEY=exact` restores the legacy
  verbatim `(name, typ)` key; `name`/`name_ci` are the more/less aggressive relaxations.
- **`ingest._cross_doc_link_enabled` default 0→1** (`GOLDENGRAPH_CROSS_DOC_LINK=0` opts out).
- **`ingest._profile_link_enabled` default `auto`** — ON when the separate
  `goldenprofile-native` wheel is importable (cached `_goldenprofile_available()` probe),
  else OFF so the linker degrades to the embedding-cosine matcher instead of hard-failing
  an install lacking the wheel. `=1` forces on (raises later if truly absent), `=0` off.
- **MEASURED (MuSiQue N=100, trace A/B, run 29851193442 vs the linking-off baseline):**
  RETRIEVAL-shatter misses **27→6 (−78%)**; shatter-probe SCORING-miss under-merges
  **18→4**; **support_recall 0.59→0.85**; entity-subset answer_match 0.231→0.277 (+20% rel).
  Overall answer_match barely moved (0.17→0.18) because fixing retrieval SHIFTED the
  bottleneck to synthesis (now the #1 bucket, ~36%: answer is in the ball, synthesizer
  picks the wrong node) + extraction of non-entity answers. No precision regression.
- Local suite runs the DEGRADED path (no `goldenprofile-native` wheel → profile-link
  auto-off → embedding matcher); the full-stack path is exercised in the `goldengraph_native`
  / pipeline CI lanes (which build the wheel) and was proven end-to-end by the bench A/B.
  NEXT lever (chosen): probe the synthesis-precision gap (answer retrieved, answered wrong).

## Self-consistency voting MEASURED (small, opt-in) — same-graph env-A/B (2026-07-22)
Self-consistency voting (`GOLDENGRAPH_SYNTH_SAMPLES`, `synthesize.complete_many` +
`_vote_answer` majority vote) was built but default-off (=1). To decide default-on we
needed a CONFOUND-FREE measurement: `head_to_head` rebuilds the KG each run, and
stochastic-extraction build variance swings even a retrieval-only metric
(`support_recall` +-0.26 between rebuilds of the SAME corpus) — swamping a
downstream-only change. The instrument is `run_engine_ab_env`
(`benchmarks/er-kg-bench/erkgbench/qa_e2e/harness.py`): build the KG ONCE, answer every
question under N answer-time env-configs on the IDENTICAL graph, so the metric deltas
are purely the knob's effect. Dispatch via `bench-graphrag-qa.yml` mode `env_ab`,
`ab_env=NAME:v1,v2`.
- **MEASURED (MuSiQue N=100, identical graph, run 29876692154):** =1 -> =5 lifts every
  quality metric ~+6% RELATIVE but tiny ABSOLUTE — `answer_match` 0.1700->0.1800
  (+0.010), `answer_match_entity` 0.2462->0.2615 (+0.0153), `token_f1` 0.2210->0.2334
  (+0.0124). **CONTROL: `support_recall` 0.8292 == 0.8292 (delta 0.0000)** across arms —
  the proof the graph was truly shared (voting can't touch retrieval); this is what a
  build-variance run CANNOT give you. Cost: ~5x synthesis (5 completions/answer).
- **DECISION: keep `SYNTH_SAMPLES=1` default (opt-in).** The lift is REAL (clean,
  same-direction on all three quality metrics, control passed) but too small to justify
  ~5x synthesis cost as a ZERO-CONFIG default. `=5` is a documented, measured quality
  knob. The bottleneck is synthesis PRECISION (the ~34-36% answer-in-ball-wrong-node
  bucket), which voting barely dents — the real lever is a better synthesizer
  (node-selection / prompt), not more samples of the same one.

## Synthesis node-disambiguation is DEFAULT ON (`GOLDENGRAPH_SYNTH_SELECT`, 2026-07-22)
The synthesis-PRECISION lever the voting note points at. Mining the confound-free env-A/B
(#168) localized the synthesis miss as WRONG-NODE selection, not reasoning: on entity
questions the model returns a plausible NEIGHBOR of the answer — the containing GROUP
instead of the member (Karen Fairchild -> Little Big Town), the famous adjacent PERSON
instead of the body (Politburo -> Stalin), a related EVENT instead of the thing
(SuperSonics -> 1950 NBA draft), the wrong same-type SIBLING (Josh Radnor -> Bob Saget).
The prior `_LOCAL_PROMPT` only guarded "don't answer with the entity you HELD going into
the final hop"; it did NOT guard "don't answer with a plausible neighbor".
- **`synthesize._SELECT_PREAMBLE`** (inserted before the answer clause by `_local_prompt()`
  when `_select_enabled()`) forces an explicit disambiguation: state the KIND of thing the
  question asks for, enumerate candidate answer nodes with types, then pick the type-matching
  FAR-END node — not the most-famous neighbor, not the group when a member is asked, not a
  related event when the thing is asked. Composes with `GOLDENGRAPH_LITERAL_ATTRS`.
- **MEASURED default-on (same-graph env-A/B run 29888086667, MuSiQue N=100, SELECT 0 vs 1
  on the IDENTICAL graph; `support_recall` 0.8417 == 0.8417 across arms = the control that
  proves retrieval was untouched):** entity-subset answer_match **0.2462 -> 0.2923 (+18.7%
  rel)**, answer_match 0.18 -> 0.20, token_f1 0.224 -> 0.263 — all up, same direction, at
  **~no extra cost** (one call with a longer prompt; total $10.88 ~= the SELECT-off $10.38).
  Contrast voting (=5): +0.015 entity at ~5x cost. SELECT is ~3x the lift at ~1/5 the cost —
  which is why it ships DEFAULT ON while voting stays opt-in.
- **`GOLDENGRAPH_SYNTH_SELECT=0` (or `false`/'') restores the pre-clause prompt
  byte-identical** (`_local_prompt()` == `_LOCAL_PROMPT`; locked by
  `tests/test_synthesis_select.py`). NEXT: broader-N / multi-seed confirmation. (Whether the
  same type-check helps the HYBRID path was tested and REFUTED — see "SELECT does NOT transfer
  to the hybrid path" below; `_HYBRID_PROMPT` stays unchanged.)

## Hybrid synthesis is the DEFAULT answer mode (2026-07-22) — the BIG lever
The synthesis-precision follow-through. The KG is a LOSSY intermediate — the extracted
triples drop the source-text fidelity (dates/numbers/phrases + exact context) that plain
text-RAG keeps. `mode="hybrid"` layers the raw retrieved PASSAGES back as ground truth
with the graph as a cross-passage multi-hop map (`synthesize_hybrid`), freeing the answer
from the entity-only constraint. It was built but default-off; the confound-free same-graph
env-A/B settled it.
- **MEASURED (run 29932330468, MuSiQue N=100, `GOLDENGRAPH_QA_ANSWER_MODE:local,hybrid` over
  ONE hybrid build, judge ON; `support_recall` 0.8417 identical across arms = the control):
  answer_match 0.16→0.43 (+169%), entity 0.2462→0.4769 (+94%), token_f1 0.2162→0.4915,
  answer_judge 0.21→0.51 (+143%).** ~3x the quality of local synthesis, for ~no extra
  answer cost (one call). Far bigger than SELECT (+18.7%) or voting (+6%).
- **`answer.ask(mode=...)` default flipped `local`→`hybrid`.** SAFE by construction: hybrid's
  win IS the passages, and the library indexes NONE (the caller supplies a `passages`
  retriever; the bench builds one via `_PassageRetriever`). So when `passages` is None/empty,
  hybrid **falls through to the LOCAL synthesis path** — byte-identical to the prior local
  default for passage-less/zero-config callers (NOT the old free-form "(no passages
  retrieved)" degrade). `mode="local"` restores the old default explicitly. Tests:
  `tests/test_hybrid_synthesis.py` (default is hybrid; no-retriever→local; with-passages→hybrid).
- **Bench:** `engines/goldengraph.py` `GOLDENGRAPH_QA_MODE` default `local`→`hybrid` (so
  `build_kg` indexes passages + `answer` uses hybrid by default); the head_to_head baked
  `GOLDENGRAPH_QA_MODE=local` flipped to `hybrid` so the competitive bench runs goldengraph's
  new default. **All goldengraph bench jobs now `pip install polars`** — the passage retriever
  (`goldenmatch_rag._make_frame`) imports it and goldenmatch itself is polars-free, so a
  hybrid build crashed `ModuleNotFoundError: polars` until installed (local-mode never built
  the retriever, which is why it only surfaced when hybrid was first exercised).
- **Honest scoping:** a TRUE out-of-box product default needs a zero-config passage index IN
  the library (goldengraph has none today — the bench builds it). Until then hybrid-default
  helps exactly the callers who supply a retriever and is a safe no-op (local) for those who
  don't. Adding a passage store to goldengraph is the follow-up to make hybrid complete.
  `answer_judge 0.51` on the hybrid arm is a different quality tier than local's ~0.21.

## SELECT does NOT transfer to the hybrid path — measured flat, NOT shipped (2026-07-22)
Answers the open `NEXT` from the SELECT section ("whether the same type-check helps the
hybrid path"). It does not. The confound-free same-graph env-A/B (`env_ab`,
`GOLDENGRAPH_SYNTH_SELECT_HYBRID:0,1` over ONE hybrid MuSiQue N=100 build, judge on, run
`29950498168`; `support_recall` **0.8525 == 0.8525** across arms = the control, so the
measurement is clean) came back **flat-to-mixed**: `answer_match` **0.4700 → 0.4600
(−0.0100)**, `token_f1` 0.5298 → 0.5392 (+0.0094). The headline metric went slightly DOWN.
- **Why it doesn't transfer:** SELECT fixes the WRONG-NODE miss in the graph-ONLY (local)
  path, where the model has only entities to choose among. The hybrid path already hands the
  model the raw **passages** as ground truth, which disambiguate the answer directly — so the
  SELECT preamble is largely redundant there; it just lengthens the prompt and slightly
  perturbs the headline. The graph-only precision lever does **not** stack on the
  passages-primary path.
- **DECISION: not shipped.** The prototype (`_HYBRID_SELECT_PREAMBLE` + a
  `GOLDENGRAPH_SYNTH_SELECT_HYBRID` flag on `synthesize_hybrid`, PR #2041) was CLOSED, not
  merged — a flat, default-off knob is dead weight; the value is this finding. `_HYBRID_PROMPT`
  stays unchanged. If a future corpus/model resurfaces a graph-only precision gap under hybrid,
  the env-A/B here is the way to re-test before re-adding the clause.

## Hybrid passage_k sweep — default 10 is optimal (2026-07-23)
How many passages hybrid retrieves per question (`passage_k`, default 10) is the retrieval-BREADTH
knob. MuSiQue/2wiki/hotpot corpora build one Document PER PARAGRAPH (`corpora.py`/`wiki_corpus.py`),
so there is no finer chunking lever — breadth is the whole of it. The bench engine's `answer()` gained
an ANSWER-time `GOLDENGRAPH_QA_PASSAGE_K` override (mirroring `GOLDENGRAPH_QA_ANSWER_MODE`) so the
same-graph env-A/B can sweep it over ONE shared build (unset/invalid -> configured default, byte-identical).
- **MEASURED (env_ab `GOLDENGRAPH_QA_PASSAGE_K:3,5,10,20`, MuSiQue N=100, one hybrid build, judge on,
  run 29982270929; `support_recall` 0.8425 identical across all 4 arms = the control):** answer_match
  0.37 (k=3) -> 0.42 (k=5) -> **0.44 (k=10)** -> 0.43 (k=20); token_f1 0.442 -> 0.485 -> **0.501** -> 0.497.
  A clear inverted-U peaking at **k=10**: too few passages miss supporting context, too many add
  distractor noise. (MuSiQue ships 10 context paragraphs/question — 2 supporting + 8 distractor — so
  k~=10 ~= "one question's worth of context," which is why it's the knee.)
- **DECISION: keep `passage_k=10` default (bench + library `ask()`), unchanged — it is optimal, not
  beatable by a tighter/broader k here.** The value is the confirmation + the reusable sweep knob for a
  future corpus/model. Re-sweep via `ab_env=GOLDENGRAPH_QA_PASSAGE_K:...` before changing the default.

## CHAT / embedding provider split (2026-07-22)
`OpenAIClient._ensure_client` (`llm.py`) reads `GOLDENGRAPH_LLM_BASE_URL` /
`GOLDENGRAPH_LLM_API_KEY` first, falling back to `OPENAI_BASE_URL` / `OPENAI_API_KEY`
(unset -> byte-identical to before). This routes goldengraph's CHAT (extraction +
synthesis) to a separate provider WITHOUT moving the embedder — the embedder is a bare
`OpenAI()` reading `OPENAI_*`, and **OpenRouter serves no embeddings endpoint**. Why it
exists: OpenAI's per-model requests-per-day cap (Usage Tier 1: gpt-4o-mini RPD 10000)
blocked a bench run mid-synthesis; the cap is on the CHAT model only (embeddings have a
separate, un-exhausted limit), so the fix is to split providers (chat->OpenRouter,
embeds->OpenAI), not move everything. The `env_ab` bench job wires this via
`OPENROUTER_API_KEY` + model `openai/gpt-4o-mini` (revertible by dropping 3 env lines).

## #4 post-hybrid extraction miss bucket — DROP (2026-07-23)
Measured, confound-free (PR #2056 fixed the empty-`OPENAI_BASE_URL` embedder bug that had
collapsed hybrid to entity-only, run 30010330554), how much of GoldenGraph's remaining QA miss is
genuinely extraction-limited now that hybrid synthesis is default-on. The instrument: one
`head_to_head` run with `GOLDENGRAPH_QA_TRACE=1` + `GOLDENGRAPH_QA_TRACE_LIMIT=0` emits the
full-population `_localize_trace` stage split for free (LLM-free, cached embeddings).
- **MEASURED (MuSiQue N=150, hybrid default-on, `passage_k=10`, `GOLDENGRAPH_EXTRACTOR=api`, judge on,
  run 30020312632):** confound cleared (zero `UnsupportedProtocol`/embedding-failed lines); headline
  recovered from the confounded 0.167 back to its clean ceiling — `answer_match` 0.393 full /
  **0.45 on the entity-subset (n=100)**, `llm_judge` 0.453 / 0.52, matching the known ~0.44 k=10 level.
  Stage split: **`{EXTRACTION:68, RETRIEVAL-BROKEN-CHAIN:3, RETRIEVAL-BUDGET:26, SYNTHESIS:53}`**.
- **Why the big EXTRACTION bucket does NOT mean an extraction frontier:** the split is
  BUILD-determined (classified against entity-graph node names, `graph_names`), so it counts every
  non-entity gold as EXTRACTION by construction. Of the 68: >=37 are structurally non-entity answers
  (12 dates, 5 numbers, 20 phrases) that can NEVER be graph nodes and are recoverable ONLY via
  hybrid's passage path; of the 31 short golds most are still ordinals/percentages/place-phrases
  ("third-largest", "74th", "48.8 percent", "northeastern Oklahoma"), leaving only ~15 plausibly
  extraction-addressable named entities (~10% of questions). Because the headline has already
  recovered to its hybrid-on ceiling, hybrid's passage path is demonstrably already answering the
  non-entity half — the EXTRACTION bucket overlaps hybrid's passage wins.
- **DECISION: DROP #4.** Per the go/drop rule (headline already high + big but build-determined
  EXTRACTION bucket → overlaps hybrid's passage recovery), entity-extraction-recall work is not the
  lever — it would chase a bucket that is mostly non-entity answers hybrid already handles. The two
  larger REAL frontiers here are **SYNTHESIS:53** (retrieved-but-wrong-answer, 35% — a prompt/synthesis
  lever) and **RETRIEVAL-BUDGET:26** (reachable-from-seeds-but-outside-the-ball, 17% — a retrieval-budget
  lever), both bigger than the ~15 extraction-addressable entities. The recall levers built for #4
  (`GOLDENGRAPH_RELATION_REPROMPT`, `GOLDENGRAPH_CHUNK_EXTRACT`) stay default-OFF; re-test via the
  same-graph `mode=env_ab` harness before ever shipping one.

## Hybrid ball edge-rerank (question-cosine top-K) — REFUTED (2026-07-23)
Tested pruning the hybrid ball to the top-K question-relevant edges before synthesis, to fix the
#4 SYNTHESIS miss (47/53 answer-edge-present but buried in a ~1,700-edge ball that `_format_subgraph`
serializes whole). Lever: `GOLDENGRAPH_HYBRID_FILTER=rerank` + `GOLDENGRAPH_HYBRID_FILTER_TOPK`
(embedder cosine question<->edge-text, seed-incident edges always kept), default-off.
- **MEASURED (env_ab `GOLDENGRAPH_HYBRID_FILTER:none,rerank`, TOPK=40, MuSiQue N=75, hybrid, judge off,
  one shared build, run 30035422835):** answer_match 0.5067 -> **0.4667 (-0.040)**; token_f1 0.565 ->
  0.519 (-0.046); support_recall 0.864 -> **0.744 (-0.120)**. A LOSS on every axis.
- **Why it fails (mechanism, not a bad K):** in the hybrid path provenance is taken from the pruned
  ball, and the -0.12 support_recall shows the rerank prunes AWAY supporting-fact edges. Multi-hop
  bridge edges are the casualty: the bridge entity is NOT named in the question, so its edges score
  low on question<->edge cosine and get dropped -- exactly the edges the answer chain needs. Ranking
  edges by similarity to the QUESTION is the wrong signal for multi-hop; a larger K only shrinks the
  harm toward baseline, it cannot manufacture a benefit.
- **DECISION: REFUTED, not shipped.** PR closed, not merged (a flat/negative default-off knob is dead
  weight; the value is this finding). If ball-pruning is revisited, the structurally-correct lever is
  `GOLDENGRAPH_HYBRID_FILTER=path` (`filter_subgraph_to_paths` — keeps seed->answer PATHS, not
  question-similar edges); A/B that instead.

## Bench build-wall is EXTRACTION, not auto-config (2026-07-23)
`GOLDENGRAPH_BUILD_DEBUG=1` per-phase split on the MuSiQue N=75 build (run 30035422835, cumulative
across 12 workers): **extract sum=9972s (72%)**, resolve sum=1193s (8.6%), pre_embed sum=920s (6.6%),
link sum=795s (5.7%). The 2,522 per-doc `resolve()` auto-config-to-RED cycles (BUDGET_ITERATIONS,
blocking) LOOK alarming in the log but are only ~8.6% of build compute -- a "configure ER once, reuse"
fix would save <9% and is NOT worth building. The dominant build cost is the per-document LLM
extraction, which is exactly what the `GOLDENGRAPH_LLM_CACHE` prompt-hash cache targets (warm cache ->
~72% build cut). Lesson: a high log-line COUNT is not wall; measure the phase split before optimizing.
