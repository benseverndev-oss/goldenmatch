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
