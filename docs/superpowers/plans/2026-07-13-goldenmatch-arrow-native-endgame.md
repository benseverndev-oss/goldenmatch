# GoldenMatch Arrow-native endgame — the full map

Mission (Ben, 2026-07-13): make GoldenMatch **Arrow-native, Rust, fused
compute** — all of it, not just the spine. This map is the authoritative
inventory of where every compute path stands and the batch plan to the
end-state. Supersedes the widening roadmap's transitional notes in
`2026-07-12-goldenmatch-arrow-descent-3x.md` (that plan's D1-D5/D2s/deep-D2/
W-1..W-7 history stands; this plan owns what remains).

## End-state definition (three invariants, testable)

1. **Zero polars at runtime.** `import goldenmatch` + any pipeline run with
   any config loads no polars unless an EXTRA-GATED integration is enabled
   (goldencheck/goldenflow keep polars as THEIR declared dep only if their
   own arrow surfaces cannot cover the call). Proof: an import-hook CI test
   (the goldencheck 2.0.0 precedent) that fails the suite if `polars` lands
   in `sys.modules` on the arrow lane.
2. **All engine compute arrow-native.** Every stage computes on
   `pa.Table`/arrow buffers via the Frame seam or `pa.compute`; per-batch
   parity gates (the differential harness + per-feature pins).
3. **Hot paths in owned Rust kernels, fused where FFI-crossing repeats.**
   "Hot" is MEASURED (the audit lesson: wall-clock on real shapes, never
   static counts). Fusion only where a profile shows per-call FFI/materialize
   overhead — the match_fused/golden_fused precedent (wall wash, ~2x RSS win;
   scale/composability is the justification, not raw speed).

## Layer inventory (2026-07-13, post W-1..W-7)

### Layer 1 — DONE: Arrow-native + Rust fused (the hot spine)
- `run_match_fused_arrow` / `run_match_fused_multipass_arrow`: block + score
  + dedup + cluster in ONE FFI call (native `fused.rs`).
- `run_golden_fused_arrow`: survivorship kernel, pa in -> pa out (deep-D2),
  indices returned, gather via `take()`.
- Bucket scoring: `score_block_pairs_arrow` (zero-copy buffers into Rust
  rapidfuzz), seam scaffolding (D5), per-lane bucket assignment.
- Native crate surface already present (packages/rust/extensions/native/src):
  autoconfig, block, bloom, cluster, documents, featurize, fused, golden,
  hash, pairs, perceptual, score, sketch, suggest.

### Layer 2 — DONE: Arrow-native Python (seam / pa.compute, no Rust)
Ingest (pyarrow csv/parquet readers), eager standardize/matchkeys,
`precompute_matchkey_transforms_frame`, static blocking + exact match
(Acero), splits/report/outputs (native parquet), validation (W-3),
lineage reads (W-2), probabilistic data reads (W-6), clustering data prep.

### Layer 3 — THE REMAINING WORK: bridged polars compute
Every entry below runs ON the Frame lane today via a zero-copy `pa->pl`
bridge; the compute substrate is still polars. Ordered by plan batch.

## Batch plan (A-series: arrow-native ports; K-series: Rust/fused kernels)

Each batch: own PR, parity pin(s) vs the bridged implementation
(byte-identical or documented-equivalence), differential-harness green,
bridge deleted in the same PR. Perf gates only where a stage is hot at 1M+.

### A1 — quality via goldencheck's OWN arrow surface  [replaces a FINAL bridge]
goldencheck 3.0.0 shipped the Arrow Flip: default scan is polars-free
(`goldencheck/core/frame.py` + `kernels.py`, fused string digest). Port
`run_quality_check`'s integration adapter to hand goldencheck a `pa.Table`
and consume its arrow results; polars loads ONLY if goldencheck's own
gated paths need it. Also flip `compute_quality_scores` (the golden
quality-weighting bridge) onto goldencheck's arrow cell-quality if exposed;
if not, file the goldencheck issue and keep that single bridge with a
pointer. RECON AT START: goldencheck's public arrow entry signature.

### A2 — transform via goldenflow's OWN arrow/fused surface  [replaces a FINAL bridge]
goldenflow has owned Rust kernels + fused columnar apply (str+num+nullable,
default-on) and a WASM build. Port `run_transform`'s adapter to the
arrow/fused apply path. Same recon caveat as A1.

### A3 — analyzer + postflight + auto-suggest arrow entries
W3d already seamed the block analyzer's REDUCTIONS; the residue is
`analyze_blocking`'s entry frame ops + sampling (Frame.sample exists,
statistical contract) and `postflight`'s score-histogram reads (pure
Python over pair lists + seam column reads — small). Deletes two bridges.

### A4 — memory corrections arrow-native
`_build_hash_to_rids` is a polars expr chain ending in per-row sha256.
REUSE `fingerprint-core` (the cross-surface record-id hash crate — :h1:
canonical fingerprint) instead of re-porting the ad-hoc sha256: one
kernel, already cross-surface-pinned. Fallback: seam cast_str reads +
Python hash (correct, slower). Deletes the memory bridge.

### A5 — identity arrow-native (LARGEST single port)
`identity/resolve.py` (~1000+ lines): row payload extraction
(select_dicts — trivial), record-id fingerprinting (fingerprint-core,
same as A4), and the incremental mini-frame machinery (schema-aligned
concat for match_record). Split: A5a resolve_clusters payload path
(the dedupe-pipeline surface — select_dicts + fingerprints); A5b the
incremental match_record mini-frames (concat_frames seam op exists;
schema alignment via select_cast). SP-C already feeds ClusterFrames
directly. Deletes the identity bridge.

### A6 — semantic blocking + domain extraction arrow-native
- Semantic sources (initialism / alias / embedding candidates): key
  derivation is string ops (seam derive twins cover); embedding retrieval
  goes through goldenembed (model-bound, representation-light). The
  `__raw__` capture is already seam (W-7).
- Domain extraction (`extract_features`): regex/derive columns — seam
  derive_transformed_column-shaped; port the feature exprs to arrow_derive.
- Both are offline-testable except the embedding model (mock the model,
  pin the frame handoffs — the W-7 pin pattern).

### A7 — throughput sketch tier arrow-native
The `_tp` block: text extraction (seam cast_str reads), simhash/minhash
sketching — native `sketch.rs` + `simhash` kernels ALREADY EXIST; the
blockers are the polars-local glue reads. Port reads to seam; sketches go
straight to the existing kernels. Deletes the throughput bridge.

### A8 — rerank + LLM tail
Model-bound stages; frames only supply row text. Port the text extraction
to seam reads (select_dicts / column reads). Smallest batch; deletes the
last two call-site bridges.

### A9 — golden slow-path demux retirement
After A1-A8 the remaining `_as_polars_df` sites are the golden DECLINE
replays (fast-columnar `build_golden_records_df` + batch builder) and the
from-frames recompute. End-state options, decided by measurement:
(a) widen `golden_fused_ready` coverage until declines are rare, keep a
seam-Python slow builder for the residue (port build_golden_records_batch's
per-cluster merge to seam reads — it is already to_list-shaped); or
(b) port the fast columnar path to pa.compute. Either way the polars demux
dies here. The order-sensitivity lesson (Acero vs polars join row order)
pins the parity fixtures.

### K1/K2 — VERDICT: MEASURED NO-GO (2026-07-13)

1M frame-lane measurement (weighted jaro config, soundex python-fallback
chain in the mix, bucket backend): matchkey precompute = **1.1s absolute,
13.9% of a 7.6s wall**. A native chain kernel's ceiling is ~1s at 1M --
not kernel-worthy; the wall lives in scoring/clustering, which the fused
match kernel already owns. K2 (fused prep) has nothing to fuse: the prep
stages are sub-second at this scale. Per the measure-first rule (the
goldenflow arrow-everywhere ~3% precedent), a measured no-go IS the
completed outcome. Re-open only if a 10M+ profile on real shapes shows
the precompute share inverting.

### K1 — precompute/derive kernel (MEASURE FIRST) [superseded by the verdict above]
At 10M the matchkey precompute was 90s polars-batched; the arrow lane loops
per-signature. Profile at 1M/10M on the frame lane; if the python-fallback
transform chains (soundex/metaphone) dominate, add a native chain kernel
(hash.rs/featurize.rs adjacency). If the native-expr chains dominate, the
arrow_derive vectorized path may already win — do nothing (the
goldenflow-kernel lesson: measure-first byte-identical wins were 3-8x, NOT
where predicted).

### K2 — fused prep (SPECULATIVE — only if K1 profiling shows FFI churn)
quality-scan + transform + standardize + matchkey precompute in one pass
over the table (the goldencheck fused-scan shape). Only if a 10M profile
shows the stage seams costing real wall; otherwise skip — fusion for
composability is already delivered by match_fused.

### D6 — the deletion (the finish line)
Preconditions: A1-A9 merged; frame-lane bench config exists (see below);
import-hook zero-polars test green on the full arrow-lane suite.
Contents: delete the polars opt-out value, PolarsFrame + PolarsColumn,
`_polars_dtype`, polars constructor branches, the `.lazy()` classic prep
block (the Frame branch becomes THE pipeline), `_as_polars_df`;
`_polars_lazy` proxy survives only inside goldencheck/goldenflow adapters
if A1/A2 recon finds gated residue. polars -> dev-dep group. Parity suites
collapse single-backend. Docs sweep. Ships as a 3.x minor.

## EXECUTION STATUS (2026-07-13, same-day)

**IMPLEMENTED (A1-A8, stacked behind W-1 #1731):** bridge ledger 21 -> 8.
- A1 quality: scan arrow-native via goldencheck's own surface; bridge
  narrowed to apply_fixes-when-findings; version-SKEW fallback (installed
  goldencheck predating its Arrow Flip bridges+retries; floor bump at D6).
- A2 transform: adapter dual-rep, exclusions on the seam; bridge narrowed
  to goldenflow's auto-detect engine call. FILED: goldenflow arrow
  auto-detect + goldencheck apply_fixes arrow port (sibling batches).
- A3 analyzer/postflight: candidate transforms via derive_transformed_
  column (compound keys pipe-join with concat_str null propagation);
  _sample_block_sizes via derive_block_key + group_len.
- A4 memory: **MAP CORRECTION** -- fingerprint-core replacement was WRONG
  (record_hash is PERSISTED; format change breaks re-anchoring). Format-
  preserving seam port instead; both hash contracts byte-stable.
- A5 identity: dedupe path seam-native; **A5b RESIDUAL** = fingerprint
  canonicalization (canonicalize_records_df/batch_fingerprints entry
  bridges -- the :h1: dtype contract ports against the fingerprint parity
  corpus) + the incremental match_record mini-frames + the Ray branch.
- A6 semantic/domain: extractors seam-native (backend-detected attach);
  _apply_domain_extraction dual-rep; semantic receives LANE-NATIVE handles.
- A7 throughput: tier-local polars import GONE; seam cast_str/fill_null +
  semantic_dtype fallback pick; sketches on the existing kernels.
- A8 rerank/llm/boost: text extraction via select_dicts; 4 bridges retired.

**REMAINING (each needs its own dedicated effort):**
- A9 golden demux: build_golden_records_batch has THREE internal routes
  (polars-native columnar, survivorship native kernel, slow sorted
  oracle) -- port against the golden fixture corpus; frames-path decline
  recompute stays (join-order hazard). The remaining 8 ledger bridges are
  all A9-adjacent (demux replays, quality_scores/cell_quality, adaptive
  refiner, NE-on-exact) + the identity/W-4 pin rename.
- K1/K2: remote 1M/10M profiles first (frame-lane bench config
  prerequisite -- zero-config bench now ENGAGES the lane post W-5).
- D6: requires the full W+A stack merged, the import-hook zero-polars
  test, floor bumps (goldencheck>=3.0), and a 3.x minor release.

## D6 SHIPPED SCOPE (2026-07-13) -- deliberate deviation from the map's
"delete PolarsFrame" phrasing

The A-series taught us the polars-PRESENT-optimization architecture: with
polars installed, the fast columnar golden, vectorized survivorship, and the
100M-pair join stay byte-identical to 3.0.x; without it, seam-native routes
carry the run (gate-proven). DELETING PolarsFrame/the classic lane would
break exactly those optimizations and the GOLDENMATCH_FRAME=polars users.
So D6 ships as: polars OUT of required deps + a [polars] extra + the
zero-polars gate + the resolver guard + 3.1.0. Structural deletion of the
classic lane (if ever) is a future major, not this program's goal --
"Arrow-native, Rust, fused compute" is satisfied with polars as an optional
accelerant, mirroring goldenmatch-native itself.

## Cross-cutting gates

- **Frame-lane bench config** (FIRST, before A1): a bench-zero-config
  variant that is frame-lane-eligible (quality/transform run via bridges
  today = realistic) at 100K/1M, so every A-batch has a wall+RSS gate.
  The zero-config bench takes auto_config and now ENGAGES the lane (W-5
  removed the preflight decline) — verify, else add a fixed-config bench.
- **Bridge-count tripwire**: a unit test asserting the exact set of
  remaining `_as_polars_df` call sites; every A-batch shrinks the list —
  no silent new bridges.
- **Parity discipline**: breadth sweeps with symmetric-decline asserts
  (the deep-D2 date32 lesson: one happy-path pin != coverage).
- **Measure-first for every K-batch**: 5-run median wall on real shapes at
  1M+ before designing; byte-identical output or no ship.

## Sizing (rough, PR-count)

A1 2 / A2 2 / A3 1-2 / A4 1 / A5 3-4 / A6 2-3 / A7 1-2 / A8 1 / A9 2-3 /
K1 1-2 (post-profile) / D6 2 (deletion + docs/release). ~20-25 PRs.
Order: bench config -> A1/A2 (default-config compute goes arrow) ->
A4/A3/A8 (small) -> A7 -> A6 -> A5 (largest) -> A9 -> K1(-K2) -> D6.
