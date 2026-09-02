# Codebase Audit Programme — Phase B: Duplication and Drift

**Status:** design approved, not yet planned
**Date:** 2026-09-02
**Phase:** B of a three-phase programme (A → B → C)
**Predecessor:** `docs/superpowers/specs/2026-09-01-dead-code-audit-design.md` (phase A, complete)

## Why

Two production bugs shipped on 2026-09-01, both from lookalike code that had
drifted from the real implementation:

- `MatchEngine` kept a parallel copy of the dedupe pipeline
  (removed in `6c89042c7`, "delete MatchEngine's copy of the pipeline")
- `score_buckets` inverted `blocker.py`'s block-key dispatch
  (fixed in `1c843c8a5`, "block a static config on `keys`, not `passes` --
  silent zero pairs")

Both produced silent wrong answers — zero pairs, no error — for real users.
Neither was a cross-language divergence. Both were **same-language** copies that
nobody had registered as a pair, so nothing compared them.

Phase A shrank the surface. Phase B addresses the failure that actually shipped.

## Scope decision

**Same-language duplication only** — Python-vs-Python, TypeScript-vs-TypeScript.

Cross-language parity is already gated by four existing checks
(`scripts/check_api_parity.py`, `scripts/check_kernel_equivalence.py`,
`scripts/check_ts_parity_freshness.py`, `parity/native_symbols/`), and neither
shipped bug came from it. Auditing whether those gates measure what they claim
is worthwhile, but it is not this phase.

## Architecture

### Evidence model — invert, as phase A did

A structural clone detector run naked over the Python surface (404,416 code
lines across 2,725 files, measured 2026-09-02) produces mostly noise, because
this repository duplicates ON PURPOSE: native kernel ↔ `_py` fallback, Python ↔
TypeScript ↔ Rust surfaces, oracle implementations.

That deliberate duplication is largely **already declared**:

| declaration | what it registers |
| --- | --- |
| `parity/*.yaml` (6 packages) | cross-surface API contracts |
| `parity/native_symbols/` | host↔kernel symbol pairs |
| `_py` naming convention | pure-Python counterpart of a native kernel |
| `_native_loader._GATED_ON` | capabilities routed to native after parity sign-off |

So rather than detecting clones and then guessing which are intentional, phase B
resolves the DECLARED parity relationships first and treats those pairs as
explained by construction — the same inversion that made phase A's
registry-aware liveness work.

**A clone pair is a candidate only when structural similarity is high AND no
declared parity relationship explains it.**

Two signals again: a false positive needs both to be wrong about the same pair.

The detector's quality is bounded by how completely parity is declared, which is
measurable — and is exactly what companion A produces. The two halves feed each
other: A's inventory of declared pairs is B's exclusion set.

**Deliberate-but-undeclared duplication reads as a finding, and that is
correct.** An undeclared parity pair is precisely a pair that can drift
silently. The remedy is to declare it, not to suppress it.

### Validation against the known incidents (load-bearing)

The detector MUST find both motivating incidents at their pre-fix commits before
it is trusted for anything:

- `6c89042c7^` — `MatchEngine`'s copy of the dedupe pipeline
- `1c843c8a5^` — `score_buckets` vs `blocker.py` block-key dispatch

Both are extracted into checked-in fixtures so the test does not depend on git
history staying reachable, and both become permanent regression tests.

A detector that cannot find the bugs that motivated it is decoration. If the
thresholds cannot catch both, the thresholds are wrong — or the approach is, and
that is worth learning in B0 rather than after shipping a green gate.

### Engine: AST normalisation on stdlib `ast`

Parse every function; normalise it (canonicalise identifier and literal names,
preserve structure); hash subtrees above a statement-count threshold; group by
hash; subtract the declared-parity exclusion set.

**The thresholds are not specified here on purpose.** Minimum statement count
and similarity cutoff are TUNED IN B0 against the two incident fixtures: the
loosest setting that finds both while keeping the first report triageable. A
number picked in advance would be a guess dressed as a requirement. B0's report
must state the values it settled on and what each one costs in findings.

Rejected alternatives, with reasons:

| option | why not |
| --- | --- |
| `pylint` R0801 | not installed; token-based rather than structural; pair output is awkward to ratchet |
| `jscpd` | not installed; token-based |
| `ast-grep` alone | a PATTERN matcher — finds shapes you already know. That is what `.ast-grep/rules/ts-no-duplicate-kernel-math.yml` already does. B's job is finding what nobody thought to write a rule for |

stdlib `ast` adds no dependency and — decisively — yields a detector that can be
unit-tested with fixtures and sabotage-checked, rather than a black box whose
silence must be trusted.

### The two engines compose

Similarity surfaces an unknown clone → triage confirms it → an `ast-grep` rule
locks that shape so it cannot return. This reuses the existing
`.ast-grep/rules/` infrastructure (13 rules, with rule-tests and a CI job)
rather than building beside it.

## Companion A — parity coverage (bounded)

For each declared native↔`_py` pair, is the equivalence actually enforced?

Determined by measurement, not static inspection: run goldenflow's suite twice —
`GOLDENFLOW_NATIVE=0` and `=1` — under coverage, and identify which `_py`
functions are never executed in the native-off run. Those can drift with no test
noticing. This reuses phase A's coverage union rather than adding infrastructure.

A's deliverable is an **inventory plus a ratchet, NOT remediation**. The 1,662
lines stay; what changes is knowing which are unguarded.

### Why the `_py` population is two problems, not one

Measured 2026-09-02:

| package | `_py` functions | native dependency | consequence |
| --- | ---: | --- | --- |
| goldenflow | 108 | `goldenflow-native>=0.27.0`, UNCONDITIONAL | native always present; `_py` reachable via `GOLDENFLOW_NATIVE=0` |
| goldenmatch | 9 | `goldenmatch-native>=0.1.0`, PLATFORM-GATED (darwin, win32/AMD64, linux x86_64/aarch64) | on musl and other platforms the `_py` path is the ONLY path |

117 functions spanning 1,662 lines, measured by AST on 2026-09-02. Phase A's spec
quoted "118 ... ~1,680 lines"; that figure was inherited rather than re-measured,
and is corrected here.

goldenflow's 108 are a supported execution mode, not a fallback of last resort.
goldenmatch's 9 are load-bearing on unsupported platforms. Neither set is dead;
they are deliberate same-language duplication and must not be deleted by this
phase.

Existing parity coverage is **family-by-family, not generic**:
`tests/transforms/test_native_parity.py` is scoped to the phone kernel alone;
`test_fastpath_parity.py` imports three specific `_date_*_py` functions by name;
other coverage sits in per-family kernel tests. Nothing enumerates all
transforms the way phase A's liveness enumerated registries.

**Measurement caution, learned the hard way during this design.** An early probe
counted how many `_py` functions were NAMED in a test file and reported "101 of
108 untested". That was measuring the wrong thing — real coverage comes from
tests that toggle `GOLDENFLOW_NATIVE` and compare, not from naming the function.
Any coverage claim in this phase must measure execution, not mention.

## Being wrong

**Phase B's dangerous failure is the opposite of phase A's.** A's was deleting
live code. B's is a **bad merge**: collapsing two implementations that must stay
separate — a cross-surface contract, or a platform fallback — into one.

That is far more damaging than a missed clone, and the noise invites it: a long
findings list pressures people toward collapsing things.

Defences, in order:

1. **Declared-parity exclusion** — deliberate pairs are explained by
   construction, not by judgement.
2. **Mandatory human triage** — every finding is classified before any action.
3. **No automated remediation, ever.** B proposes; a person disposes.
4. **One remediation per PR**, so a wrong merge reverts cleanly.
5. **Undeclared-but-deliberate pairs get DECLARED, never merged.**

A missed duplicate costs what we have today. A wrong merge costs a silent wrong
answer in production — the thing this programme exists to prevent.

## Delivery stages

**B0 — detector, report-only.** Build the AST normaliser, the declared-parity
exclusion set, and the report. Exit criterion: it finds both `6c89042c7^` and
`1c843c8a5^`, proven by checked-in fixtures.

**B1 — first real report, triaged.** Classify every finding as confirmed
duplication / deliberate-but-undeclared / false positive. Exit criterion: every
finding has a classification and a reason.

**B2 — remediation.** Confirmed duplicates resolved one per PR. Undeclared
deliberate pairs are declared. Exit criterion: no confirmed duplicate remains
unresolved or unrecorded.

**B3 — ratchet on.** Gate at the triaged floor, matching `KNOWN_DEAD` /
`KNOWN_POLARS_BOUND` / `KNOWN_JOB_FILTER_GAPS`.

**A — parity-coverage inventory.** Runnable alongside B0; they share the
coverage machinery.

## Testing

The detector is tested like a product component, because a detector that
silently measures nothing is the failure mode this repository keeps hitting —
phase A alone caught five instances of it, including two inside the tooling
built to prevent it.

Required tests:

- both incident fixtures ARE reported;
- a declared native↔`_py` pair is NOT reported;
- a pair of trivially-similar short functions below the size threshold is NOT
  reported;
- renaming every identifier in a clone does not hide it (Type-2 detection
  actually works);
- the report distinguishes "no findings" from "detector did not run".

Each verified by sabotage — breaking the detector and confirming the test fails
— not by observing a pass.

## Success criteria

- The detector finds both motivating incidents from checked-in fixtures.
- Every finding in the first real report carries a classification and a reason.
- Every confirmed duplicate is resolved, or recorded with why it stands.
- Every deliberate-but-undeclared pair is declared.
- The ratchet gates at the triaged floor.
- No cross-surface contract or platform fallback is collapsed.

## Out of scope

- Cross-language drift, and auditing the four existing parity gates.
- Deleting the `_py` populations — they are deliberate, live duplication.
- Symbol-level dead code (phase A2).
- Module splitting and cohesion (phase C).
- `_archive/`, retained pre-fold history.
