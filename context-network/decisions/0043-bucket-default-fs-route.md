# 0043 — Bucket is the default Fellegi-Sunter route; FS scores in every scale lane

**Status:** Accepted. **Shipped:** goldenmatch 3.4.0 (PRs #1810 default route, #1843 distributed + chunked, #1844 strategy blocks, #1794 batched worker, #1829 auto-split + dense guard, #1842 columnar/hatch, enabling primitive #1808; block-safety #1784 / #1790 / #1857).

## Context

FS block scoring routed through the legacy dense-`NxN` numpy path
(`score_probabilistic_vectorized` / `_batched`) by default, native-gated behind
one condition that had drifted into three per-call-site copies, with a hard row
cap. That path builds a comparison matrix per block, so a real-world blocking
scheme — many blocks, a Zipfian head — either OOMs (`person`-1M raised
`MemoryError` inside `build_blocks`) or fans out into hundreds of thousands of
tiny FFI calls. Separately, the **distributed and chunked** lanes *silently
dropped* FS matchkeys (made a loud `NotImplementedError` by #1800), and
strategy-generated blocking (lsh/ann/learned/canopy/sorted_neighborhood) had no
scale path at all.

## Decision

**The memory-bounded bucket lane is the default FS route, native-optional, and
FS scores in every execution lane behind one shared gate.**

1. **One gate, no native requirement, no row cap (#1810).** `_fs_use_bucket_route`
   replaces the drifted per-site conditions. Bucket becomes the FS default;
   without native it degrades to a memory-bounded per-block fallback (so
   `pip install goldenmatch` runs FS at scale), and with native the batched
   worker (#1794) scores a whole block-sorted bucket in **one** kernel call.
   `_default_n_buckets(height)` grows above the CPU floor targeting ~50K
   rows/bucket (cap 4096); emitted pairs are invariant to bucket count (a block
   hashes wholly into one bucket).
2. **FS in every lane (#1843 / #1844).** Distributed (`score_blocks_distributed`)
   and chunked (`ChunkedMatcher`) score FS per-partition/chunk via the bucket
   scorer; strategy/external blocking routes to a new
   `score_probabilistic_external_blocks` (bucket-lane machinery over the
   strategy's own `BlockResult` list, since the bucket scorer cannot re-derive
   those candidates from field hashes).
3. **One shared `EMResult` per FS matchkey (invariant).** The model is resolved
   **once** before dispatch and shared across all partitions/chunks — per-slice
   EM would produce inconsistent m/u weights. `GOLDENMATCH_DISTRIBUTED_FS_TRAIN_ROWS`
   (default 200k) bounds the driver-side training sample.
4. **Remaining bucket exclusions are contract-driven, not capability-driven:**
   explicit scale backends (`polars-direct` — an in-band planner choice),
   `GOLDENMATCH_FS_DEFAULT_BUCKET=0`, active profile/optimizer probes (calibrated
   on legacy block-size signals), and non-static strategies (→ external route).
   #1842 removed the over-broad columnar-opt-in exclusion and made the
   `FS_DEFAULT_BUCKET=0` hatch **loud** (warns, names the matchkey + the OOM
   consequence).

## Consequence

- **`person`-1M went from `MemoryError` to 139 s / 1.72 GB.** The bucket route +
  #1808's Arc exclude-handle (built once, not per bucket call — the #552/#688
  HashSet-rebuild pathology) + zero-copy Arrow entry are what make it affordable.
- **Block-size safety feeds the route** (all recall-only, silent when wrong):
  #1784 makes `_compute_max_safe_block = height // 40` with a scorer-aware ceiling
  (50k native / 10k numpy — native scores per-pair, no `NxN`) so the strong
  surname pass is not falsely rejected as oversized (which silently promotes
  surname into FS scoring and over-merges, `person` F1 0.97 → 0.34 at 100k);
  #1790 auto-splits oversized mega-blocks on the *default* (`skip_oversized=False`)
  path instead of scoring them whole; #1829 ports auto-split to the bucket lane
  and adds a **dense-matrix guard** (`GOLDENMATCH_FS_VEC_MAX_ELEMS`, default 2e9)
  that turns an unaffordable allocation into an actionable error; #1857 filters
  invalid/null `__block_key__` in `score_buckets` (the blocker already did) — on
  `person`-1M a 9,846-row null-postcode block was one 48.5M-comparison kernel
  call (20.5 s of 22.7 s); after, wall 31.8 → 21.4 s, FP 8,682 → 111.
- **Env flags:** `GOLDENMATCH_FS_DEFAULT_BUCKET=0` (loud legacy route),
  `GOLDENMATCH_FS_BUCKET_NATIVE=0` (byte-identical per-block loop),
  `GOLDENMATCH_FS_VEC_MAX_ELEMS`, `GOLDENMATCH_DISTRIBUTED_FS_TRAIN_ROWS`,
  `GOLDENMATCH_NATIVE=0` (non-native bucket lane still runs). Documented in
  `docs-site/goldenmatch/tuning.mdx`.
- Native-eligibility (which scorers the kernel can run) is
  [0042](0042-native-kernel-owns-fs-coverage.md); missing-value mode (which can
  force numpy) is [0041](0041-fs-missing-value-semantics.md). This ADR is *where
  the block goes*, those are *what scores it*.
