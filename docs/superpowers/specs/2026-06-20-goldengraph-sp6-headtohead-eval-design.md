# goldengraph SP6 — head-to-head eval design

The capstone: prove goldengraph's thesis with measurement, not assertion. Builds
on the investigation (`2026-06-20-goldengraph-sp6-benchmark-infra-investigation.md`)
and reuses ER-KG-Bench (`packages/python/goldenmatch/benchmarks/er-kg-bench/`)
rather than rebuilding a benchmark.

## Goal
Two questions, two halves:
1. **Resolution quality** — does goldengraph's resolution-backed KG resolve entities
   at least as well as goldenmatch (and beat the KG frameworks) on the existing
   ER-KG-Bench corpus?
2. **Downstream win** — does resolution actually buy better retrieval? **Measure the
   fact-co-location property that CAUSES the README's `(ER_accuracy)^hops` decay**: a
   resolved KG puts all of an entity's facts on one node, so retrieving that entity
   returns them all; an unresolved/exact-match KG splits the entity across duplicate
   surface-form nodes, so retrieving "the entity" returns only the queried node's
   facts and misses the rest. We measure the *fact co-location / completeness* (the
   base cause), NOT a hop-exponent — the KG model carries no edges to traverse (see
   Metrics). Today this is demonstrated once in `demo/`; SP6 measures it across a
   corpus and across engines.

## Corpus facts (what we build on)
`dataset/records.csv` rows are `record_id, mention, entity_type, context,
entity_id, failure_class, source` — surface forms grouped by `entity_id`
(Wikidata QID / RxCUI), 206 records / 48 entities / 9 failure classes. There is a
shared `context` per entity but **no distinct per-mention facts** — so the corpus
alone can't show fact loss under under-merge. The `demo/` (kg.py `build_kg`/
`retrieve`, agent.py `answer`, run_demo.py before/after KG + COUNT question) crafts
that fact structure for one protagonist. SP6 generalizes it into a small authored
QA layer.

## Half 1 — goldengraph as an ER-KG-Bench engine (reuse)
Add `erkgbench/adapters/goldengraph_adapter.py`: feed the corpus mentions through
goldengraph's resolve step (the native engine via `goldengraph` / `goldengraph-native`,
or goldenmatch-Provided mode), emit the partition in the harness's existing shape,
score via the unchanged `erkgbench/metrics.py` (per-class pair P/R/F1). Wire it into
`erkgbench/run.py` as a new engine row → it appears in `results/RESULTS.md`'s
headline + per-class tables next to the 11 framework configs.

- **Expectation (honest):** goldengraph resolves via the same goldenmatch resolver,
  so it should land at/near `goldenmatch(auto+fields)` F1 0.602 — the value is
  showing the **native engine** carries the +13pp ER lead end-to-end, and locking it
  with a parity check (goldengraph partition == goldenmatch `dedupe_df` partition on
  this corpus), not a new accuracy claim.

## Half 2 — QA / retrieval-completeness eval (the new measurement)

### QA corpus (authored)
A new `qa/` artifact (e.g. `dataset/qa.jsonl`) keyed to existing `entity_id`s.
Each item:
```
{ "qa_id", "entity_id", "question",
  "seed_surface": "<one surface form to query by>",
  "gold_facts": ["fact-1", "fact-2", ...],   # facts attached to DIFFERENT surface
                                             # forms / source docs of this entity
  "gold_answer": "<NL answer requiring all gold_facts>" }
```
Facts are attached to specific `(entity_id, surface_form, source)` rows (simulating
facts learned from different documents). The point: an entity with N surface forms
carries facts spread across them, so an unresolved KG that keeps N separate nodes
can only retrieve the queried node's facts. Pick entities with rich multi-surface /
cross-document structure (the abbreviation, xling, xdoc, suffix classes are ideal).
**Honesty:** this is an authored, synthetic QA layer (we write the facts +
questions), not a standard QA benchmark. It proves a *structural* property
(resolution co-locates facts) deterministically; it is not a claim about real-world
QA accuracy. State this in `RESULTS_QA.md`.

### KG construction per engine (the baselines — "Both")
Add a `facts: tuple[str, ...]` field to the EXISTING `demo/kg.py::Node` (additive,
default empty — keeps `demo/test_demo.py` green) and have `build_kg` union an
entity's authored facts across the cluster's `record_indices` exactly as it already
unions `names`. (Do NOT fork a parallel KG model — extend the demo's.) Build a KG
under each resolution strategy:
- **goldengraph** — resolved partition → one node per entity, all its facts co-located.
- **exact-match floor** — the harness's `validated` exact-match resolver → one node
  per distinct surface string, facts split. The controlled, self-contained baseline
  (no heavy deps); always runs, gates CI.
- **real framework KG** — a real neo4j-graphrag and/or LlamaIndex KG (with and
  without goldenmatch resolution) for a credible "vs the competition" head-to-head.
  Prefer the `goldenmatch-kg` shims **if/when they land**; they are spec'd but NOT
  yet in main, so the implementer may stand the frameworks up directly instead — do
  not block on a nonexistent import. **Opt-in / best-effort**: heavy framework deps
  in an isolated venv (mirrors the `goldenmatch-kg.yml` per-framework matrix); skip
  (status `skipped`, never fatal) if deps unavailable. NOT part of the CI gate, and
  NOT a blocker for SP6's gate.

### Metrics ("Both") — NO hop axis
The KG model carries no edges (`Node` = names/type/context/facts/record_indices; `KG`
= nodes) so there is nothing to traverse and **no `k-hops` sweep**. The win is
structural fact co-location, measured by a single resolved-vs-unresolved comparison.
Reuse the reachability model `demo/narrative.py::under_merge_answer` already uses
(`distinct_nodes` / `names_reachable` / `complete`), NOT `demo/kg.py::retrieve(query)`
— `demo/run_demo.py` (~lines 244-251) explicitly rejects query-only retrieval because
it surfaces only the one literally-queried node and HIDES the fragmentation (making
before == after); it presents the entity's whole reachable slice instead.

For each QA item × engine:
- **fact completeness (deterministic — the gated metric):** identify the node(s) the
  engine treats as "this entity" — resolved: the single merged node; exact-match
  floor: only the node matching `seed_surface` (the other surface forms are stranded
  on separate nodes the floor never connects). `retrieved_facts` = union of `facts`
  over those reachable node(s), per the `under_merge_answer` model. Score
  `completeness = |gold_facts ∩ retrieved_facts| / |gold_facts|`. No LLM →
  reproducible, free, CI-gateable. Resolved ≈ 1.0 (all facts on one node); floor < 1.0
  (facts stranded on the other surface-form nodes). Report mean completeness per
  engine, broken out by failure class. This measures the fact co-location that is the
  *cause* of the `(ER_accuracy)^hops` decay — we do NOT claim to measure the
  hop-exponent itself (no graph topology exists to traverse).
- **LLM-judged answer correctness (opt-in — color):** generate an NL answer from the
  retrieved facts (`agent.answer`), LLM-judge vs `gold_answer` (0/1 or graded).
  Non-deterministic, costs API → off by default (needs `OPENAI_API_KEY`), runs only
  in the report lane, never gates. The richer end-to-end signal on top of the
  deterministic structural metric.

### Output
`results/RESULTS_QA.md`: (1) **fact-completeness table** (engine × mean completeness,
broken out by failure class) — goldengraph high/≈1.0 vs the exact-match floor (and any
real framework) lower; (2) the framing (resolution co-locates facts → complete
retrieval; under-merge strands them); (3) the opt-in LLM-judged correctness column
when run.

## CI lane
New informational `bench-er-kg.yml` (`workflow_dispatch`; not `ci-required`, like the
other binding lanes):
- **gate job** (always): builds goldengraph native, runs Half-1 engine scoring +
  Half-2 **deterministic fact-completeness** for goldengraph vs exact-match floor.
  Asserts goldengraph's mean completeness exceeds the floor's by a concrete margin
  (the thesis), and the Half-1 parity check. Self-contained, no LLM, no framework deps.
- **opt-in inputs:** `with_llm` (LLM-judged correctness; needs the OpenAI secret),
  `with_frameworks` (real neo4j-graphrag / LlamaIndex KGs via the shims, isolated
  venv, best-effort).
Confirm green before arming auto-merge.

## Determinism & honesty (the bar this program has held)
- The CI gate is deterministic: fixed corpus, fixed authored facts, exact fact-set
  membership, resolver output is deterministic. LLM answer + LLM judge + real
  frameworks are all opt-in / non-gated.
- The QA corpus is authored/synthetic — `RESULTS_QA.md` says so plainly. SP6 proves a
  structural property (resolution co-locates an entity's facts on one node → complete
  retrieval; under-merge strands them), not a real-world QA-accuracy number.
- Half 1 adds a real engine row to a committed table; it's a parity-locked
  demonstration that the native engine carries goldenmatch's measured ER lead, not a
  fresh accuracy claim.

## Testing
- Half 1: parity unit test (goldengraph partition == goldenmatch `dedupe_df`
  partition on the corpus); adapter emits the harness shape; `metrics.py` unchanged.
- Half 2: deterministic fact-completeness on a tiny fixture (resolved vs split) with a
  known gold-fact set → asserts resolved≈1.0, split<1.0; the `Node.facts` addition +
  `build_kg` keep the existing `demo/test_demo.py` green; LLM paths use a stub
  judge/answerer (assert plumbing, not LLM accuracy — the goldenmatch-kg posture).

## File structure
```
benchmarks/er-kg-bench/
  erkgbench/adapters/goldengraph_adapter.py   # Half 1: new engine row
  erkgbench/run.py                            # +goldengraph engine wiring
  dataset/qa.jsonl                            # Half 2: authored fact/QA layer (keyed to entity_id)
  demo/kg.py                                  # +Node.facts field + build_kg unions facts (extend, don't fork)
  erkgbench/qa_eval.py                        # build per-engine KG, fact-completeness, (opt) LLM-judge
  results/RESULTS_QA.md                       # fact-completeness output
  tests/test_qa_eval.py                       # deterministic fact-completeness fixture + stubs
.github/workflows/bench-er-kg.yml             # informational lane (gate + opt-in llm/frameworks)
```

## Out of scope / follow-ups
- A real-world QA benchmark (this is an authored structural eval).
- Multi-hop reasoning beyond fact co-location (we measure fact-completeness, not inference).
- Publishing the eval as a standalone leaderboard.
- Wiring the real-framework head-to-head into the gate (stays opt-in until the
  framework deps prove stable in CI).
