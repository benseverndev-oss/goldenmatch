# Phase C, Stage 4b — Full Hand-Triage of the 25 Coverage-Rescued Claims

**Status:** complete — all 25 claims individually verified, no more extrapolation.
**CORRECTION (2026-09-04, same day):** `_rc_union_isolated` below was verdicted
FALSE POSITIVE from the 3 shared coverage contexts available at the time.
Writing its test (see the test-writing pass this triage fed into) found a
real, dedicated test already exists --
`tests/test_distributed_randomized_contraction_wcc.py::test_randomized_contraction_agrees_with_two_phase_wcc`
-- invisible to this document's coverage-based method because it's gated
behind `pytest.importorskip("ray")` and never runs (so never generates a
coverage context) in an environment without Ray. Corrected to TRUE
POSITIVE below, with the tally updated (14 TRUE POSITIVE / 7 FALSE
POSITIVE / 4 PATTERN CLAIM). A fourth blind-spot shape, distinct from the
three named in this document's own body: a real test whose coverage
signal is invisible because it's conditionally skipped in the very
environment that produces the coverage data being checked against.
**Date:** 2026-09-04
**Prior:** `docs/superpowers/specs/2026-09-03-stage4-coverage-retriage.md` (the 5-item
spot-check and the ≈19% estimate this document replaces with a real count),
`docs/superpowers/specs/2026-09-02-c1-triage-findings.md`,
`docs/superpowers/specs/2026-09-02-c1-read-of-55.md`

## Why this document exists

Stage 4 spot-checked 5 of the 25 mechanically coverage-rescued claims and
found only 2 held up as real (test genuinely compares outputs), extrapolating
to "roughly 10 of 53 (≈19%)" genuine false negatives across the full
population — explicitly flagged as a small-sample estimate, not a count.
This document does the remaining work: all 25 rescued claims read by hand,
using the identical discipline (does a real test EXECUTE both halves AND
COMPARE them, or merely co-execute), replacing the extrapolation with an
exact count.

## Method

For each of the 20 not-yet-checked claims (17 fresh + 3 already independently
confirmed by C1's original read of `core/survivorship/native.py`), the
claimant and target functions were read directly, the coverage-proven shared
test contexts were computed (`scripts/sync_claims/coverage_enforcement.py`'s
own `function_spans()`/`function_contexts()` against the combined `.coverage`
DB from CI run 33793047003), and the most promising shared tests were read in
full to judge genuine comparison vs. incidental co-execution. Same standard
as Stage 4's 5-item sample and C1's original 8.

One addition this pass forced: a third verdict bucket. Four claims turned out
not to be real equivalence assertions at all — **PATTERN CLAIM**, matching
the reclassification Stage 4's own doc already applied to
`simhash_band_hashes` and `_do_transform_columnar`. A pattern claim is not a
real gap and not a real rescue; it needs its docstring softened, not a test
written. Two shapes produce it: (a) the claimant literally calls the target
and returns its value through with no independent logic in between
(`_suggest_negative_evidence`/`_lever_calibration`: same helper call, same
params, same seed; `customer_360`/`entity_profile`:
`profile = entity_profile(store, entity_id)`, one line;
`emit_semantic_model_from_store`/`write_resolved_catalog`:
`return write_resolved_catalog(...)` directly) — nothing can drift because
there is no second implementation to drift from; (b) the target is not a
Python symbol in this repo at all (`_tool_score_strings`'s "mirrors TS
`score_strings`" names the TypeScript SDK). The line drawn against the real
FALSE POSITIVE bucket below: a pattern claim's two sides are never
independently implemented, so "no test compares them" is close to
tautological; a false positive's two sides ARE independent implementations
that could genuinely diverge, and nothing currently would catch it.

## Full result: all 25 claims

| claim | claimant → target | verdict |
| --- | --- | --- |
| `core/scorer.py:1508` | `_alias_score_matrix` → `score_field` | **TRUE POSITIVE** |
| `core/survivorship/native.py:344` | `_scalar_resolution_rule` → `resolve_cluster` | **TRUE POSITIVE** |
| `core/survivorship/native.py:391` | `_scalar_value_expr` → `merge_field` | **TRUE POSITIVE** |
| `core/survivorship/native.py:483` | `_scalar_conf_expr` → `merge_field` | **TRUE POSITIVE** |
| `core/survivorship/native.py:644` | `_resolve_conditionals` → `resolve_cluster` | **TRUE POSITIVE** |
| `core/sketch.py:337` | `simhash_band_hashes` → `band_hashes` | **FALSE POSITIVE** |
| `core/transform.py:67` | `_do_transform_columnar` → `_do_transform` | **FALSE POSITIVE** |
| `core/memory/store.py:308` | `_migrate_cluster_decision_columns` → `_migrate_field_correction_columns` | **FALSE POSITIVE** |
| `config/from_splink.py:1034` | `_agree_index_for` → `convert_comparison` | **TRUE POSITIVE** |
| `config/splink_upgrade.py:309` | `_measure_mean_token_set_size` → `_measure_mean_length` | **FALSE POSITIVE** |
| `config/splink_upgrade_fanout.py:138` | `_suggest_negative_evidence` → `_lever_calibration` | **PATTERN CLAIM** |
| `core/arrow_derive.py:326` | `standardized_column` → `_try_build_native_chain` | **TRUE POSITIVE** |
| `core/autoconfig.py:6862` | `_pass_row_keys` → `_project_pass_pairs` | **FALSE POSITIVE** |
| `core/blocker.py:123` | `_fast_static_block_sizes` → `_build_static_blocks` | **TRUE POSITIVE** |
| `core/cluster.py:621` | `build_cluster_frames` → `_finalize_clusters` | **TRUE POSITIVE** |
| `core/cluster.py:1234` | `_columnar_presplit` → `compute_cluster_confidence` | **TRUE POSITIVE** |
| `core/golden_fused.py:469` | `run_golden_fused_arrow` → `build_golden_records_from_frames` | **FALSE POSITIVE** |
| `core/pipeline.py:2712` | `_fused_needed_src_cols` → `run_match_fused_arrow` | **TRUE POSITIVE** |
| `core/probabilistic.py:3096` | `_add_ne_matrix_contribution` → `_ne_fired` | **TRUE POSITIVE** |
| `core/standardize.py:269` | `_native_address` → `std_address` | **TRUE POSITIVE** |
| `db/sync.py:765` | `_full_scan_streaming` → `_full_scan_pipeline` | **FALSE POSITIVE** |
| `distributed/clustering.py:1430` | `_rc_union_isolated` → `two_phase_wcc` | **TRUE POSITIVE** (corrected, see note above) |
| `identity/profile.py:537` | `customer_360` → `entity_profile` | **PATTERN CLAIM** |
| `mcp/server.py:2292` | `_tool_score_strings` → `score_strings` | **PATTERN CLAIM** |
| `semantic/catalog.py:90` | `emit_semantic_model_from_store` → `write_resolved_catalog` | **PATTERN CLAIM** |

**Tally: 14 TRUE POSITIVE, 7 FALSE POSITIVE, 4 PATTERN CLAIM** (corrected;
originally 13/8/4, see the `_rc_union_isolated` correction above).

(`customer_360`→`entity_profile` and `emit_semantic_model_from_store`→
`write_resolved_catalog` were investigated and reported as FALSE POSITIVE by
the agents that read them — both correctly found no test independently
verifies the two sides. Reclassified to PATTERN CLAIM here on top of that
finding: both are direct delegation, `profile = entity_profile(...)` and
`return write_resolved_catalog(...)`, so there is no second implementation
that could diverge for a test to guard against, the same shape as
`_suggest_negative_evidence`/`_lever_calibration` below. This is a
consistency correction across the whole 25, not new evidence — see the
"two shapes" note above.)

## What this changes about the headline number

Stage 4's extrapolation (2/5 = 40% real, projected to ≈10/53 ≈ 19%) undersold
the true rescue rate. The full count is **14 of 25 (56%)** genuinely
enforced, not 40% (corrected from an original 13/25 — see the
`_rc_union_isolated` correction above). Of the 53 original high-confidence
findings:

- **14 (≈26%) are confirmed audit false alarms** — the text-only check's
  "unenforced" was wrong; a real test compares the claimant and target
  directly or one level of indirection out. These should be removed from any
  future gap-count entirely, the same way `_alias_score_matrix` already was.
- **4 (≈8%) are pattern claims, not real gaps** — no test is missing because
  there was never a real equivalence assertion to test (either the claimant
  delegates to the target directly, so there is no second implementation to
  diverge, or the real target isn't a Python symbol in this repo at all).
  These need a docstring fix (soften "mirrors"/"Mirrors" language), not a
  test.
- **7 (≈13%) are confirmed real gaps within the rescued pool** — the
  mechanical "coverage-rescue" was itself a false alarm: a test executes
  both functions but never compares them, and the two sides ARE independent
  implementations that could genuinely diverge. These are genuine findings
  that should go back into the "needs a test" list, not be treated as
  resolved.
- **28 remain outside this pass's scope** — still unenforced under both text
  and coverage checks. Only 2 of these 28 were previously investigated (both
  confirmed enforced by mechanisms this tooling can't see: a Rust
  cross-language target, and an AST-string-reference test). The other 26
  have not been individually re-verified by this or any prior pass — their
  composition should NOT be assumed to follow the rescued pool's ratio
  (14:7:4). Extrapolating from one pool to the other would repeat exactly
  the mistake this whole document exists to correct.

## Three claims worth reading individually

**`config/splink_upgrade_fanout.py:_suggest_negative_evidence` mirroring
`_lever_calibration` is a pattern claim, not a gap.** Both functions call the
identical `build_blocks` → `_sample_blocked_pairs(..., seed=ctx.seed)`
sequence with the same shared constant imported from the same module — the
"mirroring" is guaranteed by construction (same call, same seed), not an
independent claim that could diverge and needs a test watching for it.

**`mcp/server.py:_tool_score_strings` is a second instance of the
cross-language blind spot Stage 4 already named for `canonical_soundex`.**
The docstring reads "mirrors TS `score_strings` -> `{scorer, score}`" — "TS"
is the TypeScript SDK, not the Python `_api.py` function the target-resolver
picked (the only same-named Python function, so the resolution itself was
correct; the claim's real target just isn't Python). No test in this repo
can verify parity against a separate language's package. This confirms the
blind spot is a recurring class, not a one-off — worth prioritizing the fix
Stage 4 already recommended (`_resolve_target` should recognize a
non-Python target and route it to `unresolvable`, not `unenforced`).

**`core/golden_fused.py:run_golden_fused_arrow` mirroring
`build_golden_records_from_frames` is a false positive with a specific,
useful shape.** The real byte-identical comparison test that exists
(`test_golden_fused.py::_assert_provenance_parity`) verifies the fused
kernel against `build_golden_records_batch` — a *third* function, not the
one the docstring names. The codebase is very likely fine (the tested
relationship is adjacent to the claimed one), but the claim AS WRITTEN,
comparing specifically against `build_golden_records_from_frames`, remains
unverified. Worth a docstring correction pointing at the function that's
actually tested, separate from whether a new test gets written for the
literal claim.

## What this means for next steps

Three distinct, differently-sized pieces of follow-up work, not one:

1. **8 confirmed real gaps need tests written** (or the claim downgraded if
   investigation shows it shouldn't have been made at byte-identical
   strength): `_measure_mean_token_set_size`, `_pass_row_keys`,
   `run_golden_fused_arrow` (note: retarget the docstring to
   `build_golden_records_batch`, the function actually tested, or write a
   new test against `build_golden_records_from_frames` specifically — see
   above), `_full_scan_streaming`, `_rc_union_isolated`, `simhash_band_hashes`,
   `_do_transform_columnar`, `_migrate_cluster_decision_columns`. (The
   latter three were labeled plain FALSE POSITIVE in Stage 4's original
   table but described there in language that is, on this document's
   taxonomy, closer to pattern-claim reasoning — "not comparable outputs,
   ever" / "incompatible input shapes" / "different columns for different
   features, nothing to compare." They are kept in the "needs a test" list
   here rather than reclassified, because unlike this document's 4 pattern
   claims their two sides really are independent implementations that
   happen not to be comparable on this particular axis — a future editor
   changing either function's behavior would not be caught by any test,
   which is the operational definition of a gap used throughout this
   document. Worth a second look before writing tests for these three
   specifically, since "not comparable, ever" may mean the RIGHT fix is a
   docstring correction rather than a test, same as this document's own
   4 pattern claims — flagged, not resolved, here.)
2. **4 pattern-claim docstrings need softening**
   (`_suggest_negative_evidence`, `customer_360`,
   `emit_semantic_model_from_store`, `_tool_score_strings`) — remove or
   soften the "mirrors"/"Mirrors" language since there is no independent
   implementation on the other side to test against.
3. **The cross-language blind spot fix** Stage 4 already recommended is now
   confirmed to matter twice, not once — worth prioritizing over the other
   two items if only one gets picked up next.

None of this is done in this document — it is triage, matching what was
asked for. Writing the 8 tests, the 4 docstring fixes, and the
`_resolve_target` cross-language fix are each their own scoped piece of work.

**Update, same day:** all of the above (the tests, the docstring
softenings — 11 in the end, not 4, once Stage 4c's own findings were
folded in — and the ambiguous-target resolver fix) is now done. See
`docs/superpowers/specs/2026-09-04-stage4d-test-writing-results.md` for
what writing the tests actually found, including a real production bug
the docstring-triage alone could never have surfaced.

## Being wrong about this document

All 25 claims were read individually this pass — there is no sampling
uncertainty left in the 14/7/4 split itself (corrected from an original
13/8/4 the same day — see the top of this document). The uncertainty that
remains is scoped correctly above: the 28 still-unenforced claims outside
the rescued pool were not touched by this pass (only 2 of them,
previously), and their true/false-negative composition is unknown, not
"probably similar to the rescued pool." Treat that 28 as a separate,
larger open question.
