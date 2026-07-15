# Bucket Fast Path Threads tf_freqs (#1781)

**Date:** 2026-07-15
**Status:** Approved (design)
**Issue:** #1781 (found by #1319's measurement pass). Blocks the redesigned PR2b (#1319).

## Problem

`GOLDENMATCH_TF_NAME_WEIGHTING` (#1318, default-on) attaches a per-dataset value-frequency table
as `MatchkeyField.tf_freqs`; `NameFreqWeightedJW.score_pair` accepts and uses it as a keyword
(`refdata/scorer.py:86-107` -- the data-driven whole-range downweight). But the DEFAULT scoring
path since #1761 -- the bucket backend's fast path -- resolves scorers via
`_resolve_score_pair_callable(scorer_name)` (`backends/score_buckets.py:204`) and, on the plugin
branch, grabs `plugin.score_pair` BARE. Called as `(a, b)`, `tf_freqs` defaults to None and the
scorer silently falls back to the static census path. #1318 is therefore a no-op on the default
path: the #1319 measurement proved fixture dedupe output ON vs OFF is byte-identical, while the
controller's SAMPLE telemetry shows the downweight biting (samples run the legacy path via the
active profile emitter) -- the telemetry-yes/output-no skew class.

The legacy path threads it correctly: `core/scorer.py:1236` passes
`tf_freqs=getattr(f, "tf_freqs", None)` into `_fuzzy_score_matrix`, which forwards it to plugin
scorers with try/except-TypeError back-compat.

## Decision (Approach A)

Extend the resolver: `_resolve_score_pair_callable(scorer_name, tf_freqs=None)`.

- Built-in branches (jaro_winkler/levenshtein/token_sort/exact/soundex_match/dice/jaccard) are
  UNTOUCHED -- `tf_freqs` only ever flows to plugin scorers, mirroring the legacy path where it
  travels only through the plugin protocol (`plugins/base.py:33`: every conforming plugin
  accepts the keyword and ignores it if unused).
- Plugin branch: when `tf_freqs` is provided (non-None, non-empty), return a wrapper that calls
  `fn(a, b, tf_freqs=tf_freqs)` and, on `TypeError` (a non-conforming legacy plugin without the
  keyword), permanently degrades to bare `fn(a, b)`. Precision note: this MIRRORS the
  try/except-TypeError posture `_fuzzy_score_matrix` applies to `score_matrix` forwarding
  (core/scorer.py:594-597); the legacy per-pair `score_pair` fallback (scorer.py:611) never
  forwards `tf_freqs` at all -- but it also never fires for `name_freq_weighted_jw`, which
  exposes `score_matrix`. This wrapper is therefore the score_pair-side twin of the established
  posture, not a byte-copy of an existing one. When `tf_freqs` is None, return `fn` bare (zero
  change for the overwhelmingly common case).
- Call sites: the weighted-field site (~427) passes `getattr(f, "tf_freqs", None)`. The NE-spec
  site (~342) is untouched -- `NegativeEvidenceField` has no `tf_freqs` attribute.
- Docstring on the resolver notes #1781 and the sample-telemetry-vs-final-dedupe skew this
  closes.

Rejected: binding at the call sites via `functools.partial` (spreads binding + back-compat
across two sites); passing the whole field object into the resolver (wider contract than one
attribute needs).

## Testing / success bar

1. **Parity-matrix case (the gap that should have caught this):**
   `tests/test_bucket_legacy_parity_matrix.py` gains a `name_freq_weighted_jw` field WITH a
   populated `tf_freqs` table -- same data+config through the bucket and legacy paths on the
   polars lane, byte-identical multi-member clustering. FIXTURE CAVEAT: legacy scores this
   plugin via `score_matrix` (float32) while the bucket per-pair loop accumulates float64
   (score_buckets.py:92-97 documents the borderline-flip risk) -- keep the fixture's pair scores
   away from the threshold boundary.
2. **Applied-table regression test:** a miniaturized #1319 common-name fixture where the bucket
   path's dedupe output WITH the table present differs from the same config with the table
   stripped (pins that the table is actually applied; the ON==OFF byte-identical state was the
   bug's signature).
3. **Back-compat unit test:** a fake plugin whose `score_pair` lacks the `tf_freqs` keyword
   still scores through the wrapper (TypeError fallback), matching the legacy posture.
4. **Success bar:** re-run the #1319 measurement's Leg A (crafted 2600-row fixture) on the fix
   branch -- bucket ON now diverges from bucket OFF and matches legacy ON (P 0.031 at the
   committed 0.8 threshold; the FULL precision recovery to ~0.99 lands with the redesigned PR2b,
   not this fix).

## Out of scope

- The redesigned PR2b controller rule (#1319, next feature).
- NE-field tf_freqs (the schema has no such knob).
- The native kernel: the bucket weighted path's kernel gate is `_NATIVE_SCORER_IDS`
  (score_buckets.py:188, four built-in scorers only), which excludes every plugin scorer --
  `name_freq_weighted_jw` never reaches the kernel; unchanged.
- Any scoring-semantics change beyond delivering the already-shipped table to the
  already-shipped scorer.
