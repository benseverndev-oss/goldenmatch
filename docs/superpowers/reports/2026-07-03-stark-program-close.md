# STaRK retrieval / ER-moat program — CLOSED (2026-07-03)

Canonical closure record for the STaRK arc. Status: **complete, honest negative.
Recommend no further STaRK work.**

## Goal

Prove goldengraph's graph + goldenmatch's entity resolution earn their place on a
**real, structure-load-bearing retrieval benchmark** (STaRK, arXiv 2404.13207),
after the MuSiQue finding showed no measurable graph value on passage-style QA.

## The arc (4 runs, each a shipped PR + verdict report)

| # | run | PR | finding |
|---|-----|----|---------|
| 1 | feasibility + vanilla, names-embed | #1399/#1400 | ingest+retrieve runs at PRIME scale (129K nodes / 8.1M edges, single-batch, no OOM); graph "+39% recall@20" over dense |
| 2 | fair dense (full-text embed) | #1402 | the +39% was a **weak-baseline artifact**; with a fair dense baseline the graph delta **inverts** (−18%). Dense is all you need on vanilla STaRK |
| 3 | alias-injected, Case A (fragment answers) | #1407 | **confounded instrument** — canonical-equivalence scoring gives dense k retrieval chances at the gold, so fragmentation *helps*; signal inverted |
| 4 | alias-injected, Case B (fragment bridges) | #1408 | confound removed (dense control flat); moat **directionally real** on the graph arm (ER 0.190 > adhoc 0.185 > frag 0.183) but **negligible** — ER−adhoc = +0.005 recall@20 ≈ 1 question |

Reports: `2026-07-02-stark-prime-feasibility.md`, `2026-07-03-stark-alias-moat.md`,
`2026-07-03-stark-bridge-moat.md`.

## Conclusion

**goldenmatch's ER does not measurably improve STaRK retrieval — but not because it
fails.** It demonstrably merges variant surface forms that exact-match cannot (proven
in the `stark_resolve` unit test AND at scale: ~1,554 merges on PRIME). It simply
does not move **retrieval** on a **text-rich, dense-dominated** benchmark, because
retrieval there rarely depends on resolved structure: dense answers most queries
directly, so the graph path — and therefore any resolution of that path — is
load-bearing for only a small slice.

The moat lives where resolution is **the** bottleneck: **deduplication /
entity-resolution** tasks (the suite's home turf), not semi-structured retrieval.

## Why this is a trustworthy negative (not a failed attempt)

- **Measure-first, every step.** No claim survived without a measured number on the
  real benchmark.
- **The discipline caught two would-be-mis-sold results:** the full-text inversion
  (killed a flattering +39%) and the Case A confound (the built-in "check the
  control / clean-fragmented first" guard fired both times).
- **Every experiment passed spec + plan review** (each caught a real load-bearing
  bug pre-code: the graph-arm store-merge that doesn't work in one batch; the
  str/int scoring-type mismatch).
- **Case B added a control arm** (dense, answers intact) that behaved exactly as a
  valid control should — flat.

The instrument is sound; the signal is genuinely absent.

## What ships and stays (reusable)

All box-TDD'd, on main:
- `goldengraph/bulk.py::bulk_load` — pre-structured KB → store, single-batch or
  chunked (an at-scale ingest primitive, independent of the moat question).
- `goldengraph/entity_index.py::EntityIndex` — embed-once ANN retrieval sidecar.
- `goldengraph/stark_inject.py` (`inject_aliases`, `bridge_targets`),
  `goldengraph/stark_moat.py`, `erkgbench/stark_resolve.py`,
  `erkgbench/stark_metrics.py`, `stark_adapter.py`, `scripts/distill/modal_stark.py`
  — the full alias-moat harness (inject → resolve → cluster → collapse → canon-score
  → Modal). Reusable verbatim if a **structure-load-bearing** retrieval benchmark
  ever appears.

## Not pursued (deliberate, with reasons)

- **AMAZON / MAG (~1M+ nodes)** — a pure scale/OOM engineering data point, unrelated
  to the moat question; runnable anytime via `--kb amazon` (`chunk_edges` wired +
  parity-tested). Not worth the embed cost for a question already answered.
- **Further injection knobs** (higher k, background rate, distractor scoring) — the
  Case B instrument is sound and the signal is absent; more knobs would chase noise.
- **Full-text-with-relations dense / smarter graph arm** — would strengthen dense or
  the walk, widening the gap the moat must overcome, not narrowing it.

## Recommendation

**Close STaRK.** Redirect graph/ER validation to tasks where resolution is the
bottleneck (dedup / ER quality), where goldenmatch already wins — the honest home
for the moat.
