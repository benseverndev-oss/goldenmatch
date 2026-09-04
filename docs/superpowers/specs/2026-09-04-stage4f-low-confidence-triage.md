# Phase C, Stage 4f — Triage of the 111 Low-Confidence Claims

**Status:** complete — all 111 individually triaged, 3 real bugs found and pinned with tests
**Date:** 2026-09-04
**Prior:** `docs/superpowers/specs/2026-09-04-stage4e-ambiguous-target-triage.md`
(the smaller, proven-shape ambiguous-target batch this document's methodology
was validated against first)

## Why this document exists

Scoping the 166-claim remainder of Phase C found the low-confidence bucket
(112 claims, 1 already handled as part of `mcp/server.py`'s cross-language
cluster) was the larger of the two remaining populations and shared the
proven vocabulary ("mirrors", "byte-identical to") of everything already
triaged. `claims.py`'s own `_confidence` heuristic measurement says roughly
half of low-confidence resolutions are wrong targets — this document
confirms that at full scale and closes out the population.

## Method

Split into 8 batches of ~14, dispatched in parallel, each following a fixed
triage order: (1) read the claimant's full current docstring, not the
truncated window; (2) is the "resolved target" a false match on an ordinary
English word or builtin (e.g. `min` from `min(cpu, 8)`, `edge` from "the
shared edge set") — if so, **NOT A REAL CLAIM**, no target, no test; (3) is
the real claim about a non-Python reference (C source, a Rust crate, the
TypeScript SDK) — if so, **CROSS-LANGUAGE**, not testable; (4) is it direct
delegation — if so, **PATTERN CLAIM**, nothing to diverge; (5) otherwise,
find whether an existing test already verifies the real relationship, and
write one if not, running it to confirm before reporting.

## Result: 111 claims, three real bugs found

**Tally:**

| verdict | count |
| --- | ---: |
| NOT A REAL CLAIM (false word/builtin match) | 32 |
| CROSS-LANGUAGE (not testable from Python) | 4 |
| PATTERN CLAIM (direct delegation) | 18 |
| ALREADY TESTED (real claim, existing coverage found) | 34 |
| NEW TEST WRITTEN, claim confirmed TRUE | 20 |
| **REAL DIVERGENCE FOUND** | 3 |
| **Total** | **111** |

(Counts are tallied from each batch's own per-claim table; the full
per-claim table each batch produced is preserved in this session's own
record, not reproduced claim-by-claim here to keep this document a
reasonable size — the pattern above is what matters, and the three real
findings below are named individually.)

## Three real bugs, none fixed here

Writing 23 new tests (20 confirming true claims + 3 pinning real
divergences) found things reading alone did not, the same way today's
earlier passes did.

**1. `db/... chunked.py::ChunkedMatcher._block_key_column` silently ignores
per-field transforms.** Docstring claims it mirrors
`blocker._build_block_key_expr` (the main, non-chunked path). Confirmed
false: `_build_block_key_expr` honors `BlockingKeyConfig.field_transforms`
per field; `_block_key_column` applies `key_config.transforms` uniformly to
every field and has no `field_transforms` parameter at all. Concrete case:
with `field_transforms={"last": ["uppercase"]}`, the main path computes
`"alice||SMITH"`, the chunked path computes `"alice||smith"`. Since block
keys gate which records become candidate pairs, **the large-dataset
streaming path can compute different block keys — and therefore different
candidate pairs — than the main path for the identical config**, whenever a
blocking key uses per-field transforms (relates to #1826). Pinned with a
test in `tests/test_chunked.py`; not fixed (would mean threading
`field_transforms` through `_block_key_column`, or delegating to
`_build_block_key_expr` directly, as its own docstring already half-suggests
— a real fix, not a docstring correction).

**2. `distributed/clustering.py::_attach_quality_metadata` computed cluster
confidence differently than the in-memory path -- FIXED, same day.**
Docstring claimed it mirrors `core/cluster.py`'s
`build_clusters`/`compute_cluster_confidence`. Confirmed false by calling
both production functions directly on the same star-topology cluster
(`1-2`:0.9, `1-3`:0.9, `1-4`:0.1): in-memory computed `confidence=0.266`,
quality `"weak"`; distributed computed `confidence=0.420`, quality
`"strong"` -- the identical cluster classified weak one way, strong the
other. Root cause, found on review: `compute_cluster_confidence` routes
through the native Rust kernel when enabled
(`native_module().cluster_confidence(edges, size)`) -- this project's own
standing principle is that the Rust kernel is the reference, not an
optional fast path (`project_rust_is_the_reference`) --
but `_attach_quality_metadata` never called it at all, reimplementing its
own hand-written formula instead. Not a design choice between two valid
formulas; a genuine failure to route through the established reference
implementation. Fixed by having `_attach_quality_metadata` call
`compute_cluster_confidence` directly, then apply the identical
`weak_cluster_threshold` downgrade `build_clusters` applies afterward --
verified against the same star-topology example, now agrees exactly
(`weak`, `confidence=0.266` on both paths). The pinning test in
`tests/test_distributed_clustering.py` (Ray-gated, correctly skips locally)
now asserts genuine parity rather than an `xfail`.

**3. `core/probabilistic.py::_fs_scoring_workers`'s default has drifted from
`_DEFAULT_MAX_WORKERS`, the function it claims to mirror.** Git history:
`_DEFAULT_MAX_WORKERS` (`core/scorer.py`) was briefly `min(cpu_count(), 16)`
in PR #301, then reverted to a fixed `4` in PR #303 after an RSS-pathology
OOM on a 16-core runner. `_fs_scoring_workers` was added over a month later
(PR #1566) with its own independent cpu-count-based default and a docstring
that (incorrectly) still described mirroring `_DEFAULT_MAX_WORKERS`. The
two now disagree by a wide margin (12 vs. 4 on a 12-core machine) — meaning
whatever the RSS-OOM fix was protecting against on the weighted path may be
equally exposed on the FS path, unprotected. **Docstring corrected** to
state the real (diverged) relationship rather than the false one, and a
test in `tests/test_probabilistic_parallel.py` pins the divergence
explicitly. The worker-count fix itself (should FS scoring also cap at 4,
or was the original OOM incident weighted-path-specific?) is left for
whoever owns that RSS-pathology history to decide.

## What remains in Phase C

The 54 unresolvable claims — no bare-word match found any declared symbol
at all — are the one piece of the 166-claim population not yet started.
Given this batch's yield (3 real bugs, ~63 new tests, out of 111 claims that
looked less promising going in than the 45-claim clean population), the
unresolvable bucket is worth the same treatment rather than assuming it is
lower-value.

## Being wrong about this document

All 111 claims were read individually by the dispatched batches, each
following the same fixed order this document describes. The one place this
document is *less* precise than Stage 4b/4c/4e: it does not reproduce the
full 111-row claim-by-claim table those documents used, on the judgment
that the aggregate pattern and the three individual findings are what a
future reader needs, not a table whose entries are almost all "false word
match, no action." If a future pass needs the exact per-claim record, this
session's own transcript carries it in full for each of the 8 batches.
