# STaRK-PRIME feasibility spike (SP2) — verdict

**Question:** does goldengraph's ingest + retrieve path run at STaRK scale, and
does the graph earn its place over a pure-dense baseline?

**Answers.** Feasibility: **YES** — ingest + retrieve runs cleanly on 129,375
nodes / 8.1M edges. Does the graph earn its place: **NO on vanilla PRIME once the
dense baseline is FAIR.** The initial "graph +39% recall@20" held only against a
name-only dense baseline; when dense embeds each node's intrinsic description, it
nearly doubles and the naive 1-hop walk provides no net benefit (recall@20 −18%).
See "Fair-baseline follow-up" below — this is the load-bearing correction, and it
redirects to the ER-moat experiment (alias-injected STaRK). Run on Modal (A10G,
64GB), `snap-stanford/stark` PRIME, 200 test queries, Ollama `nomic-embed-text`.

> The "names-mode" numbers below (§Raw numbers / §Findings 1-4) are the FIRST run
> and are superseded on the retrieval-quality question by the fair-baseline
> follow-up. They remain valid for FEASIBILITY (ingest/RAM/scale) and as the
> weak-baseline half of the A/B.

## Raw numbers

```
load_stark_kb: 40.6s  nodes=129375 edges=8100498 queries=200
bulk_load:     38.1s  {n_nodes:129375, n_edges:8100498, n_dropped_edges:0, n_batches:1}  peak_rss=10.93GB
EntityIndex.build: 852.5s  indexed=129375  peak_rss=10.99GB
slice: endpoint_nodes=129375  isolated_nodes=0
peak_rss_final=10.99GB

[dense] hit@1=0.035 hit@5=0.145 recall@20=0.144 mrr=0.077  lat_mean=237.9ms lat_p95=321.9ms
[graph] hit@1=0.035 hit@5=0.145 recall@20=0.200 mrr=0.090  lat_mean=575.9ms lat_p95=2529.6ms
```

## Findings

### 1. Feasibility: single-batch ingest scales to PRIME (no OOM)
`bulk_load` loaded 129K nodes + **8.1M edges in ONE `StoreBatch`** (`n_batches=1`)
in 38.1s at 10.9GB peak RSS. The OOM ceiling the spike was designed to find was
**not hit** — the chunked-edges fallback was unnecessary at this scale. Zero
dropped edges (every STaRK endpoint resolved). Total peak RSS across the whole
run stayed at ~11GB.

### 2. The graph arm beats dense on recall/MRR
| arm | hit@1 | hit@5 | recall@20 | MRR | lat_mean | lat_p95 |
|-----|-------|-------|-----------|-----|----------|---------|
| dense (vectors only) | 0.035 | 0.145 | 0.144 | 0.077 | 238ms | 322ms |
| graph (seeds + 1-hop) | 0.035 | 0.145 | **0.200** | **0.090** | 576ms | 2530ms |

The 1-hop store walk (`as_of().query(seeds, 1)`) recovers answers reachable by a
relation but not textually near the query: **recall@20 +39%** (0.144→0.200),
**MRR +17%** (0.077→0.090). hit@1/hit@5 are identical — the top of the ranking is
dominated by the dense seeds; the graph contributes answers deeper in the list,
which is exactly where recall@20 and MRR reward it. This is the graph earning its
place through the store's retrieval path (not a Python side-structure).

### 3. Embedding is the build bottleneck
`EntityIndex.build` was 852.5s of the ~890s total non-query time — embedding 129K
node names via Ollama `nomic-embed-text` (batches of 256). The store ingest (38s)
and the graph walk (per-query) are cheap by comparison. Any scale-up work should
target the embedding throughput first (batch size, a faster/GPU-native embedder),
not the store.

### 4. Latency: graph walk costs p95, not mean
Dense is a flat ~238ms/query (one query embed + ANN). Graph adds a 1-hop walk:
mean 576ms but p95 2530ms — high-degree PRIME nodes (biomedical hubs) blow up the
neighbor set. A neighbor cap or degree-aware expansion is the obvious lever if
graph-arm latency ever matters.

## Honest caveats

- **Not leaderboard-comparable.** `EntityIndex` embeds node **names only**, not
  STaRK's full node text. Absolute numbers are low (hit@1 3.5%) for that reason.
  A real dense retriever on full text scores far higher. This spike is an
  **internal A-vs-B** (our dense vs our dense+graph); the **delta** is the signal,
  and it is positive. Making Arm A competitive with STaRK's leaderboard would mean
  embedding full node text — a separate, larger change.
- **isolated_nodes=0 here.** PRIME is edge-dense (every node is an endpoint), so
  the isolated-node honesty fix (index over the full node list, not the `as_of`
  slice) didn't bite on PRIME — but it is load-bearing for sparser KBs and stays
  in the design.
- **ER moat NOT exercised.** Vanilla STaRK is pre-resolved; this proves "structure
  loads + retrieves at scale," not "our ER beats ad-hoc dedup." Alias-injected
  STaRK is the deferred moat experiment.

## Next

- **AMAZON** (~1M nodes) is the next scale rung — same entry (`--kb amazon`). The
  single-batch JSON at ~1M nodes / more edges is where the OOM ceiling may finally
  appear; the `chunk_edges` fallback is wired and parity-tested for exactly that.
- If pursued as a real benchmark: embed full node text (not names) for a fair
  dense baseline, and add a degree cap to the graph walk.

Entry: `scripts/distill/modal_stark.py` (`modal run --detach ... --kb prime --sample 200 --spawn`).
Eight Modal-harness fixes were needed to get `stark_qa` importable without its
retrieval-baseline dependency tree (see the PR #1399 commit history); the
box-tested `bulk_load` + metrics code was unchanged throughout.

---

## Fair-baseline follow-up (the load-bearing correction)

**Motivation.** The names-mode "graph +39% recall@20" came against a dense baseline
that embeds only node NAMES — it cannot see a node's description, so it misses
answers the 1-hop walk then recovers. That is a handicapped baseline. The honest
test: embed each node's INTRINSIC document (`get_doc_info(add_rel=False)` = name +
description, NO relations, so the graph walk stays the ONLY structural signal) and
ask whether the graph delta SURVIVES. Same run (`--text-mode full`), same PRIME,
same 200 queries, same machine.

| metric | names-dense | names-graph | **text-dense** | **text-graph** |
|--------|-------------|-------------|----------------|----------------|
| hit@1 | 0.035 | 0.035 | **0.085** | 0.085 |
| hit@5 | 0.145 | 0.145 | **0.230** | 0.230 |
| recall@20 | 0.144 | 0.200 (+39%) | **0.261** | 0.213 (**−18%**) |
| MRR | 0.077 | 0.090 (+17%) | **0.151** | 0.150 (flat) |

(text run: load 44.2s, bulk_load 34.8s @ 11.0GB, EntityIndex.build 1123s — longer
docs embed slower than names; peak RSS 11.3GB.)

**Finding 1 — a fair dense baseline is far stronger.** Embedding the intrinsic doc
nearly doubles dense: recall@20 0.144→0.261 (+81%), hit@1 0.035→0.085 (2.4x), MRR
0.077→0.151 (~2x). The name-only baseline was badly handicapped.

**Finding 2 — the graph delta does not survive; it INVERTS.** Against fair dense,
the naive 1-hop walk gives **no net benefit and hurts recall@20 (−18%: 0.213 vs
0.261)**; MRR is flat. hit@1/hit@5 identical (the top-5 seeds are unchanged). So
the earlier +39% was **entirely a weak-baseline artifact**.

**Mechanism.** The graph arm ranks `top-5 seeds ++ all their 1-hop neighbors`
(deduped). When dense is strong, the top-20 dense hits are already good; flooding
in unranked neighbors after only 5 seeds displaces good dense hits at ranks 6-20
with structurally-adjacent-but-irrelevant nodes → recall@20 drops. First-hit rank
is unchanged → MRR flat, hit@1/5 unchanged.

**Conclusion.** On **vanilla** STaRK-PRIME, with a fair dense baseline, the graph
walk does **not** earn its place. This is expected and honest: vanilla STaRK is
**pre-resolved and text-rich**, so structure adds nothing over strong text. It does
NOT refute the program thesis — it sharpens it. The graph's value must be shown
where text retrieval BREAKS: the **ER-moat experiment (alias-injected STaRK)** —
inject alias/duplicate noise so the text signal fragments and dense collapses, and
resolved-graph structure is the only way to recover the answer. This negative is
the strongest argument for running the moat experiment next.

**Caveat on the graph arm itself.** The 1-hop expansion here is naive (top-5 seeds,
unranked neighbor flood). A smarter arm — rank neighbors by dense score, expand only
when dense confidence is low, or cap by degree — might not hurt. But the clean
finding stands: naive structural expansion does not beat strong dense on vanilla,
pre-resolved, text-rich STaRK.

## Revised next step

- **NOT the AMAZON scale rung** (it would only re-run the same weak-vs-fair question
  at 10x cost). Scale/OOM is an engineering data point available anytime.
- **The ER-moat experiment: alias-injected STaRK.** Corrupt a real STaRK KB with
  duplicate/alias nodes (mirror the homograph injection), run ad-hoc-dedup vs
  ER-native ingest, and measure whether resolved-graph retrieval recovers quality
  that fragmented-text dense loses. That is the battlefield where the graph is the
  only way to win — the test vanilla STaRK cannot provide.
