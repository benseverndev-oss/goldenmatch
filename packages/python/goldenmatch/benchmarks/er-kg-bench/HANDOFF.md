# ER-KG-Bench — Handoff

_For the next Claude picking this up. Read this first, then `README.md` + `TAXONOMY.md` in this dir._

## TL;DR

ER-KG-Bench is a neutral, reproducible scoreboard for **entity-resolution quality in
knowledge-graph / agent-memory frameworks** (Microsoft GraphRAG, LightRAG, Cognee, mem0,
Graphiti, Neo4j LLM-KG-Builder, neo4j-graphrag-python, LlamaIndex) vs **goldenmatch**. It
runs each framework's *documented-default* dedup rule over a labelled record set stratified
by 9 failure classes and reports pairwise P/R/F1 per class.

- **Merged → `main`:** #1023 (benchmark), #1025 (`emb-ann` + LLM experiment), #1032 (`emb-openai`),
  #1034 (grow dataset + the `bench-er-kg` CI lane).
- **This branch (`claude/er-kg-bench-real-corpus`):** the big credibility upgrade — replaced the
  hand-authored synthetic seeds with a **real corpus** from Wikidata + RxNorm (QID/RxCUI = ground
  truth). See "Real corpus" below; this is what makes the numbers defensible.
- Location: `packages/python/goldenmatch/benchmarks/er-kg-bench/`.

## Why this exists (strategic context)

Came out of a research thread: "what big OSS wave could goldenmatch be the default in?" Answer
landed on the **AI-agent / knowledge-graph wave** — but an adversarial pressure-test showed the
"unowned gap" thesis is **mostly false** (Zep/Graphiti owns "ER for agents"; Senzing coined
"Agentic Entity Resolution"). The *defensible* position is the **vector-DB precedent**: every
framework ships shallow built-in dedup (a single similarity threshold or one LLM prompt; none
does multi-field probabilistic matching + blocking), so goldenmatch can win the **quality/scale/
auditability ceiling** like Qdrant/Weaviate won over LangChain's naive in-memory vector store.
This benchmark is the artifact that makes that ceiling concrete and citable.

## How to run

```bash
cd packages/python/goldenmatch/benchmarks/er-kg-bench
python dataset/build_real.py                 # sources.jsonl -> records.csv from Wikidata+RxNorm (committed; --dry-run to preview)
python erkgbench/run.py                      # offline, no deps beyond polars/rapidfuzz/goldenmatch/numpy
python erkgbench/run.py --embedder st        # activates the modelled cosine OR-terms (needs sentence-transformers)
OPENAI_API_KEY=sk-... python erkgbench/run.py   # adds the goldenmatch(auto+llm) row
```
Outputs: `results/RESULTS.md` + `results/results.json`. Lint with `ruff check .`.

## Layout

```
dataset/sources.jsonl             33 curated real entities (Wikidata QIDs / RxNorm ingredients) by failure_class
dataset/build_real.py             sources.jsonl -> records.csv (147 records); QID/RxCUI = ground truth
erkgbench/metrics.py              pairwise P/R/F1 per class + determinism check
erkgbench/adapters/base.py        Adapter protocol, Record, union-find clustering helpers
erkgbench/adapters/modeled.py     the 7 framework default-rule models (exact constants + source citations)
erkgbench/adapters/goldenmatch_adapter.py   GoldenMatchAdapter (auto/auto_fields/auto_llm) + GoldenMatchEmbAnnAdapter
erkgbench/run.py                  runner; per-adapter try/except so one flaky adapter can't sink the board
README.md / TAXONOMY.md           the writeup + the 9-class taxonomy with framework citations
```

## Current committed results (offline, no key — reproducible by anyone)

| System | F1 | note |
|---|---|---|
| goldenmatch(auto+fields) | **0.721** | leads by +16.7pp; zero-config multi-field (name+type+context) |
| goldenmatch(auto) | 0.617 | string-only zero-config |
| Neo4j-KGBuilder | 0.554 | cosine 0.97 / edit-dist<3 / substring (best framework default) |
| goldenmatch(emb-ann) | 0.492 | offline char-embedding ANN, name only |
| neo4j-graphrag(fuzzy) | 0.448 | rapidfuzz WRatio≥0.8; over-merges (P 0.35) |
| LlamaIndex-PGI | 0.315 | KNN-10 + word-dist<5 + cosine>0.9 (P 0.22) |
| MS-GraphRAG / LightRAG / Cognee / mem0 | **0.089** | exact-match family COLLAPSES on real variants (R 0.047); only `cross_document_exact` non-zero |

## The load-bearing findings on REAL data (don't re-derive these)

1. **Every framework's built-in dedup is shallow** — a single similarity threshold or one LLM
   prompt; none does multi-field probabilistic matching + blocking. Modelled at exact documented
   constants in `modeled.py` (each cites source + issue).
2. **On real data the exact-match family COLLAPSES** — GraphRAG / LightRAG / Cognee / mem0 →
   **F1 0.089** (recall 0.047): byte-identical matching can't resolve real surface-form variants
   (IBM vs International Business Machines, München vs Monaco di Baviera). Fuzzy resolvers buy
   recall but over-merge: neo4j-graphrag 0.448 (P 0.35), LlamaIndex 0.315 (P 0.22).
3. **goldenmatch(auto+fields) wins decisively — 0.721, +16.7pp over the best framework default**
   (Neo4j 0.554), zero-config. Multi-field probabilistic ER is the differentiator (abbr 0.77,
   xling 0.77, typo/suffix 1.0, nick 0.85). This is goldenmatch's REAL strength; on real data the
   bench finally shows it (the synthetic set, name-heavy + clean-context, didn't).
4. **The LLM scorer is a PRECISION tool** (key-gated, prose): `auto+llm` drives same-name-collision
   precision to **1.0** (correctly refuses Georgia country-vs-state, Michael Jordan
   athlete-vs-scientist) but still can't crack synonym (0.12) — it filters borderline pairs, never
   creates them. On the OLD synthetic set (no genuine collisions) this looked useless; real
   collisions reveal its value. **This finding flipped with the real corpus.**
5. **Semantic embedding (emb-openai, key-gated) cracks abbreviation but does NOT win** — name-only
   `text-embedding-3-small` gets abbr 0.90 / xling 0.88 but over-merges (P 0.41, F1 0.52), BELOW
   multi-field `auto+fields` (0.72). On real multi-field entities context beats a name embedding.
   **This too flipped** (on synthetic, name-only emb-openai led; on real data multi-field leads).

**Net arc (real data):** frameworks collapse (exact) or over-merge (fuzzy); goldenmatch's
zero-config multi-field probabilistic ER wins; the LLM adds collision precision; semantic
embedding is a name-only complement. Honest gaps: synonym recall (0.14), collision precision
(~0.47 without the LLM).

## Real corpus (this branch — the credibility upgrade)

**Shipped.** `dataset/build_real.py` (stdlib HTTP, `--dry-run`) reads `dataset/sources.jsonl`
(curated Wikidata QIDs + RxNorm ingredients) and writes `records.csv` with a `source` provenance
column. 147 records / 33 entities / 9 classes; **71% from Wikidata + RxNorm with external ground
truth** (QID/RxCUI), 29% honest synthetic-over-real (typo/org_suffix/cross_document_exact +
temporal_version editions). Real Wikidata `description` = the `context` field. Old
`seeds.jsonl`/`generate.py` removed; `run.py` errors with a build hint if `records.csv` is missing
(no network regen mid-run). Curation note: not every QID carries the surface forms you want —
`--dry-run` first (e.g. NASA/UNESCO/FBI had no usable aliases and were dropped).

`emb-st` (free MiniLM semantic row, `provider="local"`) was prototyped on the prior branch but
shelved (modest: ~0.6, MiniLM lacks drug-synonym knowledge); not on this branch. The OpenAI key
lives in Infisical AND as the `OPENAI_API_KEY` Actions secret used by `bench-er-kg`.

## Other open work (lower priority)

- **Live-framework adapters** for the deterministic resolvers (neo4j-graphrag rapidfuzz/spaCy,
  LlamaIndex Cypher) behind an optional extra, to corroborate the models in `modeled.py`.
- **More real entities** — add curated rows to `dataset/sources.jsonl` (Wikidata QIDs / RxNorm
  ingredients), `--dry-run` to confirm the QID actually carries the surface forms, then re-run
  `build_real.py`. Synonym recall (0.14) is the biggest headroom — more RxNorm drugs help. Then
  regenerate results via the **`bench-er-kg` CI lane** (goldenmatch rows are too memory-heavy for
  a laptop; offline artifact = committed table, keyed run = emb-openai/auto+llm prose via the
  `OPENAI_API_KEY` Actions secret).
- **`emb-st` (free MiniLM committed semantic row)** — prototyped + shelved (modest ~0.6); revisit
  if a stronger free local embedder is worth a no-key semantic row.
- **The before/after GraphRAG demo** — build a KG, show the agent answering wrong from
  fragmented/over-merged entities, resolve, show it correct. Draws its numbers from this harness.
  This is the shareable artifact (Show-HN / blog).

## Gotchas / environment facts (will save you time)

- **`import torch` hangs in this env.** Anything semantic-embedding via sentence-transformers may
  not run interactively here. The whole reason `emb-ann` uses the numpy char-n-gram embedder.
- **CI "superseded-run" pattern:** every push cancels the prior in-flight run, which flips the
  `ci-required` aggregate gate to *failure* for the OLD sha. This fired 3× this session and was
  cosmetic every time. **Verify with `get_job_logs(failed_only=true, run_id=...)` — if it returns
  `failed_jobs: 0`, it's a cancelled superseded run, not a real failure.** Only act on a failure
  whose `HeadSHA` is the *current* head.
- **Path filters:** this benchmark dir sits outside the package's pytest/lint paths, so the
  `python` job is *skipped* on these PRs — no test lane runs against the benchmark. The
  `synthetic_benchmarks` + consistency gates (`version_consistency`, `docs_consistency`,
  `docs_staleness`, `ts_parity_freshness`) are the ones that actually run; they've been green.
- **Squash-merge + follow-up branches:** #1023 was squash-merged, so a follow-up branch targeting
  `main` shows a bloated diff (re-includes merged content) until rebased. Fix:
  `git rebase --onto origin/main <old-pr-tip-sha> <branch>` then `git push --force-with-lease`.
  (Already done for #1025.)
- **Commit identity:** set `git config user.email noreply@anthropic.com && git config user.name
  Claude` before committing or the stop-hook flags commits as Unverified.
- **A real OpenAI key was pasted in the originating chat** to run the `auto+llm` experiment. It
  was used only as an ephemeral env var, never written to any file or commit (diff was scanned).
  The user was told to rotate it. Do not expect it to be available; the `auto+llm` row is
  opt-in via `OPENAI_API_KEY`.
- **Determinism:** the `det-floor` column re-runs each adapter and compares partitions. All
  current adapters are deterministic (goldenmatch auto-config was stable on this small set;
  emb-ann uses a fixed-seed projection). An LLM-backed adapter will likely show `det-floor: no`.
- **Fairness stance:** modelled adapters reproduce each framework's *documented default*; don't
  strawman them. goldenmatch is *dogfooded* as zero-config `dedupe_df(df)`, not a hand-tuned
  threshold. Keep both honest — the benchmark's credibility is the whole point.

## PR / branch state at handoff

- `main`: has ER-KG-Bench (PR #1023 merged).
- `claude/er-kg-bench-llm-experiment` (PR #1025, **draft**): 2 commits ahead of main
  (`2e7ea1e` LLM experiment, `ba2beac` emb-ann). Rebased + force-pushed clean. CI was green on
  the prior head; a fresh run is in flight after the rebase. User decides when to mark ready/queue.
