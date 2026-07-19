# PR-B design — Arrow-native FS pair stream (retire `list[tuple]` scoring output)

**Epic:** `docs/superpowers/plans/2026-07-18-fs-rust-arrow-only.md` (goal 1 / PR-B).
**Trigger:** the 1M person `gm_probabilistic` OOM. This session proved the two
independent causes and fixed the first:

1. **Blocking quality (FIXED, commit `8e6dc10`).** Auto-config emitted ~12.9B
   candidate pairs (dob-YEAR + name-soundex mega-passes). `_bound_probabilistic_
   blocking_pairs` gates every pass on candidate PAIRS not rows → 66M pairs, max
   block 660. Necessary but not sufficient.
2. **The pair STREAM is `list[tuple]` (THIS PR).** `backends/score_buckets.py::
   score_buckets` scores each block with the native Arrow kernel
   (`score_block_pairs_fs_arrow`) but then marshals results back to Python and
   accumulates `all_pairs: list[tuple[int,int,float]]` (line 1557/1608) **plus a
   second full copy** `matched_pairs: set[(min,max)]`. At 66M pairs that is
   ~16 GB of Python objects (≈240 B/pair across the two structures) vs ~1.3 GB
   if the stream stayed Arrow int64/int64/float64 (20 B/pair). That is the local
   12 GB OOM and the reason 1M only *just* fits 64 GB.

**Thesis:** a functioning FS path is Rust + Arrow native. The per-block math is
already native; the pair stream between the kernel and clustering must stay Arrow
(never become `list[tuple]`).

## Target architecture

`score_buckets` returns a **`pa.Table` with `PAIR_STREAM_SCHEMA_SPEC`**
(`{id_a: int64, id_b: int64, score: float64}`, `core/frame.py:2139`) instead of
`list[tuple]`, threaded straight into **`cluster.build_clusters_arrow_native`**
(`cluster.py:1948`) — the Rust Union-Find that reads the pair stream's Arrow
buffers via the C Data Interface and emits `ClusterFrames` directly. **Both the
schema and the Arrow cluster kernel already exist** (the columnar weighted lane
uses them) — PR-B wires the FS lane onto the same rails.

Memory at 66M pairs: ~16 GB (Python) → ~1.3 GB (Arrow) for the stream; clustering
stays in Rust (no `dict[int,dict]` materialization).

## The hard problems (why this isn't a return-type swap)

The FS `pairs` list is consumed by SIX things in `pipeline.py` (dedupe path +
both match lanes). Each must go Arrow-native or bridge explicitly:

1. **`matched_pairs` cross-pass exclude set.** FS blocking is multi_pass; a pair
   scored in pass 1 must not be re-emitted by pass 2. Today: a Python `set` of
   `(min,max)`, passed INTO `score_buckets` and `.add`ed after each pass.
   Arrow-native replacement: **union all passes' pair tables, then dedup by
   canonical `(id_a,id_b)` keeping max score** — `dedup_pairs_max_score`
   (distributed path already has this shape). Decision: keep exclusion INSIDE
   `score_buckets` (it already partitions per pass internally) but have it emit a
   single deduped table; the caller stops maintaining the `set`. The kernel's
   `exclude_set` FS support (`FS_SUPPORTS_EXCLUDE_SET`) stays for intra-call use.
2. **`_split_probabilistic_pairs(pairs, link_threshold)`** splits into linked
   pairs (≥ link) + review candidates (grey zone). Vectorize as an Arrow
   `score >= link` filter → two tables (linked, review). Review pairs are small
   (grey band only) — they MAY stay a small list, but the linked table (the big
   one) must be Arrow.
3. **`across_files_only` filter** (`source_lookup[a] != source_lookup[b]`) — a
   match-lane predicate. Vectorize via an Arrow join/gather on a source-id array
   (both endpoints), not a Python comprehension.
4. **`review_pairs`** feed the review queue — small, grey-band only; keep as-is
   (not a scale surface).
5. **Semantic-blocking union** (opt-in) unions extra candidate sources into the
   stream; must union into the Arrow table via `dedup_pairs_max_score`, not
   `all_pairs.extend`. Refuse+degrade already exists for the columnar lane —
   mirror it.
6. **Bench dump path** (`_bench_dump_dir`) stays per-block `list` (exact
   candidate/emitted accounting); it is a diagnostic lane, explicitly not scale.

## Phasing (sub-PRs, each parity-gated + green before the next)

- **B1 — `score_buckets` emits Arrow internally, adapts at the boundary.**
  Build the per-pass pair tables as Arrow, dedup across passes with
  `dedup_pairs_max_score`, and return the `pa.Table`. Add a thin
  `score_buckets_list()` shim that `.to_pylist()`s it so every current caller is
  byte-unchanged. **Parity gate:** the shimmed list == today's `all_pairs`
  (order-insensitive; sort by `(id_a,id_b)`), on the FS bucket fixtures.
- **B2 — thread the Arrow table into `build_clusters_arrow_native`** on the FS
  dedupe path, behind `GOLDENMATCH_FS_ARROW_STREAM` (default off). `_split` +
  `across_files` vectorized to Arrow. `matched_pairs` set no longer built on the
  Arrow path. **Parity gate:** `DedupeResult.clusters` byte-identical (pair-set
  and cluster-membership) to the list path on historical_50k / febrl3 /
  synthetic; the standing `bench-probabilistic` panel F1 unchanged.
- **B3 — flip default, drop the list path + `matched_pairs` set** on the FS
  lane; the `score_buckets_list` shim moves under `tests/` as the parity oracle.
  The two match lanes migrate the same way. `import numpy` audit on the FS
  scoring path.

## Memory-validation gate (the actual bug)

Re-run the local probe (`scratchpad/probe_autoconfig_mem.py`, 12 GB cap) on the
1M person fixture with `GOLDENMATCH_FS_ARROW_STREAM=1`: scoring must **complete
under 12 GB** (list path OOMs at 12 GB today). Plus the `bench-er-headtohead`
person-1M lane green on the 64 GB runner at materially lower peak RSS than the
list path.

## Rollout / gates / rollback

- Land B1→B3 separately; each green on FS unit + the pair-parity gate + the
  `bench-probabilistic` panel.
- `GOLDENMATCH_FS_ARROW_STREAM=0` restores the `list[tuple]` path for one release.
- `native_symbols` gate: no NEW kernel symbol (reuses `build_clusters_arrow` +
  `score_block_pairs_fs_arrow`, already registered) — no wheel republish needed,
  sidestepping the #688 skew class.

## Risks

- **Cross-pass dedup parity** (highest) — the `matched_pairs`→`dedup_pairs_max_
  score` swap must keep the exact emitted set (canonical `(min,max)`, max score).
  Dedicated fixture: a pair reachable via two passes with different scores.
- **Clustering parity** — `build_clusters_arrow_native` v1 marks all clusters
  `strong`; weak-downgrade/auto-split run in the legacy post-processor. The FS
  path must chain the same post-processor so `cluster_quality` is unchanged.
- **Score-tuple width** — if the FS stream ever needs to carry more than `score`
  (NE dims), `PAIR_STREAM_SCHEMA` is 3-col; extra per-pair signal must ride a
  separate structure or be resolved before the stream. Confirm the FS bucket
  output is exactly `(a,b,score)` today (it is — `all_pairs` is `[(int,int,float)]`).
