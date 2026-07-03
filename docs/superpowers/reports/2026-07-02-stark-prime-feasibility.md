# STaRK-PRIME feasibility spike (SP2) — verdict

**Question:** does goldengraph's ingest + retrieve path run at STaRK scale, and
does the graph earn its place over a pure-dense baseline?

**Answer: YES on both, at PRIME scale.** Ingest + retrieve runs cleanly on
129,375 nodes / 8.1M edges; the 1-hop graph walk lifts recall@20 +39% and MRR
+17% over dense-only. Run on Modal (A10G, 64GB), `snap-stanford/stark` PRIME,
200 test queries, Ollama `nomic-embed-text`.

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
