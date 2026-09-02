# Phase C1 — Triage of the Unenforced Sync Claims

**Status:** first pass complete; bulk triage BLOCKED on one more detector fix
**Date:** 2026-09-02
**Spec:** `docs/superpowers/specs/2026-09-02-sync-claim-audit-design.md`
**Detector:** shipped in #2846, target rule corrected in #2847

## The headline

C1 set out to classify 167 unenforced sync claims. It found that **most of
them are not findings about the codebase — they are findings about the
detector.** Three distinct precision problems surfaced, in order of
discovery. One is fixed. Two are not.

The bulk triage should not proceed until the second is fixed. Triaging
against a detector whose targets are wrong spends the effort classifying
false positives, and stage C3 seeds its ratchet floor from
`(claimant, target)` pairs — a floor built now would bake those pairs in
permanently.

## Where the 167 stand

| class | n | meaning |
| --- | ---: | --- |
| R needs reading | 95 | machine-classified only; a human has not read the claim |
| D drift candidate | 27 | marked-up target, code differs 0.30-0.90 |
| F false positive | 28 | wrong target (bare word, structurally unrelated) |
| E target is not a function | 14 | claimant or target is a class/attribute |
| H claim holds | 2 | structurally near-identical; write the test |
| P pattern claim | 1 | claim is about arrangement, not behaviour |

**This is not a completed triage and should not be read as one.** 95 of 167
carry a machine label of "needs reading" and no human judgement. The spec's
C1 exit criterion — every finding carries a classification and a reason — is
not met.

## Precision problem 1: bare-word targets (FIXED, #2847)

Triaging the 50 strongest claims produced a suspicious result: **not one of
the 49 comparable pairs was structurally similar to its resolved target.**
That looked like mass drift. Reading the claim text showed the cause was
wrong targets:

| resolved to | what the window says |
| --- | --- |
| `slice` | "slice one bucket off the keyed frame" (a verb) |
| `min` | "Default ``min(cpu, 8)``" (the builtin) |
| `edge` | "the shared edge set" (a noun) |
| `native` | "the per-block native path" |

Seven of eight sampled targets were wrong. `goldenmatch` declares thousands
of symbols and many are ordinary English words, so nearly any prose sentence
contains one and a first-match rule finds it.

Fixed by preferring a marked-up target and falling back to a bare word only
when the window has no markup. 26 targets corrected; findings 172 -> 167.

## Precision problem 2: the target is not the claim's OBJECT (NOT FIXED)

This is the one blocking the bulk triage.

After the markup fix the targets are real symbols, but they are frequently
not what the claim equates. The claim keyword and the symbol both appear in
the window; the symbol is simply somewhere else in the sentence.

```
_fs_bucket_native_enabled --identical to--> _fs_native_eligible
   "Only gates the BATCHED bucket call; whether the per-block loop itself
    is native still follows ``_fs_native_eligible``"
```

`_fs_native_eligible` is a real symbol, correctly spelled, and the claim is
NOT that the two are identical — it is that one gates a call the other
governs. Same shape in `_ensure_name_tables_installed --> find_fuzzy_matches`
("the vectorized ``find_fuzzy_matches`` scores the block") and
`resolve_fs_block_source --> score_buckets`.

**The measured lever is proximity.** How often the resolved target sits
within N characters of the claim keyword:

| window | findings with a target inside it |
| ---: | --- |
| 20 chars | 54 of 167 (32%) |
| 40 chars | 111 of 167 (66%) |
| 60 chars | 133 of 167 (80%) |
| 200 chars (today) | 167 of 167 (100%) |

The motivating incident survives even the tightest setting — "mirrors
run_dedupe but returns EngineResult" puts the target one character after the
keyword.

**The tradeoff is real and is why this is not being changed unilaterally.**
A tighter window drops findings, and some of those drops would be correct
claims whose target legitimately appears later in the sentence. Choosing the
number is a judgement about what C1 wants to triage, not a bug fix. It should
be decided, measured against the incident fixture, and shipped as its own
change.

## Precision problem 3: pattern claims (NOT FIXED, low priority)

Some claims are about how code is ARRANGED, not how it behaves:

```
_do_transform_columnar: "Call goldenflow.transform -- the POLARS-FREE
   columnar engine. ... Separated for testability, mirroring _do_transform."
```

The two are deliberately different engines (polars vs polars-free). "Mirroring"
here means "organised the same way", and there is no behaviour for a test to
enforce. Same shape in the two `_migrate_*_columns` functions, which follow a
common migration pattern rather than sharing behaviour.

The spec anticipated false positives from target resolution. It did not
anticipate a claim whose VERB is real and whose object is real but whose
subject matter is structural. Currently one is auto-detected; the true count
is unknown because it needs reading.

## What C1 did confirm

**One claim holds and should be enforced.**
`identity/snowflake_backend.py:_rel_expr` says "Snowflake port of
``store._rel_value_expr``. Same FIXED transform vocabulary, same
NULL-on-no-match semantics". Checked: both sides support exactly
`email_domain`, `lower_trim`, `normalize_company`, `raw`, `zip3`. The claim
is true today and nothing enforces it, so the two backends can drift into
resolving identities differently. A test comparing the two vocabularies is
cheap and is the right remediation.

**The detector observed its own remediation.** Merging main into the B0a
branch turned three of its tests red, because #2845 moved the keys-vs-passes
decision onto `BlockingConfig.resolved_keys()` and four modules
(`score_buckets.py`, `fs_out_of_core.py`, `distributed/scoring.py`,
`identity/block_index.py`) consequently stopped reading `.passes`/`.keys`
directly. The shared-decision inventory shrank because the duplication it
surfaced was removed. Those tests were updated, not widened — one of them
carries an explicit warning against widening its window to silence it.

## Recommended order

1. **Decide the proximity window**, measure it against the incident fixture
   and the 167, ship it as its own change. This is the gate on everything
   below.
2. **Re-run the triage** against the corrected detector; expect the "needs
   reading" population to fall substantially.
3. **Read what remains** and classify it, meeting the spec's exit criterion.
4. **Write the `_rel_expr` vocabulary test** — the one confirmed, actionable
   finding so far, and independent of the above.
5. **C3's ratchet last**, once the floor is worth freezing.

## Being wrong about this document

The honest risk is that three rounds of "the detector needs another fix"
becomes a way of never triaging anything. Two guards against that:

- Each fix so far was found by ATTEMPTING the triage, not by inspecting the
  detector — problem 1 came from comparing 49 claimed-identical pairs,
  problem 2 from reading the claims that survived the fix.
- The proximity number is measured, not estimated, and its cost is stated in
  findings dropped. If the next pass produces a fourth precision problem of
  the same kind, that is evidence the docstring-claim signal is weaker than
  the spec assumed, and the phase should be re-scoped rather than refined
  again.
