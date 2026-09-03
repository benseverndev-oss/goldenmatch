# Phase C1 — Triage of the Unenforced Sync Claims

**Status:** re-scoped. Option 2 chosen and implemented -- findings are now
the high-confidence subset, and the rest are reported without being triaged.
**Superseded in part by:** `docs/superpowers/specs/2026-09-03-stage4-coverage-
retriage.md` -- the false-negative rate this document extrapolates from 8
checked items is corrected there with a population-scale, coverage-based
measurement (lower than the extrapolation implied, for a reason -- a
material false-positive class -- this document had no evidence for yet).
Read both.
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

### The proximity lever was measured, and it does not work

The paragraph above proposed picking a window and shipping it. That was
measured against seven findings hand-identified as wrong targets and three
hand-identified as right, plus a "same sentence as the keyword" rule that
seemed more principled than a character count:

| rule | findings kept | known-BAD kept | known-GOOD kept |
| --- | ---: | --- | --- |
| 20 chars | 54 | 2/7 | **0/3** |
| 40 chars | 111 | 3/7 | 2/3 |
| 60 chars | 133 | 4/7 | 3/3 |
| same sentence | 144 | **6/7** | 3/3 |
| 200 chars (today) | 167 | 7/7 | 3/3 |

**The sentence rule barely helps: 6 of 7 wrong targets sit in the same
sentence as their keyword.** The grammatical intuition behind it -- that a
claim's object is in its own sentence -- is simply false for this corpus.
20 characters drops every known-good finding. The best available setting,
60 characters, still keeps 4 of the 7 known-bad.

So proximity is a marginal precision gain, not the gate this document
claimed it was. **No proximity change is being shipped**, because shipping
one would trade real findings for a minority of the false ones and leave
the triage no more tractable.

## What this means for the phase

This document's own guard, written before the measurement:

> If the next pass produces a fourth precision problem of the same kind,
> that is evidence the docstring-claim signal is weaker than the spec
> assumed, and the phase should be re-scoped rather than refined again.

The measurement is that evidence, arriving a different way: not a fourth
problem, but proof that the proposed fix for the second one does not work.
Three attempts to make the target rule precise have produced one real
improvement (markup preference, #2847) and one dead end.

**The weak link is the concept of "the target".** The detector tries to
resolve each claim to exactly one symbol, and a docstring sentence does not
reliably contain exactly one resolvable referent in a position a regex can
find. Grammatical resolution needs parsing, which is out of proportion to
the phase.

Two honest ways forward, neither of which is "refine the regex again":

1. **Drop the auto-resolved target and report the CLAIM.** The detector
   keeps what it does well -- finding the 319 docstrings that assert a
   synchronisation and noting which are unenforced -- and stops asserting
   which symbol each one means. The output becomes "these N claims exist and
   nothing tests the thing they name", triaged by reading. That loses the
   automatic enforcement check, which depends on knowing the target, so the
   unenforced/unverified split would go with it.

2. **Narrow the phase to the claims that ARE machine-resolvable.** Keep only
   claims whose target is marked up AND within a tight window -- the
   high-confidence subset. That is roughly 54 findings at 20 characters, of
   which the hand-checked precision was best. Small, trustworthy, ratchetable
   at C3; explicitly does not attempt the other two thirds.

Option 2 preserves a working gate and is the smaller change. Option 1 keeps
coverage and abandons automation. This is a scoping decision, not an
engineering one, and it belongs to whoever owns the programme's budget.

## Option 2, as implemented -- and the trap in its first formulation

Chosen. Findings are now the HIGH-CONFIDENCE subset; everything else is
reported in its own bucket and excluded from triage.

**The obvious formulation of option 2 was unusable, and the measurement
caught it.** "Keep only claims whose target is marked up and near the
keyword" excludes this phase's own motivating incident: `_run_pipeline`'s
docstring reads "mirrors run_dedupe but returns EngineResult", and
`run_dedupe` carries no markup at all. A gate that cannot see the bug the
detector exists to catch is decoration -- the exact failure this programme
was built to remove, proposed by the programme.

The rule that ships accounts for it: **marked up within 40 characters, OR
bare within 12.** A bare word immediately after the claim keyword IS the
object by position; one 100 characters later is not. Measured on the real
package:

| rule | findings | known-bad kept | known-good kept | incident |
| --- | ---: | --- | --- | --- |
| markup only, 40 chars | 47 | 2/7 | 4/6 | **EXCLUDED** |
| markup 40 **or** bare 12 | 59 | 2/7 | 4/6 | survives |
| markup 40 or bare 20 | 82 | 3/7 | 4/6 | survives |
| markup 60 or bare 30 | 109 | 4/7 | 5/6 | survives |

The shipped setting rejects five of seven hand-identified wrong targets
while keeping four of six hand-identified right ones, and keeps the
incident.

**Nothing is discarded.** The low-confidence findings go to
`unenforced_low_confidence`, printed with a line saying they are not
triaged. A bucket split that quietly dropped them would shrink the reported
number while the claims stayed exactly as unchecked, which is the shape of
defect this document exists to record. A test asserts the buckets sum to
the symbol-level claim count.

Current split: **55 high-confidence findings**, 112 low-confidence, 49
unverified, 54 unresolvable, 49 module-level.

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

1. **Decide between re-scoping options 1 and 2 above.** Proximity was
   measured and rejected; there is nothing to ship until this is settled.
2. **Re-run the triage** under whichever scope is chosen.
3. **Read what remains** and classify it, meeting the spec's exit criterion.
4. ~~Write the `_rel_expr` vocabulary test~~ — DONE, PR #2849. The one
   confirmed finding, and it never depended on any of the above.
5. **C3's ratchet last**, and only if a scope survives that is worth
   freezing.

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
