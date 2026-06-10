# DataFusion scale-substrate — sub-project 1: the cluster-edge rollup

**Date:** 2026-06-03
**Status:** design (approved by Ben, pre-spec-review)
**Parent decision:** scale mode (see `2026-06-01-arrow-native-finish-line-design.md`
§ "Scale mode decision"). Ben's call: the real unlock is DataFusion (embedded,
one box) + Sail (distributed, later). This is sub-project 1 of that arc.

**Scope guard:** this spec is ONE bounded proof-point. It does NOT migrate the
pipeline to DataFusion, does NOT touch the fuzzy scorer / Union-Find / golden,
and does NOT introduce Sail. Those are later sub-projects, each gated on this
one's measured result.

---

## Why this, and why now

The 25M/100M complete-path verdict (run 26878555131, post-#691/#692, dict-backed
view) recorded:

| pairs | variant | build s | golden s | id_prep s | peak RSS MB |
|---|---|---|---|---|---|
| 25M | legacy | 65.2 | 13.4 | 11.8 | 16,169 |
| 25M | columnar | 31.4 | 0.92 | 45.8 | 14,155 (−12.5%) |
| 100M | legacy | 284.8 | 56.7 | 47.6 | 61,082 |
| 100M | columnar | 136.8 | 3.87 | **566.0** | 56,333 (−7.8%) |

Two facts from it drive this sub-project:
1. The RSS win is real but **modest** (−12.5% / −7.8%, short of the −30% target),
   and the **dict never OOM'd** at 100M (61 GB fit a 64 GB box). The trump card —
   "the dict doesn't finish at scale" — is **unproven**.
2. `id_prep` (566s at 100M, **super-linear** from 45.8s @25M) is the limiter on
   BOTH wall and the residual RSS.

**Precise target (verify-don't-trust; corrected after spec-review #1):**
`confidence`/`quality`/`bottleneck` (the per-cluster ROLLUP) are ALREADY computed
columnar inside `build_cluster_frames` (the 136s build stage) — so a DataFusion
rollup wins nothing; the rollup is not the cost. The 566s `id_prep` is the
**per-pair edge VIEW**: `ClusterPairScores.from_frames` → `_bucket_pairs` builds
a `dict[int, dict[(a,b),score]]` that identity iterates to emit one evidence edge
per pair (`resolve.py:493/660`). **THAT per-pair dict-of-dicts is the 566s, and
it is what we replace.**

The replacement is NOT an `array_agg` (its in-memory group state ≈ the whole
input and spills poorly). It is a **cid-sorted edge stream**: attach `cid` to
each edge, `ORDER BY cid` via DataFusion's external (spilling) sort, and hand
identity a stream it consumes in same-cid runs — same data as the dict, packed
Arrow (~24 B/row vs ~100+ B/entry boxed), and **spills to disk** instead of
building a multi-GB Python dict. The per-cluster rollup rides along as a cheap
SECOND `group_by(cid)` (it duplicates what build already has, used only for the
parity check). DataFusion over Polars-lazy because **Sail is DataFusion** — the
proof transfers to the distributed sub-project.

## What this builds

A single bounded unit, gated behind scale mode, with TWO outputs from one
`SessionContext`:

```
cluster_edges_datafusion(
    pairs: pa.Table | pl.DataFrame,        # (a:i64, b:i64, score:f64) all_pairs
    assignments: pa.Table | pl.DataFrame,  # (member_id:i64, cluster_id:i64), one row/member (WCC out)
    *, memory_limit: int | None,           # bytes; None = unlimited; set low to force spilling
) -> (edges: pa.RecordBatchReader,         # PRIMARY: cid-sorted edge stream (replaces the dict)
      rollup: pa.Table)                    # SECONDARY: per-cluster aggregates (parity check)
```

**Step 0 — dedup (scale-mode policy).** The raw pairs may contain duplicate
canonical `(a,b)` with different scores. The legacy dict is INPUT-order LAST-WINS
(`_bucket_pairs`); scale mode resolves this to **MAX** (signed-off; R1 ≈ 0 on the
default single-weighted-matchkey path). So first reduce to one row per `(a,b)`
via MAX score (a `max(score) GROUP BY a,b`, or skip if the caller guarantees
deduped pairs — assert the invariant). This makes `edge_count`/`avg_edge`
well-defined and matches the scale-mode contract.

**Step 1 — attach cid + filter.** Register `pairs`/`assignments` zero-copy
(confirm at impl). Join `pairs.a→member_id` (`cid_a`) and `pairs.b→member_id`
(`cid_b`); keep `cid_a == cid_b` (cross-cut edges dropped — `_bucket_pairs` rule);
`cid = cid_a`.

**Step 2 — PRIMARY output: cid-sorted edge stream.** `SELECT cid, a, b, score …
ORDER BY cid` via DataFusion's **external sort** (spills through `DiskManager`).
Returned as a `RecordBatchReader`; identity consumes same-cid runs. This is the
per-pair view replacement — the 566s killer.

**Step 3 — SECONDARY output: per-cluster rollup (for the parity gate).** Two
group-bys joined on `cid`:
  - over the filtered edges: `min(score)→min_edge`, `avg(score)→avg_edge`,
    `count(*)→edge_count`, `first_value(struct(a,b) ORDER BY score,a,b)→bottleneck`
    (lexicographic, order-free, deterministic — the parent doc's §7 tie-break).
  - over `assignments`: `count(*) GROUP BY cluster_id → size` (REQUIRED — `size`
    is the member count, independent of `edge_count`; connectivity =
    `edge_count / (size*(size-1)/2)`).
  **Drive the rollup from `assignments` (LEFT join to the edge aggregate)** so
  singleton/edgeless clusters survive: coalesce `edge_count→0`, `min/avg→0.0`,
  matching `_columnar_presplit`. Confidence: `size<=1 → 1.0`; else
  `0.4*min + 0.3*avg + 0.3*conn`; weak iff `avg-min > 0.3` (split handled
  upstream). Output schema: `cluster_id, size, edge_count, min_edge, avg_edge,
  bottleneck_a, bottleneck_b`.

**Step 4 — spilling.** `RuntimeEnv::with_memory_limit(memory_limit, frac)` so the
external sort (Step 2) AND `GroupByHashExec` (Step 3) spill instead of OOM.

## The benchmark (the actual deliverable)

`scripts/bench_df_cluster_edges.py` + a `workflow_dispatch` job on
`large-new-64GB`. Three variants over the SAME `(pairs, assignments)`:
- **legacy** — the current `ClusterPairScores.from_frames` dict-of-dicts view
  build (the 566s) + its per-cid iteration.
- **datafusion** — `cluster_edges_datafusion` (this spec): the sorted edge
  stream consumed in cid-runs + the rollup.
- **polars** — the same via Polars lazy `sort`/`group_by` (attribution control:
  is the win DataFusion-specific, or just "not a Python dict"?).

**Input shape (the spec-review #1 gap — the toy fixture hides everything).** The
in-tree `_make_pairs_df` makes uniform fully-connected size-5 clusters, score
0.95 — which (a) never exercises sparse connectivity, oversized clusters, or the
`first_value ORDER BY` per-group sort cost, and (b) makes EVERY pair a bottleneck
tie, so "tie-break ≈ inert" is untestable on it. So the bench MUST run on a
realistic-shaped input via ONE of:
  1. a **capture step** that dumps `(pairs, assignments)` from an actual
     `bench-dataset-v1` run (real heavy-tailed cluster-size distribution), or
  2. a generator with a **heavy-tailed cluster-size distribution incl. oversized
     clusters + partial (sparse) connectivity + duplicate canonical pairs**.
The legacy dict scales with PAIR count (keyed per-pair), so size-5 does stress
RSS at 200M pairs — but the rollup's cost drivers and the tie-break rate need the
realistic shape. Report the bottleneck-divergence rate vs legacy on this input.

Scales: **25M, 100M, and a deliberate OOM-seeking point** — push pairs past where
the dict fits (200M+) AND/OR cap `memory_limit` low, so the legacy dict **OOMs**
and DataFusion **spills and survives**. This is the experiment that settles
binding-vs-non-binding — the question every prior bench dodged.

Record per variant: wall, peak RSS, survives/OOM, and bottleneck-divergence rate.
Commit the table into the roadmap doc.

## Correctness gates (not bit-identical — per scale-mode policy)

- **Edge-set parity (PRIMARY):** the cid-grouped edge SET from the DataFusion
  stream equals the legacy `for_cluster(cid)` edge set for every cid — as SETS
  (membership relaxed; edges are unordered identity inputs). Tested on a fixture
  with sparse clusters, a singleton, an oversized/split cluster, and duplicate
  canonical pairs (post-MAX-dedup).
- **Rollup parity (SECONDARY):** `size`, `edge_count`, `min_edge`, `avg_edge`
  (ε), and derived `confidence`/`cluster_quality` match the legacy per-cluster
  values for EVERY cluster INCLUDING singletons (`confidence=1.0`) and edgeless
  clusters (coalesced `min/avg=0.0`). Rand-1.0 partition assumed (WCC unchanged).
  `bottleneck` uses the NEW lexicographic rule — an output change on ties only;
  REPORT the divergence rate vs legacy on the realistic input (do NOT assert
  "inert" — the toy fixture's all-tie shape made that unmeasurable).
- **Dedup invariant:** assert the DataFusion `edge_count`/`avg_edge` match legacy
  only AFTER MAX-dedup of duplicate canonical pairs (legacy is last-wins; scale
  mode is MAX — the signed-off R1 change). If the caller passes raw pairs with
  diff-score dups, the rollup uses MAX, not last-wins; document the input
  contract.
- **Determinism:** run the DataFusion plan at **≥3 different `target_partitions`**
  (incl. one > core count); assert identical `cluster_id`/`size`/`edge_count`/
  `bottleneck` + edge sets, and `avg_edge` equal to a **tight ε (1e-12)**. If
  `avg_edge` drifts, pin the reduction (sort-then-sum) — the parent doc's §7
  determinism trap (`2026-06-01-arrow-native-finish-line-design.md` § "Scale mode
  decision", determinism row).
- **CI-validated posture:** subagents validate via `ruff` + `py_compile` only
  (box hangs on import); real tests + the bench run in CI. `datafusion` is a new
  optional dep — add under an extra (`goldenmatch[datafusion]`) so core installs
  are unaffected; the bench workflow installs it.

## Out of scope (explicit — later sub-projects, each gated on this result)

- Fuzzy scorer as a DataFusion `ScalarUDF` (needs the Rust-DataFusion build, #629).
- Union-Find / connected components on DataFusion.
- Golden survivorship on DataFusion.
- Identity per-pair edge streaming from DataFusion.
- Sail (distributed). DataFusion-embedded one-box must prove out first.

## Risks

- **Zero-copy ingest** — confirm DataFusion ingests the pairs/assignments frames
  without a full copy (else RSS measurement is contaminated). Validate first.
- **`first_value … ORDER BY` cost** — ordered aggregation may force a per-group
  sort; if it dominates, fall back to `min_by`-style or a two-pass (min then
  filter). Measure.
- **Win may still be non-binding** — if even DataFusion's spill doesn't matter
  because the dict fits at all reachable scale, the honest output is "one-box
  DataFusion is a modest win; the real value is the Sail distributed path" — and
  that reshapes the arc. The OOM-seeking bench is what tells us.
