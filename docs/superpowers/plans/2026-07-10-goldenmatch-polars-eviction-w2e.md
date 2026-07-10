# GoldenMatch Polars eviction — W2e plan (expression long tail)

Parent: `2026-07-10-goldenmatch-polars-eviction-w2.md`. Predecessors: W0-W2d.
Recon: 2026-07-10 (full report in the W2e recon; key findings inlined).

W2e ships as FOUR sub-batches, each its own PR (same no-stacking discipline):

## W2e-1 — standardize arrow twins

Extend `arrow_derive` with the six new pc constructs the `_NATIVE_STANDARDIZERS`
registry needs: null-if-empty (`pc.if_else` over `utf8_length==0`; null input
stays null — named fixture), `utf8_lpad` (zip5 pad), one-arg to-end slice
(phone), `match_substring`/`match_substring_regex` + `and_` (email), nested
if_else (phone len==11 branch). `trim_whitespace`'s `\s+` MUST reuse the
`_WS_CLASS` Unicode class (same RE2-vs-Rust hazard W2a solved).

- **Titlecase is HIGH-RISK**: `pc.utf8_title`'s word-boundary rules vs
  Python `str.title()`/Polars `to_titlecase` are unverified. Parity fixture
  FIRST (apostrophes, hyphens, digits, non-ASCII); if it reddens,
  `name_proper` DECLINES to the pure-Python `STANDARDIZERS` fallback.
- **`address` always declines** on the arrow backend (split + list.eval +
  coalesce + dict-replace has no pc analog) — the pure-Python `std_address`
  is the byte-exact oracle, mirroring arrow_derive's `address_normalize`
  treatment.
- Port `apply_standardization` to per-column `derive_standardized_column` +
  batched attach; the fallback oracle is the `STANDARDIZERS` registry (NOT
  `apply_transforms` — different function set). Preserve the
  `GOLDENMATCH_STANDARDIZE_STAGED` collect/lazy round-trip hook (maps to a
  materialize no-op on arrow).
- Gates: `test_standardize.py` unedited + arrow parity twins.

## W2e-2 — matchkey chains

- Generalize the derive vocabulary to PER-FIELD transform lists:
  `derive_composite(fields: Sequence[tuple[field, transforms]], sep="||")` —
  matchkey applies a different chain per field (arrow_derive's `block_key`
  applies one chain to all fields). W2a already pinned concat_str==emit_null.
- Weighted matchkey -> null-literal column (`pa.nulls(n, large_string())`).
- Derived-NE space-join needs `fill_null("")`-then-concat.
- Port `compute_matchkeys` + `precompute_matchkey_transforms`: preserve the
  blake2b `__xform_<sig>__` naming (backend-agnostic) and the EAGER
  materialized-column caller contract (:312-319). `_emit_matchkey_profile`
  stays Polars behind the emitter gate (W3 reductions).
- Gates: `test_matchkey.py` unedited + arrow twins.

## W2e-3 — golden fast path: EXTEND golden_fused, don't port expressions

Recon verdict: the two Polars fast paths own EXACTLY the config surface
`golden_fused` declines (`most_complete`/`first_non_null`, no rules, no
quality — golden_fused.py:466-467 declines when `_polars_native_eligible` is
True, deliberately deferring to the cheaper polars path). Acero has no
grouped arg-min with composite stable tie-break, so porting
`sort_by(struct).first()` would be a risky reimplementation of the #870
tie-break.

**Default decision: make the `_polars_native_eligible` decline
polars-backend-only** — on the arrow backend the merged Rust kernel handles
these configs directly (it already implements both strategies). The Polars
fast paths stay UNTOUCHED for the polars backend. Fall back to a
`group_agg_survivors` seam-op port ONLY if measurement shows the kernel path
>10% slower (spec 4.3).
- Gates: test_golden.py, test_golden_from_frames_parity.py,
  test_pipeline_fused_golden.py, test_golden_provenance_batch.py,
  test_golden_fused*.py.

## W2e-4 — survivorship + spine tail

- `survivorship/native.py` (3rd-densest file): same kernel-vs-port decision
  as W2e-3 (tied to golden_fused Stage 5 group strategies).
- `pairs.py` (`dedup_pairs_max_score_*` — arrow twin half-exists),
  `incremental._prepare_incremental` (diagonal concat + row_id/source),
  `cluster_pairscores.py`, the deferred FS-classic `_field_values_for_block`
  extraction, thin blocking/transform helpers.
- The both-backends 1M bench gate lands HERE (bench script gains a
  `load_file(return_frame=True)` ingest path + the W2d `frame` input).

Out of W2e (W3/W4 per the W0 audit): autoconfig/controller/profiling,
LLM/reporting surfaces, distributed, chunked IO tail.
