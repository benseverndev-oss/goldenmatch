# Phase C, Stage 4g — Triage of the 54 Unresolvable Claims

**Status:** complete — all 54 individually triaged. This closes the entire
166-claim population Phase C's scoping pass identified, and with it, Phase C.
**Date:** 2026-09-04
**Prior:** `docs/superpowers/specs/2026-09-04-stage4f-low-confidence-triage.md`
(the low-confidence bucket, triaged first, larger yield of real bugs)

## Why this document exists

The last untouched slice of Phase C: 54 claims where the bare-word scanner
found no declared symbol matching the target at all (`target=None`) --
harder in principle than the low-confidence bucket, since there is no
even-possibly-wrong candidate to start from. 7 were already classified
during scoping as part of `mcp/server.py`'s cross-language MCP-tool cluster.
The remaining 47 were split into 4 batches and triaged the same way as
Stage 4f, adapted for the "nothing resolved at all" starting point: read
the full docstring, then ask whether the real target is a non-Python
reference, genuinely vague prose, a renamed/moved symbol, direct
delegation, or a real testable claim.

## Result: 54 claims, one real-bug re-confirmation, no new bugs

| verdict | count |
| --- | ---: |
| CROSS-LANGUAGE (not testable from Python) | 20 |
| NOT A REAL CLAIM (vague prose, no symbol named) | 12 |
| PATTERN CLAIM (delegation / documented known divergence) | 6 |
| ALREADY TESTED (real claim, existing coverage found) | 7 |
| NEW TEST WRITTEN, claim confirmed TRUE | 9 |
| **Total** | **54** |

Unlike Stage 4f, this batch did not surface any *new* bugs. It did
re-confirm one already known: claim 45 (`spark/identity.py::build_identity_events`)
is a **documented, existing divergence** -- `build_identity_events`/
`build_incremental_events` emit `"CREATED"` (uppercase) while the one-box
`EventKind.CREATED.value` is `"created"` (lowercase). That function's own
docstring already names this as accepted, frozen-contract drift; nothing
new here, just confirmed still true and still documented, not silently
missed.

**A structural pattern worth naming for future audit passes:** a
noticeably higher fraction of this bucket (20 of 54, 37%) is cross-language
than the low-confidence bucket (4 of 111, 4%). This makes sense --
low-confidence claims found *some* Python symbol (however wrong); a claim
whose real target is Rust, C, or TypeScript often has no matching Python
symbol to find at all, so it is more likely to land here, unresolved,
rather than in low-confidence with a wrong guess. `mcp/server.py`'s entire
9-claim burden across every bucket was this shape.

**Renamed/moved targets (a shape not seen in Stage 4f):** 3 claims named a
function that has since been renamed --
`_run_fused_fs_short_circuit`→`_run_fused_fs_match_short_circuit`,
`_diversify`→`_diversify_probabilistic_blocking`, and `shatter_cluster`'s
target resolved to a different file (`mcp/server.py`) than the scanner's
same-file assumption expected. Two of these needed a docstring correction
alongside the new test (the third's target was already correctly named,
just in a different file the scanner didn't check). This is a distinct
failure shape from "false word match" or "genuinely vague" -- the claim was
entirely correct when written, and code churn (a rename) is what made it
unresolvable, not an error in the claim itself.

## What this completes

Phase C's 166-claim scoped population (everything outside the 45-claim
clean high-confidence set already closed by Stage 4b/4c) is now fully
triaged:

| population | claims | status |
| --- | ---: | --- |
| Clean high-confidence (Stage 4b/4c) | 45 | closed |
| Ambiguous-target (Stage 4e) | 8 | closed |
| Low-confidence (Stage 4f) | 111 | closed |
| Unresolvable (this document) | 54 | closed |
| **Total individually triaged today** | **218** | |

Combined with the 8 ambiguous-target claims and the earlier 45, every claim
the sync-claims scanner currently flags across the entire `unenforced` +
`unenforced_low_confidence` + `unenforced_ambiguous_target` +
`unresolvable` populations has now been read by hand at least once. Five
real production bugs were found across the whole day's effort (two before
this document's population, three within Stage 4f) -- see each stage's own
document for the individual writeups. C3 (arming sync-claims as an
enforcement gate rather than a report) remains the one deliberately
deferred piece of Phase C, per Stage 4b's own standing position.

## Being wrong about this document

All 47 dispatched claims were read individually; the 7 `mcp/server.py`
claims were classified during scoping (same cross-language pattern
independently confirmed 9 times across today's whole population) rather
than re-dispatched. As with Stage 4f, this document reports the aggregate
pattern and the one re-confirmed finding rather than reproducing all 47
individual rows -- the per-claim detail lives in this session's own
record for each of the 4 batches.
