# Codebase Audit Programme — Phase C, Coverage-Based Enforcement

**Status:** design approved, not yet planned
**Date:** 2026-09-03
**Phase:** extends C0/C1 (sync-claim audit) within phase C of the programme (A -> B -> C)
**Predecessors:**
`docs/superpowers/specs/2026-09-02-sync-claim-audit-design.md` (C0, the detector)
`docs/superpowers/specs/2026-09-02-c1-triage-findings.md` (C1 first pass)
`docs/superpowers/specs/2026-09-02-c1-read-of-55.md` (the soundness refutation)

## Why

C0's enforcement rule asks one question: does any single test FILE's source
text name both a claim's claimant and its target? C1 found that rule is not
sound as a negative. Checking eight findings the phase itself flagged as
highest-confidence -- not a random sample -- found **five confirmed false
negatives**: claims the rule reports UNENFORCED that a real test already
verifies, every time by the same mechanism -- the test calls a public
wrapper that internally calls the claimant, and separately reaches the
target, without ever naming either in the test's own source.

`core/scorer.py:_alias_score_matrix` is reported unenforced; a test calling
`_fuzzy_score_matrix`, which calls `_alias_score_matrix` at `scorer.py:1358`,
already parity-checks it. Three claims in `core/survivorship/native.py` are
reported unenforced; a 1,477-line, 77-test parity harness already verifies
them through `build_survivorship_native`'s public entry point.
`utils/transforms.py:canonical_soundex` is reported unenforced; it is reached
through `core/scorer.py:_soundex_score_single` and battered against the
native kernel across an adversarial vocabulary.

The common shape: **a test can compare two functions without naming
either**, by reaching them through code that calls them. A text-reference
rule cannot see that. This spec builds the fix C1 recommended and the
original spec rejected for the wrong reason -- coverage-based enforcement,
asking what a test actually EXECUTES rather than what its source text
mentions.

## Architecture

### What "coverage-enforced" means

A claim (claimant, target) is coverage-enforced when some single test
function, during a real CI run of the goldenmatch suite, executed at least
one line inside the claimant's own definition AND at least one line inside
the target's own definition.

Function-level granularity, not file-level. File-level co-execution would
just move the "co-occurrence is not comparison" problem from text to
runtime rather than narrowing it -- two unrelated tests in one file
satisfying the rule by accident is exactly the class of false enforcement
this fix must not introduce.

### Components

**CI (`.github/workflows/ci.yml`).** The `python_goldenmatch_coverage`
matrix already runs the full suite under coverage on every relevant PR,
sharded into `coverage_shard{1,2,3}.dat` and `coverage_heavy_{1,2,3}.dat`,
merged by a `combine` step (`coverage combine` -> `coverage xml` ->
`packages/python/goldenmatch/coverage.xml`, consumed today by phase A's
dead-code detector). This is extended, not duplicated: the matrix jobs gain
`--cov-context=test` (per-test dynamic contexts), and the combine step
additionally uploads the raw combined `.coverage` SQLite as a new artifact
-- `coverage xml` drops context data entirely, so the existing artifact
cannot serve this. The artifact's exact name is a plan-level detail with no
design consequence (any name works identically), so it is chosen during
implementation rather than fixed here; `gm-coverage-contexts` is the
working example used elsewhere in this document.

**`scripts/sync_claims/coverage_enforcement.py`** (new). A small,
purpose-built AST scan collecting `(module, function_name, lineno,
end_lineno)` for every top-level and nested function in the goldenmatch
package -- the same technique Companion A already validated in
`parity_coverage.py:_py_function_spans`, but general rather than scoped to
`_py`-suffixed names, so it is new code, not an import: that function is
module-private and answers a narrower question (only functions whose name
ends `_py`, only the packages Companion A cares about). Reads the
`.coverage` SQLite via `coverage.CoverageData`; for each function's line
range, unions the dynamic contexts (test qualified names) covering any line
in it. A claim is coverage-enforced when the claimant's and target's
context sets intersect.

**`scripts/sync_claims/report.py`.** `enforced = text_enforced OR
coverage_enforced` -- a strict widening; nothing text currently reports as
enforced can become unenforced. Each finding gains
`enforcement_source: "text" | "coverage" | "none"`, and the report prints a
count of findings coverage additionally resolved, so a reader sees the
check did something rather than trusting it silently.

**CI wiring.** `sync_claims` gains a dependency on the coverage-combine
job's artifact. `dead_code` already establishes a precedent worth following
here: its own comment notes it consumes the required gate's coverage
artifact directly rather than via a job-level `needs:` dependency, so the
sweep artifact "does not arrive through python_goldenmatch_coverage" as a
blocking dependency. Whether `sync_claims` follows that same lighter
coupling or a direct `needs:` is an implementation choice for the plan, not
fixed here -- but either way, if the coverage job's own filter did not fire
on a given PR (e.g. a PR touching only `scripts/sync_claims/`),
`sync_claims` MUST degrade cleanly to text-only and say so in the report. It
must never hang waiting on a job that was skipped, and never treat missing
data as "nothing coverage-enforced" silently -- the report states which mode
ran.

### Data flow

PR touches goldenmatch code -> coverage matrix runs pytest with
`--cov-context=test` -> combine merges shards with contexts intact ->
uploads both `coverage.xml` (existing) and the new raw `.coverage` artifact
-> `sync_claims` downloads the new artifact if present, builds the
function-to-tests map, folds `coverage_enforced` into `inventory()`, prints
per-finding source and a coverage-consulted/not-consulted line.

### Rejected alternatives

| option | why not |
| --- | --- |
| Static call-graph analysis | Python call graphs are not soundly resolvable without real effort: this exact suite uses monkeypatching (`_block_key_column`'s own UDF-routing test swaps a live implementation with a spy), which defeats naive static resolution. A second from-scratch static heuristic risks repeating the three-round target-resolution struggle, in a harder domain with no existing tool in this repo's chain to lean on. |
| Per-claim targeted coverage runs | Needs a way to pick candidate tests without already having what a whole-suite context run gives for free -- circular as a starting point -- and multiplies pytest invocations instead of reusing one already-running full-suite pass. |

## Being wrong

**This narrows a false-negative problem; it does not eliminate the
possibility of a different false positive.** Co-execution is not proof of
comparison. A test could run both claimant and target without ever
comparing their outputs, if both happen to fire inside one integration test
for unrelated reasons. Function-level granularity keeps this narrow, but it
is a real residual gap, and `coverage_enforcement.py`'s own docstring must
say so plainly -- the same self-honesty every detector module in this
programme carries, not a claim that this makes the signal sound.

**The riskiest unverified assumption is whether `coverage combine`
preserves dynamic contexts across shards.** This programme has learned not
to trust a coverage claim until measured -- Companion A's lesson was "the
measurement and the remedy are the same piece of work." Stage 1 proves this
against a synthetic fixture before anything is built on top of it.

**The most dangerous failure mode is the wiring going wrong silently** --
the shape this session has already found twice (PR #2839's skipped jobs,
the B3 ratchet's own test file sitting outside its filter). If the `needs:`,
the artifact name, or the filter ever drift, the failure is not a crash --
it is coverage enforcement quietly finding nothing, with the whole
mechanism degrading to text-only and nobody noticing. Two defences: the
report always states whether coverage was consulted this run, and the
wiring gets its own sabotage-verified CI-reachability test, matching every
prior job in this phase, including a sabotage that specifically breaks the
artifact/needs wiring.

**Cost is real and must be measured, not guessed.**
`python_goldenmatch_coverage` feeds `ci-required`; dynamic-context tracking
adds overhead to an already-required job on every relevant PR. Stage 1
measures the actual wall-clock delta on this repo's real matrix. If it is
too expensive to run on every PR, the fallback is a scheduled/nightly lane
rather than always-on -- a real decision point for the plan to record, not
a footnote to skip.

**No ratchet-migration risk.** C3 was never armed, so there is no existing
floor this invalidates. Stage 4's re-triage produces a first-time-honest
floor rather than breaking one that already existed.

## Delivery stages

**Stage 1 -- prove the mechanism in isolation.** Add `--cov-context=test`;
confirm contexts survive `coverage combine` across multiple shards and read
back correctly via `coverage.CoverageData`, proven against a synthetic
two-file, two-test fixture, not assumed from documentation. Measure the
real wall-clock cost here, not later.

**Stage 2 -- build `coverage_enforcement.py`, report-only, no CI wiring
yet.** The general function-span scan, the function-to-tests map, the
intersection check. Validated against the Stage 1 synthetic fixture and a
second fixture reproducing `_alias_score_matrix` -- this stage's own
motivating incident, the same discipline every earlier stage in this
programme used. Exit criterion: it correctly resolves `_alias_score_matrix`
as enforced when run against real coverage data, which the text-only check
could never do.

**Stage 3 -- wire into CI.** Extend the coverage job, upload the artifact,
wire `sync_claims`'s dependency on it, implement graceful degradation.
Sabotage-verified CI-reachability test, including a sabotage that breaks
the artifact/needs wiring specifically.

**Stage 4 -- re-triage.** Re-run the full 167-finding population under the
new mechanism. Produces the first honestly-measured false-negative rate
across the whole set, not an extrapolation from 8. Updates the C1 documents
with real numbers.

**Stage 5 -- the C3 ratchet decision.** Out of scope for this plan. Once
Stage 4 produces a trustworthy floor, whether to arm it is a separate call.

## Testing

Every new function sabotage-verified, matching every prior phase. The
contexts-survive-combine claim gets its own dedicated proof before anything
is built on it -- a fixture, not an assumption. `coverage_enforcement.py`'s
docstring states the co-execution-is-not-comparison limitation plainly.

Required tests:

- contexts for a synthetic two-file, two-test fixture survive
  `coverage combine` and are readable back correctly;
- `_alias_score_matrix` resolves as coverage-enforced against real coverage
  data;
- a claim with no coverage data at all (neither side executed) is NOT
  reported coverage-enforced;
- the CI wiring degrades to text-only, visibly, when the coverage job's
  artifact is absent;
- the report distinguishes "coverage consulted, found nothing" from
  "coverage not consulted this run".

Each verified by sabotage -- breaking the mechanism and confirming the test
fails naming the right thing -- not by observing a pass.

## Success criteria

- Contexts proven to survive `coverage combine`, not assumed.
- `_alias_score_matrix` resolves correctly from real coverage data.
- The CI wiring degrades cleanly and visibly when its dependency does not
  run.
- The measured cost is reported, not assumed, and a fallback lane is
  defined if it is too high for every-PR use.
- Stage 4 produces a real false-negative rate for the full 167 findings,
  not an extrapolation.

## Out of scope

- Arming C3's ratchet (Stage 5, a separate decision, made once the floor
  is trustworthy).
- Extending this to any package beyond `goldenmatch`.
- Closing the co-execution-is-not-comparison gap itself -- a known, stated
  limitation of this fix, not a defect this pass closes.
