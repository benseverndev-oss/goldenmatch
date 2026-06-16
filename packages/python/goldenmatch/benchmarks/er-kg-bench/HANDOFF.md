# ER-KG-Bench — Handoff

_For the next Claude picking this up. Read this first, then `README.md` + `TAXONOMY.md` in this dir._

## TL;DR

ER-KG-Bench is a neutral, reproducible scoreboard for **entity-resolution quality in
knowledge-graph / agent-memory frameworks** (Microsoft GraphRAG, LightRAG, Cognee, mem0,
Graphiti, Neo4j LLM-KG-Builder, neo4j-graphrag-python, LlamaIndex) vs **goldenmatch**. It
runs each framework's *documented-default* dedup rule over a labelled record set stratified
by 9 failure classes and reports pairwise P/R/F1 per class.

- **Merged:** PR #1023 (the benchmark) → on `main`.
- **Open (draft):** PR #1025, branch `claude/er-kg-bench-llm-experiment` (the LLM experiment
  + the `emb-ann` adapter). Rebased clean onto `main`; awaiting the user to mark ready/queue.
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
python dataset/generate.py                  # seeds.jsonl -> records.csv (committed; regenerable)
python erkgbench/run.py                      # offline, no deps beyond polars/rapidfuzz/goldenmatch/numpy
python erkgbench/run.py --embedder st        # activates the modelled cosine OR-terms (needs sentence-transformers)
OPENAI_API_KEY=sk-... python erkgbench/run.py   # adds the goldenmatch(auto+llm) row
```
Outputs: `results/RESULTS.md` + `results/results.json`. Lint with `ruff check .`.

## Layout

```
seeds.jsonl                       32 ground-truth entities, mentions tagged by failure_class
dataset/generate.py               seeds -> records.csv (105 records)
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
| goldenmatch(auto+fields) | **0.674** | leads; only system scoring non-zero on `synonym` (via the context field) |
| Neo4j-KGBuilder | 0.636 | cosine 0.97 / edit-dist<3 / substring |
| neo4j-graphrag(fuzzy) | 0.548 | rapidfuzz WRatio≥0.8 |
| goldenmatch(emb-ann) | 0.529 | offline char-embedding ANN; beats string-only auto (0.418) |
| LlamaIndex-PGI | 0.486 | KNN-10 + word-dist<5 + cosine>0.9 |
| goldenmatch(auto) | 0.418 | string-only zero-config |
| MS-GraphRAG / LightRAG / Cognee / mem0 | ~0.18 | exact-match family; precision **0.0** on `same_name_collision` |

## The three load-bearing findings (don't re-derive these)

1. **Every framework's built-in dedup is shallow** — a single similarity threshold or one LLM
   prompt. None does multi-field probabilistic matching + blocking. Modelled at exact documented
   constants in `modeled.py` (each cites the source file + GitHub issue).
2. **The LLM scorer is the WRONG tool for the semantic classes** (measured, key-dependent, kept
   out of the committed table). `goldenmatch(auto+llm)` *lowered* abbr/synonym/cross-lingual
   (F1 0.674→0.607) because `llm_scorer` is a **precision filter on borderline candidate pairs
   blocking already produced** — it can confirm/reject a candidate, never create one. It never
   sees "IBM"↔"International Business Machines" because blocking doesn't pair them.
3. **Embedding-ANN is the RIGHT mechanism, but the offline embedder is char-based.** The shipped
   `goldenmatch(emb-ann)` uses goldenmatch's in-house char-n-gram embedder (pure numpy, no key,
   no torch). It beats string blocking on cross-lingual transliteration / typo / org-suffix, but
   **abbreviation (~0.18) and synonym (0.0) stay unsolved** — char-n-gram cosine has no world
   knowledge (IBM↔IBM-expansion ~0.05; Coumadin↔warfarin ~0.02).

**Net arc:** string blocking → misses semantics; LLM pair-scorer → wrong tool; embedding-ANN →
right mechanism, needs a *semantic* embedding to close the last two classes.

## The next task (highest value, explicitly requested direction)

**Swap a semantic embedding into `emb-ann` to crack abbreviation + synonym.** The adapter
(`GoldenMatchEmbAnnAdapter` in `goldenmatch_adapter.py`) is already structured for it: it embeds
mentions, builds a cosine matrix, thresholds candidate pairs, union-finds. Only the embedder
needs swapping.

Options, in order of "offline-ness":
- **sentence-transformers** (`all-MiniLM-L6-v2`) — local but needs `torch`. **WARNING: `import
  torch` hangs/segfaults in this dev environment** (documented in the package CLAUDE.md). You
  may only be able to validate it in CI / a torch-working box, not interactively here.
- **goldenmatch inhouse with a TRAINED model** — the in-house embedder can be *trained* from
  labelled pairs (`goldenmatch.embeddings.inhouse.train_embedder`), but a trained char-n-gram
  projection still won't learn IBM↔expansion (no shared features) — semantic knowledge is the
  missing ingredient, not training.
- **cloud embedding** (OpenAI `text-embedding-3-small` / Vertex) — needs a key/creds.

Add it as a new mode/adapter (e.g. `goldenmatch(emb-st)`), gate it on availability (skip
gracefully if torch/key absent, like `auto_llm` does), keep it **out of the committed table** if
it's key/torch-dependent (record as prose, matching how the LLM experiment was handled). Pick the
threshold by a small sweep but **do not overfit** — report a round, defensible value.

## Other open work (lower priority)

- **Live-framework adapters** for the deterministic resolvers (neo4j-graphrag rapidfuzz/spaCy,
  LlamaIndex Cypher) behind an optional extra, to corroborate the models in `modeled.py`.
- **More seed entities** — `seeds.jsonl` is small (32 entities / 105 records). Keep adding the
  precision-critical negative classes (`same_name_collision`, `temporal_version` — distinct
  entities with colliding surface forms). Re-run `generate.py` after editing.
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
