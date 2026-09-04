# Phase C, Stage 4d — Writing Tests for the 18 Confirmed Real Gaps

**Status:** complete — 17 new tests written and passing (1 Spark half unverifiable locally),
1 claim found already covered by a pre-existing test invisible to coverage, 2 real findings
surfaced by writing the tests rather than just reading the claim
**Date:** 2026-09-04
**Prior:** `docs/superpowers/specs/2026-09-04-stage4b-full-rescue-triage.md`,
`docs/superpowers/specs/2026-09-04-stage4c-remaining-population-triage.md` (the 18 confirmed
real gaps this document closes out)

## Why this document exists

Stage 4b and 4c triaged 45 claims by reading them and their existing tests. 18 came back
FALSE POSITIVE — genuine, checkable equivalence claims with no test verifying them. This
document is the next, larger step: actually writing a test for each. Writing a test is a
stronger check than reading one, and it found things reading did not: a claim already covered
by a test invisible to the coverage mechanism (correcting Stage 4b, see that document's own
correction note), a docstring whose claim is confirmed false as literally written, and a real
behavioral divergence between two production code paths that both claimed to agree.

## Result: 18 claims, 17 outcomes (one collapsed into a correction)

| claim | target | outcome |
| --- | --- | --- |
| `config/splink_upgrade.py:_measure_mean_token_set_size` | `_measure_mean_length` | **TEST WRITTEN, PASSES** |
| `core/autoconfig.py:_pass_row_keys` | `_project_pass_pairs` | **TEST WRITTEN, PASSES** |
| `db/sync.py:_full_scan_streaming` | `_full_scan_pipeline` | **TEST WRITTEN, PASSES** |
| `distributed/clustering.py:_rc_union_isolated` | `two_phase_wcc` | **ALREADY COVERED** — see Stage 4b correction |
| `core/embedding_ops.py:CanonicalizationEval` | `EvalResult` | **TEST WRITTEN, PASSES** |
| `core/vector_index.py:VectorIndex.query` | `retrieve_similar_records` | **TEST WRITTEN, PASSES** |
| `core/config_lint/registry.py:LintInput` | `ColumnProfile`/`data_profile_column_stats` | **TEST WRITTEN, PASSES** (docstring retargeted) |
| `core/golden_fused.py:run_golden_fused_arrow` | `build_golden_records_from_frames` | **TEST WRITTEN, CONFIRMS THE CLAIM IS FALSE** |
| `backends/score_duckdb.py:score_blocks_duckdb` | `score_blocks_parallel` | **TEST WRITTEN, PASSES** |
| `core/config_critique.py:_add_blocking_pass` | `apply_quality_aware_blocking` | **TEST WRITTEN, PASSES** |
| `core/perceptual.py:phash_image_batch` | `phash_image` | **TEST WRITTEN, PASSES** |
| `core/perceptual.py:audio_ber_aligned` | `audio_ber` | **TEST WRITTEN, PASSES** |
| `documents/classify.py:_strip_fence` | `parse_message_text` | **TEST WRITTEN, PASSES** |
| `spark/autoconfig.py:_boundary_columns` | `profile_columns` (retargeted) | **TEST WRITTEN, PASSES** (no Spark needed — pure Python both sides) |
| `spark/identity.py:build_identity_graph_incremental` | `resolve_clusters` | **TEST WRITTEN, CONFIRMS A REAL BUG** (see below) |
| `web/pair_prose.py:with_prose` | own `pair` parameter | **TEST WRITTEN, PASSES** |

16 rows above; `_rc_union_isolated` is listed once in Stage 4b's corrected table, not
duplicated here as an 18th test. 14 of 16 tests written this pass pass cleanly and confirm
their claim. Two surfaced something worth reading individually.

## Finding 1: `run_golden_fused_arrow`'s docstring claim was literally false

The claim: `provenance=True` returns a `(golden_df, records)` tuple "mirroring
`build_golden_records_from_frames`'s `(df, list[dict])` shape." The new test
(`tests/test_golden_fused.py::test_provenance_shape_vs_build_golden_records_from_frames`)
confirms this is false as written: `build_golden_records_from_frames` always populates
exactly one of its two return slots (its own docstring says so); `run_golden_fused_arrow`
always populates both. The test locks this shape divergence in explicitly, then checks what
remains genuinely checkable — field-for-field agreement between the two functions' POPULATED
records, which does hold. Docstring corrected (this same change) to state the real shape
relationship instead of the false one; the separate, true "byte-identical to
`build_golden_records_batch`" claim earlier confirmed by Stage 4b is untouched.

Side effect of that docstring fix, worth recording rather than chasing further: the
byte-identical-to-`build_golden_records_batch` clause was never independently detectable by
the scanner anyway (`CLAIM_PATTERN` requires the literal contiguous phrase "byte-identical
to," and the actual text is "byte-identical **at the FIELD level** to" — not contiguous). It
was only ever picked up as a side effect of the now-fixed "mirroring" match earlier in the
same docstring being the first `CLAIM_PATTERN` hit. A minor, pre-existing scanner precision
gap, not introduced by this change — noted for whoever next touches `claims.py`'s regex.

## Finding 2: a real behavioral divergence between Spark and one-box identity merge

**This is the significant result of this whole test-writing pass, and needs a decision from
whoever owns Layer 2 identity resolution — it is not fixed here.**

`build_identity_graph_incremental`'s docstring says its merge-winner selection "mirror[s]"
one-box `resolve_clusters` — and the *winner selection itself* does agree (verified: seeded
identical pre-existing entities into both a SQLite one-box store and Spark's
`existing_records`/`existing_nodes` tables so the tie-break branch actually fires; both pick
the entity holding most of the current cluster, not the one with the most records overall).

What does NOT agree: **what happens to a merge loser's records that are not part of the
current run's cluster.**

- **One-box** (`identity/resolve.py`'s reassignment loop): scoped to `existing`, built only
  from the current run's `record_ids`. A loser's OTHER records — ones the current run never
  touched — keep pointing at the retired entity.
- **Spark** (`spark/identity.py::build_incremental_records`): joins the FULL
  `existing_records` table on `loser_entity_id` and reassigns ALL of a loser's records to the
  winner, unconditionally, matching its own existing test
  (`test_merge_retires_losers_into_the_winner`).

Confirmed empirically, not just by reading: seeded `ent:BIG` with one record in the current
cluster and two records NOT in it; after a one-box merge, the two out-of-cluster records
stayed attributed to the retired `ent:BIG`. Spark's own code and its own existing test
confirm it would reassign all three.

This means, in a real dataset, a Spark-processed incremental merge and a one-box-processed
merge of the identical input can leave the identity store in different states for records
outside the specific run's cluster — a genuine cross-surface correctness question, not a
test-coverage gap. The new test
(`tests/test_sail_identity_incremental.py::test_incremental_winner_selection_matches_one_box_on_a_real_merge`)
is deliberately scoped to the part that DOES agree (winner selection), with the divergence
named explicitly in its own docstring/comments so it is not lost — it does not assert either
side's loser-reassignment behavior, because asserting either would either paper over the
real disagreement or bake in a guess about which side is "right."

**Three ways to close this, in decreasing order of invasiveness, left for the identity
owner to choose, not decided here:** (a) make one-box reassign store-wide like Spark does,
(b) make Spark scope reassignment to the current run's cluster like one-box does, or (c) if
the current difference is intentional (e.g. Spark's incremental-graph model assumes
store-wide reassignment is always correct, one-box's assumes conservative scoping), amend the
"mirror" documentation on both sides to name the carve-out explicitly, the way the design spec
already does for the entity-id tie-break.

## Confirming nothing else broke

All 15 touched test files run together: 352 passed, 1 skipped (the `pyspark`-gated half of
the identity-merge test, consistent with this repo's own "never install pyspark locally"
policy — the file compiles cleanly and collects/skips correctly, matching every other
Tier-2 Spark test in it). The `sync_claims` test suite (52 tests) and the full-package ruff
lint remain clean. Only test files were changed for 14 of the 16 items; three items
(`LintInput`, `run_golden_fused_arrow`, `_boundary_columns`) also needed a one-clause
docstring correction, each verified against the actual current source rather than assumed.

## Being wrong about this document

14 of 16 tests are straightforward passes confirming an already-suspected-true claim — solid.
The two exceptions are the valuable output of this pass and deserve the most scrutiny from a
second reader: Finding 1 (the shape-mismatch test) rests on reading both functions' current
source directly, not on the old docstring's claim. Finding 2 (the identity divergence) rests
on an empirical reproduction in both systems, not on code inspection alone — but "which
behavior is correct" is a product/design judgment this document deliberately does not make.
