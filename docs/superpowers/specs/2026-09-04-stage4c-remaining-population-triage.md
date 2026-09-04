# Phase C, Stage 4c — Triage of the Remaining 20 Claims; the Clean Population Is Now Complete

**Status:** complete — combined with Stage 4b, all 45 clean high-confidence claims are individually verified
**Date:** 2026-09-04
**Prior:** `docs/superpowers/specs/2026-09-04-stage4b-full-rescue-triage.md` (the 25 mechanically-rescued claims,
already fully triaged), the sync_claims resolver fix (PR #2863, ambiguous-target flag +
class-claim coverage spans)

## Why this document exists

After PR #2863 landed, the "unenforced" text-only baseline dropped from 53 to 45 (8 claims
moved into the new `unenforced_ambiguous_target` bucket, correctly excluded from ordinary
triage). Re-running coverage-rescue against the fixed resolver mechanically rescued 3 MORE
claims that were invisible before the class-span fix (`CanonicalizationEval`, `VectorIndex`,
`FreshnessWithMaxAgeStrategy`). That left 20 claims with no prior individual verification:
the 3 newly-rescued class claims, `LintInput` (a 4th class claim that did NOT get rescued),
and 16 claims with zero coverage-context overlap at all (no test co-executes the claimant and
target, so there was no shortcut — each needed a genuine broad search of the test suite, not
just a read of an already-identified candidate). One of those 16, `identity/snowflake_backend.py:
_rel_expr`, was already confirmed enforced by prior work (PR #2849, the AST-string-reference
case) and was not re-verified here.

19 of the 20 were read fresh this pass (18 by dispatched investigation, 1 —
`tui/engine.py:_run_pipeline`, the historical incident that motivated building this whole tool
— read directly, given what it is).

## Full result: the 19 fresh verdicts

| claim | claimant → real target | verdict |
| --- | --- | --- |
| `core/embedding_ops.py:267` | `CanonicalizationEval` → `EvalResult` | **FALSE POSITIVE** |
| `core/vector_index.py:72` | `VectorIndex` → `retrieve_similar_records` | **FALSE POSITIVE** |
| `plugins/builtin/business.py:147` | `FreshnessWithMaxAgeStrategy` → *(not a cross-symbol claim)* | **PATTERN CLAIM** |
| `core/config_lint/registry.py:55` | `LintInput` → `ColumnProfile`/`data_profile_column_stats` (retargeted) | **FALSE POSITIVE** |
| `backends/datafusion_spine.py:214` | `_resolve_single_weighted_matchkey` → `score_blocks_datafusion` | **PATTERN CLAIM** |
| `backends/score_buckets.py:530` | `_ensure_legal_forms_installed` → `_initialism_match_single` | **TRUE POSITIVE** |
| `backends/score_buckets.py:1395` | `score_buckets` → `filter_eq` | **TRUE POSITIVE** |
| `backends/score_duckdb.py:50` | `score_blocks_duckdb` → `score_blocks_parallel` | **FALSE POSITIVE** |
| `core/config_critique.py:980` | `_add_blocking_pass` → `apply_quality_aware_blocking` | **FALSE POSITIVE** |
| `core/perceptual.py:263` | `phash_image_batch` → `phash_image` | **FALSE POSITIVE** |
| `core/perceptual.py:399` | `radial_align_similarity` → `audio_ber_aligned` | **PATTERN CLAIM** |
| `core/perceptual.py:559` | `audio_ber_aligned` → `audio_ber` | **FALSE POSITIVE** |
| `documents/classify.py:23` | `_strip_fence` → `parse_message_text` | **FALSE POSITIVE** |
| `identity/resolve.py:203` | `_batch_fingerprint_enabled` → own per-row vs. batched path (retargeted) | **TRUE POSITIVE** |
| `spark/autoconfig.py:108` | `_boundary_columns` → `core/autoconfig.py`'s exact full-frame pass (retargeted) | **FALSE POSITIVE** |
| `spark/identity.py:45` | `record_id_for_row` → `_record_id_candidates` | **TRUE POSITIVE** |
| `spark/identity.py:820` | `build_identity_graph_incremental` → `resolve_clusters` | **FALSE POSITIVE** |
| `web/pair_prose.py:27` | `with_prose` → own `pair` parameter, self-referential (retargeted) | **FALSE POSITIVE** |
| `tui/engine.py:170` | `_run_pipeline` → *(historical narrative, not a live claim)* | **PATTERN CLAIM** |

**Tally: 5 TRUE POSITIVE, 11 FALSE POSITIVE, 4 PATTERN CLAIM** (19 fresh + the 1 already-known
`_rel_expr` TRUE POSITIVE brings this pass's real population to 20/20 individually accounted for).

## The auto-resolver got the target wrong on 5 of these 19 — and it mattered differently each time

This is the most important structural finding of this pass. `_locate_target`'s bare-word
resolution being *unique* (only one function anywhere with that name) does not mean it's
*right* — five separate claims resolved to a real, unique, completely unrelated symbol, and
each required reading the claim's own prose to find what it actually meant:

- **`FreshnessWithMaxAgeStrategy`** — "parallel to `values`" resolved to
  `LazyClusterDict.values` in an unrelated module. The real referent is the claimant's own
  local variables `dates`/`values`, zipped together — not a cross-module symbol at all. Not
  even a mis-resolution in the usual sense; there was never a second symbol to point at.
- **`LintInput`** — "mirrors ... fields the auto-config profiler already computes" resolved to
  a nested helper function named `fields` in an unrelated module. The real target, found by
  reading the claim text and matching its named fields (`cardinality_ratio`, `null_rate`,
  `col_type`) against the codebase, is `ColumnProfile`/`data_profile_column_stats`. Retargeted,
  the claim is a genuine and currently real gap: `LintInput`'s populator duplicates the
  ratio/null-rate formula locally rather than calling the shared helper, and no test compares
  the two.
- **`identity/resolve.py:_batch_fingerprint_enabled`** — "byte-identical to ... the per-row
  path" resolved to a FastAPI route handler named `row`. The real claim is internal to
  `resolve.py` (per-row vs. batched fingerprint computation), and — unlike the other four —
  turned out to already be thoroughly tested
  (`test_record_ids_byte_identical_batch_vs_per_row_no_pk`). A wrong resolution masking an
  already-correct, already-tested claim — the same shape `canonical_soundex` already
  demonstrated for the cross-language blind spot, now confirmed for a plain wrong-resolution
  too, not just a cross-language one.
- **`spark/autoconfig.py:_boundary_columns`** — "same shape as" resolved to `core/cluster.py:keys`.
  The claim's own text names `core/autoconfig.py` explicitly; the real target is that module's
  exact-full-frame surrogate-key pass. Retargeted, still a confirmed real gap — no test
  compares the Spark and core surrogate-key boundary logic.
- **`web/pair_prose.py:with_prose`** — "copy of `pair`" resolved to `identity/mediation.py`'s
  unrelated `pair` property. The real claim is self-referential (the function's own `pair`
  dict parameter should come back with only a `prose` key added) — also a confirmed real gap,
  since existing tests only check for the presence of `"prose"`, never that every original key
  survives unchanged.

**Net effect on THIS pass's own tally:** 2 of these 5 (`_batch_fingerprint_enabled`,
`record_id_for_row` — the latter resolved correctly) turned out fine; 3 (`LintInput`,
`_boundary_columns`, `with_prose`) are real gaps once correctly retargeted, correctly counted
as FALSE POSITIVE above (not skipped, not misclassified as PATTERN CLAIM) because the
underlying claim IS real and checkable, just against a different symbol than the tool named.

## Two new pattern-claim shapes, beyond the two Stage 4b already named

Stage 4b's pattern claims were direct delegation (nothing to diverge) and cross-language
targets (not Python at all). This pass adds two more:

**Shared call, not independent implementation.**
`_resolve_single_weighted_matchkey` "mirrors" `score_blocks_datafusion` because both literally
import and call the same `_validate_matchkey` function object from the same module — not two
implementations of the same idea that could drift apart, one implementation invoked from two
places. Same non-claim shape as Stage 4b's `_suggest_negative_evidence`, just via a shared
downstream call instead of a shared upstream one.

**Historical narrative matching the keyword regex.**
`tui/engine.py:_run_pipeline`'s docstring no longer makes a live claim at all — it explains,
in past tense, why the OLD `_run_pipeline` (which really did say "mirrors run_dedupe" and
really did drift, shipping the ImportError this whole audit programme's motivating incident is
named for) was deleted and replaced with a thin delegation wrapper
(`result = run_dedupe_df(df, config, ...)`). `claims.py`'s `CLAIM_PATTERN` regex matched the
word "mirrors" inside `its own docstring **said** "mirrors run_dedupe"` — a quotation of dead
prose, not a present-tense assertion about current code. Once matched, it's also a delegation
pattern claim (nothing to diverge, matching the first Stage 4b shape) — but the root cause is
distinct and worth naming: a claim scanner built on regex-matching a keyword cannot
distinguish "this docstring is currently claiming X" from "this docstring is currently
describing, in a comment, that some code used to claim X." No fix proposed here — flagging it
as a fourth blind-spot shape, the way Stage 4b flagged the first three.

**A cross-domain analogy, not an equivalence.**
`radial_align_similarity` names itself the "angular counterpart" to `audio_ber_aligned`'s
time-offset search — an explicit statement that the two apply the SAME ALGORITHMIC APPROACH to
different domains (image correlation vs. audio bit-error-rate), operating on different data
types with different comparison metrics. Not comparable outputs, ever — same shape as Stage
4b's `simhash_band_hashes`/`_do_transform_columnar`.

## Combined totals: the entire clean high-confidence population is now individually verified

Stage 4b triaged the 25 mechanically-rescued claims (13 TRUE POSITIVE / 8 FALSE POSITIVE / 4
PATTERN CLAIM). This document triages the remaining 20 (5 / 11 / 4). Together, that is **45 of
45** — the complete `unenforced` + `coverage_enforced` population after PR #2863's ambiguity
fix, with zero claims left unverified by extrapolation:

| | Stage 4b (25) | Stage 4c (20) | Combined (45) |
| --- | ---: | ---: | ---: |
| TRUE POSITIVE (confirmed enforced) | 13 | 5 | **18 (40%)** |
| FALSE POSITIVE (confirmed real gap) | 8 | 11 | **19 (42%)** |
| PATTERN CLAIM (not a real claim) | 4 | 4 | **8 (18%)** |

The originally-reported "53 high-confidence findings" is now fully decomposed: 8 were
ambiguous-target artifacts (PR #2863), and of the remaining 45, 18 are confirmed false alarms,
8 need a docstring fix instead of a test, and **19 are confirmed real, individually-verified
gaps** — not an estimate, an exact count with a named test file (or absence of one) for each.

## What remains, precisely scoped now

1. **19 confirmed real gaps need tests written.** Full list, combining both documents:
   Stage 4b's 8 (`_measure_mean_token_set_size`, `_pass_row_keys`, `run_golden_fused_arrow`,
   `_full_scan_streaming`, `_rc_union_isolated`, `simhash_band_hashes`,
   `_do_transform_columnar`, `_migrate_cluster_decision_columns`) plus this document's 11
   (`CanonicalizationEval`, `VectorIndex`, `LintInput`/`ColumnProfile`, `score_blocks_duckdb`,
   `_add_blocking_pass`, `phash_image_batch`, `audio_ber_aligned`, `_strip_fence`,
   `_boundary_columns`, `build_identity_graph_incremental`, `with_prose`).
2. **8 pattern-claim docstrings need softening**, combining Stage 4b's 4
   (`_suggest_negative_evidence`, `customer_360`, `emit_semantic_model_from_store`,
   `_tool_score_strings`) with this document's 4 (`FreshnessWithMaxAgeStrategy`,
   `_resolve_single_weighted_matchkey`, `radial_align_similarity`, `_run_pipeline`).
3. **The 8 ambiguous-target claims are still completely untriaged** — a genuinely separate,
   not-yet-started body of work. Unlike the 45 above, these were never individually read at
   all; PR #2863 only stopped them from masquerading as ordinary findings. `canonical_soundex`
   is known among them (cross-language, already confirmed enforced by C1). The other 7
   (`_ensure_alias_tables_installed`, `_iso_date_digits`, `prep_rows`, `edges_by_kind`,
   `_tool_score_pair`, `build_incremental_nodes`, `from_dataframe`) need the same real-target
   identification this document did for 5 unique-but-wrong resolutions, before they can even
   be triaged as true/false positive.
4. **The historical-narrative blind spot** (`_run_pipeline`'s shape) has no fix proposed here,
   named for a future pass the way Stage 4b named the cross-language and AST-string shapes.

## Being wrong about this document

All 19 fresh claims here were read individually — the same discipline as Stage 4b, no sampling.
Five required identifying a different real target than the one the tool resolved; those five
were retargeted using the claim's own prose (not guessed), and the retargeted claim was then
verified the same way as any other. The one item not independently re-verified this pass,
`_rel_expr`, rests on prior work (PR #2849's own review, C1's read) — solid, but not re-checked
here. The 8 ambiguous-target claims remain a real, sized, unstarted piece of work, not folded
into either total above.
