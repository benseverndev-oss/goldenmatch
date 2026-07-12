# GoldenMatch Polars eviction — W5 plan (the flip, v3.0.0)

Spec row: polars out of deps, PolarsFrame + `GOLDENMATCH_FRAME` deleted, test
assertions migrate to Arrow, results become `pa.Table`. W0-W4 merged/queued
(#1616 → #1677). Recon 2026-07-11.

## Recon verdicts (agent-verified, file:line)

1. **The W2d ingest shim names its own removal point** (pipeline.py:803-814):
   arrow reaches ingest and is converted back to polars-lazy. Four stages block
   arrow flowing past it:
   - `_add_row_ids` (pipeline.py:715-733): seam ops NEARLY cover it; the
     conditional "reuse existing __row_id__" branch (#844 collision guard) has
     no op → small new op `ensure_row_ids(name, offset)` or inline branch.
   - `apply_standardization` (standardize.py:408-506): per-column work is
     FULLY mirrored by `derive_standardized_column` (frame.py docstring cites
     standardize.py:443-501); only the LAZY orchestration (collect_schema +
     one fused with_columns) is unported. Eager Frame loop replaces it; cost =
     losing polars cross-column fusion (measure, don't assume).
   - `compute_matchkeys` (matchkey.py:256-395): mirrored by `derive_matchkey`
     + `derive_ne_joined`; residue is signature-dedup batching + profile
     emission orchestration.
   - **domain extraction (domain.py:431+, ~23 pl uses, ZERO seam refs): the
     one real coverage gap.** Already eager post-collect (pipeline.py:455).
     Needs derive ops or an arrow twin for the per-domain `_extract_*`
     with_columns chains.
2. **Result surface**: 5 public frame fields flip to pa.Table — DedupeResult
   golden/dupes/unique (_api.py:138-141), MatchResult matched/unmatched
   (:279-280); `_repr_html_` (to_dicts) + `to_csv` (write_csv) re-render from
   Arrow (pyarrow csv writer).
3. **Core residue classes**: cluster/golden/scorer/blocker already route
   through the seam; their raw pl. is (b) the exact-polars DELEGATION TARGETS
   PolarsFrame imports + (c) result construction. standardize is redundantly
   mirrored (cheap); domain is the gap.
4. **Test migration: ~334 of 461 test files** touch polars result shapes —
   the single biggest W5 work item (acknowledged in spec §4.2).
5. **Deletion scope**: `GOLDENMATCH_FRAME` refs are only 9 (frame.py x4 =
   resolve_frame_backend, fused_match.py:162, golden_fused.py:466,
   ingest.py:121) but resolve_frame_backend feeds every frame_from_*
   constructor — deletion = hardwire arrow, not 9 line-deletes. The
   differential/parity suites mostly become SINGLE-BACKEND, not deleted
   (test_frame_* / test_arrow_*_parity / io_arrow ingest parity).
   `_polars_dtype` + polars constructor branches die. AUDIT REQUIRED: the
   polars-backend-only decline conditionals in fused_match.py:162 /
   golden_fused.py:466 change meaning when arrow is the only backend.
6. run_dedupe's per-file loop is polars-concat-centric (collect + re-lazy +
   pl.concat, pipeline.py:821-825) → pa.concat_tables at the flip.

## Batching

- **W5a (2.x)** — close the coverage gap: domain-extraction derive ops or
  arrow twin (fixtures-first, per-domain corpora); `ensure_row_ids` op with
  the #844 reuse branch pinned. Gates: domain suites unedited.
- **W5b (2.x, SUB-BATCHED; recon 2026-07-11 mapped _run_dedupe_pipeline)** —
  arrow flows past ingest UNDER THE ENV GATE, boundary moving down in steps:
  - W5b-1 (SHIPPED #1682): ingest front eager (column_map/validate/__source__/
    ensure_row_ids); ONE post-ingest shim at pipeline.py:852.
  - RECON FINDING: the polars-bound PREP BLOCK (quality=goldencheck
    quality.py:24, transform=goldenflow transform.py:27, autofix, validation
    validate.py, auto-config) sits BETWEEN ingest and standardize
    (pipeline.py:1620-1712) — the boundary cannot cross it wholesale. All
    prep stages are CONFIG-CONDITIONAL (skip when unset).
  - W5b-2: arrow-eager standardize+matchkeys for the NO-PREP config shape
    (the default/zero-config path): when the arrow lane is active and
    quality/transform/autofix/validation/autoconfig are unset, run
    derive_standardized_column + derive_matchkey/precompute eagerly on the
    Frame; shim right before build_blocks/block_scorer (blocker already
    seam-internal, blocker.py:381/737; scorer takes a clean DF handoff).
    Domain extraction (gated) declines the eager path when enabled.
  - W5b-3 (RESCOPED 2026-07-12): goldenmatch's polars removal does NOT
    require goldencheck/goldenflow to be polars-free. At W5e the
    quality/transform integrations become EXTRA-GATED: config.quality
    requires goldencheck (which carries polars as ITS dep until its own
    3.0 arrow program lands — project_goldencheck_arrow_fused_scan);
    the integration functions import polars lazily inside the gated path
    (transitively available when the extra is installed), so goldenmatch
    core never imports it. goldencheck's scan_dataframe takes pl.DataFrame
    (scanner.py:289; PyFrame.from_columns exists internally — a pa entry
    point rides goldencheck 3.0, not this program). W5b-3 therefore =
    seam ports for the goldenmatch-INTERNAL prep stages only: autofix
    (core/autofix.py ~15 pl uses) + validate (core/validate.py ~9);
    autoconfig already Frame-accepting since W3e.
  - Prep cache stores pl.DataFrame (pipeline.py:899) — becomes
    backend-typed at W5b-3.
  - Differential harness proves END-TO-END parity each step (controller
    expectations per-backend per the W3a sample contract). Wall + RSS both
    lanes on the 100K gate + 1M dispatch bench (fused-with_columns loss
    measured; >10% → multi-column derive op).
- **W5c (v3.0.0 branch)** — result flip: 5 fields → pa.Table, _repr_html_ /
  to_csv re-render, migration guide (pl.from_arrow one-liner), input
  polymorphism already in place. Fused/golden_fused backend-conditional
  audit. Ingest loop → pa.concat_tables.
- **W5d** — test migration (~334 files; mechanical to_dicts/pl.DataFrame →
  arrow equivalents in waves by directory, shard-aware per
  [[feedback_pytest_split_shard_shift_clobber]]).
- **W5e** — the deletion: polars out of deps ([polars] nowhere), PolarsFrame
  + _polars_dtype + polars constructor branches + env var + _polars_lazy
  proxy deleted; parity suites collapse to single-backend; fallback-contract
  doc (no-wheel platforms = Arrow+Python correct-but-slower); FULL
  rollout-docs-sweep (tuning.mdx, api-quick-reference, README, examples,
  CHANGELOG); v3.0.0 release + golden-suite floor bump lockstep; Rust bridge
  JSON→Arrow (deferred from W4f) rides this train.

## Risks

| Risk | Mitigation |
| --- | --- |
| Eager per-column standardize slower than fused lazy pass | W5b measures on the 100K gate + 1M bench; >10% → multi-column derive op (one seam call, fused impl per backend) |
| Domain extraction arrow twin drifts from polars semantics | Fixtures-first per-domain corpora; W5b differential harness covers e2e |
| 334-file test migration destabilizes shards | Directory-wave migration, rootdir-relative deselect checks, "N deselected" verification each wave |
| Backend-conditional declines (fused) invert meaning | Explicit audit item in W5c; behavior pinned by tests before the env var dies |
| v3.0.0 ships with a stale doc surface | rollout-docs-sweep is a W5e exit gate, not an afterthought |
