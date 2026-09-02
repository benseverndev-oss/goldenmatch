# Codebase Audit Programme — Phase C: Unenforced Synchronisation Claims

**Status:** design approved, not yet planned
**Date:** 2026-09-02
**Phase:** C of a three-phase programme (A → B → C)
**Predecessors:**
`docs/superpowers/specs/2026-09-01-dead-code-audit-design.md` (A, complete)
`docs/superpowers/specs/2026-09-02-duplication-drift-audit-design.md` (B)
`docs/superpowers/specs/2026-09-02-shared-decision-triage.md` (B1 triage)

## Why

The codebase tells you where its traps are. 319 docstrings in `goldenmatch`
assert that a piece of code must stay in step with another piece of code —
*mirrors*, *byte-identical to*, *must match*, *keep in sync with*, *copy of*,
*counterpart*. Nothing checks any of them.

That is the shape of the incident this phase was originally scoped around.
`MatchEngine._run_pipeline`'s own docstring read:

> Core pipeline logic — mirrors run_dedupe but returns EngineResult.

It stopped mirroring `run_dedupe`. The shipped pipeline moved to an eager arrow
path while the copy kept driving the polars LazyFrame API, and `demo` and
`lineage` raised `ImportError` on a default install. Deleted in `6c89042c7`;
203 lines became 54.

Measured 2026-09-02 on `goldenmatch`, and these are the numbers the rest of
this document uses:

| | claims | resolvable target |
| --- | ---: | ---: |
| on a function or class | 270 | 216 |
| on a module | 49 | (not triaged, see below) |
| **total** | **319** | |

**172 of the 216 symbol-level resolvable claims (80%) are unenforced.** 50 of
the 172 say "byte-identical to" or "identical to".

### A correction, made while planning

An earlier draft of this spec reported 119 resolvable and 87 unenforced. Those
came from a target-extraction rule that only accepted a name in backticks
(`` `run_dedupe` ``) or with a call suffix (`run_dedupe()`).

**That rule could not extract this phase's own motivating example.**
`_run_pipeline`'s docstring says "mirrors run_dedupe but returns EngineResult"
-- a BARE identifier. The rule found nothing, the incident was not in the
resolvable set, and C0's exit criterion as originally written was
unsatisfiable.

The rule is now: any word in the 200-character window after the claim keyword
that names a symbol declared in the package, taking the first match.
**Resolution is the filter**, so no punctuation convention is required. That is
also why the population nearly doubled: most claims name their target in
prose, not in markup.

The cost is a first-match heuristic that can pick the wrong name when a claim
mentions several symbols. C1 triage classifies those as false positives, and
the report prints the matched window so a reader can see what it keyed on.

### A second correction, made while finishing Task 1

Every count in this document was re-measured against what the shipped
detector reports today, and it does not match what an earlier version of
this document said, either: 212 resolvable became **216**, 168 unenforced
became **172**, and the "48 byte-identical / identical claims" line became
**50**. The cause is a one-line fix to the same rule this correction already
covers. `_WORD`'s continuation class includes `.`, so a target word
immediately followed by sentence-ending punctuation (`"...mirrors helper."`)
matched WITH the trailing period, and `"helper."` was never equal to
`"helper"` in the known-symbols set — the resolver silently missed it.
`claims()` now strips that trailing period before comparing, and four more
targets resolve as a result.

**The "179 unresolvable claims" line under Out of scope, below, was wrong
under either measurement, not just the current one.** 270 symbol-level claims
minus 212 (the earlier resolvable count) is 58; minus 216 (the current one)
is 54. Neither arithmetic produces 179 — it is not a stale snapshot of a real
intermediate state, it is simply an error, and the fix is to read it off the
detector rather than to explain it. Every count in this document is now
generated from a live run of `python -m sync_claims.report`, not typed by
hand from an intermediate measurement.

## What this phase was going to be, and why it is not

Phase C was scoped as **module cohesion and splitting**, with B0b — structural
clone detection — folded in. Both halves were refuted by measurement before
design began. Recording that here, with the numbers, because a future phase
will otherwise propose them again.

### Module splitting has no mechanical basis

`goldenmatch` is 493 modules and 168,347 lines, median module 189 lines; 428 of
493 are under 500 lines. The distribution is healthy and the large modules do
not decompose. Partitioning each module's top-level definitions into connected
components by intra-module reference:

| module | top-level defs | components | largest |
| --- | ---: | ---: | ---: |
| `core/autoconfig.py` | 127 | 5 | 123 |
| `core/probabilistic.py` | 119 | 8 | 111 |
| `core/pipeline.py` | 69 | 3 | 66 |
| `core/scorer.py` | 80 | 7 | 73 |

Every large module is one connected component plus a handful of isolated
helpers. There are no natural split lines; splitting would cut through the call
graph rather than along it, which is a design project, not an audit finding.

*Bound on this measurement:* it counts top-level name references, so a module
can be one component and still lack cohesion. What it rules out is a cheap
mechanical split, not the existence of a cohesion problem.

### B0b's premise was wrong

The phase-B spec states that incident 1 is "detectable by structural clone
detection". It is not. That sentence was written from the commit message's
phrasing — "a ~200-line reimplementation whose own docstring said 'mirrors
run_dedupe'" — rather than from the diff.

Structural similarity, measured as `difflib` ratio over normalised AST
node-type sequences (identifiers and literals erased):

| pair | similarity |
| --- | ---: |
| a function vs itself, every identifier renamed | 1.000 |
| a function vs itself with 20% of statements deleted | 0.562 |
| **`_run_pipeline` vs `run_dedupe` (the incident)** | **0.033** |
| median of 400 random large-function pairs | 0.036 |

The incident scores **below** the median random pair, and 56% of random pairs
score at or above it. The two positive controls establish that the measure
detects both identifier-renamed clones and genuinely drifted copies, so the
negative is about the incident, not the instrument. `_run_pipeline` and
`run_dedupe` shared a purpose, not a structure.

A clone detector tuned to find the incident would flag more than half of all
large-function pairs. B0b is dropped.

### What survived

The incident had a different detectable property: **it said what it was doing.**
That generalises to a population of 319 claims -- 216 of them both
symbol-level and resolvable, which is what this phase triages -- and unlike
clone detection it finds the motivating incident by construction rather than
by threshold.

## Architecture

### Evidence model — invert, as A and B did

A **claim** is a docstring on a module, class or function asserting a
relationship to another symbol. A **resolvable claim** names a symbol that
exists in the package. A claim is **unenforced** when no single test file
references both the claimant and its target in *executable* code.

**The enforcement check needs a claimant SYMBOL, so it applies to the 216
symbol-level resolvable claims only.** A module-level claim has no single name
a test can reference — "this module MIRRORS the datafusion backend" is a claim
about a file, and the check would have to guess which of its symbols carries
it. The 49 module-level claims are extracted and reported in their own bucket,
and C1 does not triage them. That is a real gap, stated rather than hidden: it
is 15% of the claim population, and a later pass can address it with a
per-module rule once the symbol-level pass has shown what a good rule looks
like.

Two properties of that definition carry the phase:

**Executable references only.** At `6c89042c7^`, `tests/test_engine.py` names
both `_run_pipeline` and `run_dedupe` — inside a docstring. A text scan
classifies the incident as enforced and this phase misses the bug that
motivates it. Counting only `Name`, `Attribute` and `alias` nodes: 2 tests
reference `_run_pipeline`, 10 reference `run_dedupe`, **0 reference both**.

**Sound as a negative, suggestive as a positive.** No co-reference genuinely
proves that nothing compares the two. Co-reference proves only that one file
mentions both, never that it compares them. So the finding is the unenforced
set; the 44 possibly-enforced claims are reported as UNVERIFIED and never as
safe.

This is A and B's inversion. A resolved declared liveness (registries) and
flagged the unexplained. B resolved declared parity and flagged undeclared
shared decisions. C resolves declared synchronisation and flags the unenforced
— with the difference that the codebase has already written the declarations
down.

### Rejected alternatives

| option | why not |
| --- | --- |
| Coverage co-execution (per-test contexts; enforced if one test executes lines in both) | Measures execution rather than mention, and reuses A/B's coverage machinery — but co-execution still does not prove comparison. It buys precision in the POSITIVE direction, which carries no finding; the negative direction is already sound. Not worth the per-test-context cost. |
| Inventory only, no enforcement signal | Yields no automatic finding and nothing to ratchet. Would be the first phase here with no gate, and A's durable value came from the gate, not the report. |
| Structural clone detection | Refuted above. |

## Being wrong

**Phase C's dangerous failure is making a finding disappear without removing the
trap.** It is quieter than A's (deleting live code) or B's (a bad merge), and
more likely than either.

**1. Deleting the claim.** The cheapest way to clear a finding is to delete six
words from a docstring. That leaves the coupling as dangerous as it was and
destroys the only record that it exists — strictly worse than the status quo,
because the next person no longer gets the warning. **Deleting a claim is never
a remediation.** A claim goes only when the coupling itself is gone, which means
the code changed, not the prose.

**2. Writing a test that pins drift as correct.** `resolve_fs_block_source`
claims to be "byte-identical to `score_buckets`". If those have already drifted,
an enforcing test fails, and the tempting repairs are to weaken the assertion
until it passes or to align one side by picking whichever is convenient. That is
finding F10 again: when two copies disagree, deciding which is canonical is a
judgement, and getting it wrong ships a bug under a green test. **Verify the
claimed property holds today BEFORE writing anything to enforce it. If it does
not hold, that is a defect finding, not a test-writing task.**

**3. The ratchet teaching people not to write claims.** If unenforced claims are
gated, the way never to trip the gate is to stop writing "mirrors X" in
docstrings — suppressing the declarations this phase depends on while the
reported number improves. This is the failure most likely to happen by accident.

The defence against all three: **the ratchet floor is a set of
`(claimant, target)` pairs, not a count**, matching `KNOWN_DEAD`. Removing a
pair means deleting a named line from a checked-in file, with a reason, in a
reviewable diff. A count can be improved by deletion silently; a named set
cannot. The report also prints claim-count and finding-count separately, so a
drop in total claims cannot masquerade as progress.

Carried from B: **no automated remediation** — C proposes, a person disposes —
and **one remediation per PR**, so a wrong one reverts cleanly.

## Delivery stages

**C0 — detector, report-only.** Claim extraction, target resolution,
enforcement check, report. Exit criterion: it extracts
`_run_pipeline --mirrors--> run_dedupe` from a checked-in fixture and classifies
it unenforced.

**C1 — triage all 172 symbol-level findings.** Every finding classified as: enforceable and should be /
claim is stale because the coupling is gone / already drifted, therefore a
defect / false positive. Exit criterion: every finding carries a classification
and a reason. **Start with the 50 "byte-identical to" / "identical to" claims** — unlike
"mirrors", that phrasing states a property that can actually be checked, so
triaging one either produces an enforcing test or discovers a drifted pair.
Budget for the first-match heuristic's measured cost while triaging the rest:
13 of the 172 findings (7.6%) resolve to a generic word, picked up because it
happens to also be the first declared symbol name in the 200-character window,
rather than because the claim is about it. **The rule that produces 13**, so a
reader can reproduce the figure rather than take it on trust: a target of three
characters or fewer, OR one drawn from a fixed common-word set (`min`, `max`,
`row`, `key`, `run`, `sum`, `len`, `get`, `set`, `add`, `map`, `all`, `any`,
`col`, `val`, `obj`, `idx`). Both halves independently select the same 13, of
which `row` accounts for 6 and `key` for 3. Those are false positives by construction, not
edge cases; expect roughly one in thirteen findings to be one.

**C2 — remediation, one per PR.** Enforcing tests where the property holds;
defect fixes where it does not; claim removal only where the coupling is gone.

**C3 — ratchet on.** Gate at the triaged floor, matching `KNOWN_DEAD`,
`KNOWN_ACTIONABLE` and `KNOWN_JOB_FILTER_GAPS`.

## Testing

The detector is tested like a product component, because a detector that
silently measures nothing is the failure mode this repository keeps hitting.
Phase A caught five instances; phase B found its own accessor scan reading 23%
of the config surface, and found that only because a sabotage check was invalid.

**Fixtures split in two**, because the enforcement half cannot check in 972 test
files:

- *Claim extraction* validates against the real `tui/engine.py` at
  `6c89042c7^`, checked in as a prefix fixture the way
  `scripts/fixtures/incident_1c843c8a5/` already is. It must extract the claim
  and name `run_dedupe` as the target.
- *Enforcement* validates against a small synthetic tree: one test referencing
  both symbols in code (enforced), one referencing only the claimant
  (unenforced), and one whose ONLY co-mention is inside a docstring, which must
  come out unenforced.

Required tests, each verified by sabotage — breaking the detector and confirming
the test fails naming the right thing — not by observing a pass:

- the incident fixture is extracted and reported unenforced;
- a docstring-only co-mention does not count as enforcement;
- a genuinely co-referenced pair is not reported;
- an unresolvable claim lands in its own bucket, never as a finding;
- the report distinguishes "no findings" from "detector did not run";
- claim-count and finding-count are reported separately.

**A sabotage that does not apply is not a sabotage.** Phase B's ratchet check
planted a divergent fallback at a site the scan never attributed, and read the
resulting green as proof the gate worked. Every sabotage in this phase asserts
that the sabotage actually landed before reading the test result.

## Success criteria

- The detector extracts and reports the motivating incident from a fixture.
- Every one of the 172 findings carries a classification and a reason.
- Every claim surviving triage is either enforced or recorded with why it stands.
- No claim is deleted while its coupling remains.
- Every "byte-identical to" claim is either enforced by a test or recorded as a
  defect.
- C0's report names the matched claim window, so a wrong target resolution is
  visible to triage rather than silent.
- The ratchet gates at the triaged floor, as a named set of pairs.

## Out of scope

- Module splitting and cohesion, and structural clone detection — both refuted
  above.
- The 54 unresolvable claims (they name nothing that exists): reported in
  their own bucket, not triaged.
- The 49 module-level claims: extracted and reported, not triaged — the
  enforcement check needs a claimant symbol. See the evidence model.
- Packages other than `goldenmatch`. `goldenflow` carries 37 claims and gets its
  own pass if this one pays off.
- Cross-language claims (a Python docstring naming a TypeScript symbol).
- `_archive/`, retained pre-fold history.
