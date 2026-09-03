# Phase C, Stage 4 — Coverage Re-Triage of the Full High-Confidence Population

**Status:** measured; the mechanical headline number does not survive spot-check
**Date:** 2026-09-03
**Spec:** `docs/superpowers/specs/2026-09-03-coverage-based-enforcement-design.md` (Stage 4)
**Prior:** `docs/superpowers/specs/2026-09-02-c1-triage-findings.md`,
`docs/superpowers/specs/2026-09-02-c1-read-of-55.md`
**Harness:** `scripts/stage4_coverage_retriage.py`, run against CI run
`33793047003` (PR #2856, `python_goldenmatch`/`python_goldenmatch_heavy`
shards on a commit touching `core/scorer.py`)

## Why this document exists

C1 read 8 of the then-55 high-confidence findings by hand and found 5
confirmed false negatives — claims a real test verifies indirectly, that the
text-reference check reports unenforced because the test never names the
claimant. That was a real result, but an extrapolation from 8. Stage 4 is
the mechanism built to answer the same question at the full population's
scale, honestly, instead of guessing whether 8 generalizes.

It does not generalize the way the headline count first suggested. This
document's main finding is not "coverage-enforcement found N false
negatives" — it is "the mechanical rescue count overstates the true
false-negative rate by roughly 2-3x, and the reason why is itself a
concrete, useful finding."

## The mechanical run

Baseline: 53 high-confidence findings today (down from 55 at #2850 — two
claims, `spark/config_pipeline.py:_block_key_column` and
`distributed/clustering.py:_rc_union_isolated`, are now enforced by TEXT
ALONE, because the tests C1's priority list produced for them import and
call both halves directly by name; they never reached the coverage layer at
all).

Running the just-merged mechanism (`scripts/sync_claims/coverage_enforcement.py`
+ `report.py`'s `inventory(coverage_db=...)`) against real per-test coverage
data from a full CI shard run:

| | count |
| --- | ---: |
| high-confidence findings (text-only baseline) | 53 |
| coverage-rescued | 25 |
| still unenforced | 28 |
| of which fall inside `[tool.coverage.run].omit` | 0 |
| `coverage_functions_with_data` | 4,343 |

25/53 = 47%. Taken at face value, this says coverage-based enforcement
roughly doubles the enforced fraction of the high-confidence set. **It should
not be taken at face value** — see the next section.

**A real bug found and fixed getting here.** The first run of this harness
against real CI data returned `coverage_consulted: True,
coverage_functions_with_data: 0` — a silent, complete failure the final
whole-branch review's own diagnostic addition (Important #2 of that review)
was built specifically to catch, and it did. Root cause: `_relative_to_root`
required a measured file's path to be a literal subpath of `root`
(`.resolve().relative_to(root)`), which only holds when coverage data is read
in the same environment that produced it — never true when a Linux CI
artifact is downloaded and read from a local Windows checkout. Fixed in
PR #2857 by reusing `scripts/coverage_paths.py`'s `normalize()`, already
built and proven for exactly this problem by phase A's per-module floor
table going silently vacuous the same way. `coverage_functions_with_data`
went from 0 to 4,343 after the fix — the number this whole document depends
on did not exist until that bug was found and closed.

## The spot-check: 2 of 5 rescued claims hold up

Per the approved design, five of the 25 mechanically-rescued claims were
read by hand — same discipline C1 used on the original 8, same question:
does a real test actually EXECUTE both halves, and does it actually COMPARE
them, or does it just happen to run both for unrelated reasons.

| claim | claimant → target | verdict |
| --- | --- | --- |
| `core/scorer.py:1508` | `_alias_score_matrix` → `score_field` | **TRUE POSITIVE** |
| `core/survivorship/native.py:344` | `_scalar_resolution_rule` → `resolve_cluster` | **TRUE POSITIVE** |
| `core/sketch.py:337` | `simhash_band_hashes` → `band_hashes` | **FALSE POSITIVE** |
| `core/transform.py:67` | `_do_transform_columnar` → `_do_transform` | **FALSE POSITIVE** |
| `core/memory/store.py:308` | `_migrate_cluster_decision_columns` → `_migrate_field_correction_columns` | **FALSE POSITIVE** |

**The two true positives.** `_alias_score_matrix` is the mechanism's own
motivating case, confirmed exactly as designed: `tests/test_semantic_scorers.py`
calls the public wrapper `_fuzzy_score_matrix`, which reaches
`_alias_score_matrix` internally, and asserts `np.testing.assert_array_equal`
against a value built from `score_field` directly — a real comparison, one
level of indirection from the claimant's name.
`_scalar_resolution_rule` is a fourth claim in `core/survivorship/native.py`
beyond the three C1 already confirmed (`_scalar_value_expr`,
`_scalar_conf_expr`, `_resolve_conditionals`) — same mechanism, same
1,477-line parity harness, `assert_parity()` comparing
`build_survivorship_native()` byte-for-byte against the slow oracle. C1's
"three survivorship claims" underenumerated the module by one; the harness
found the fourth for free.

**The three false positives — real co-execution, no comparison.** All three
are the class the spec's "Being Wrong" section named as an accepted residual
gap: two functions genuinely both execute inside one test, but the test
never compares their outputs, because it was never testing that relationship
in the first place.

- `simhash_band_hashes` and `band_hashes` pack **different byte widths** by
  design (MinHash: 8-byte little-endian per element; SimHash: 1 byte, since
  a plane-bit is 0/1) — not comparable outputs, ever. C1 already reclassified
  this exact claim as a "pattern claim" (shared byte-layout convention, not
  equal output) rather than a real equivalence claim. The spot-check's static
  read could not locate any test naming both; querying the real coverage
  context data directly resolved it: both functions share exactly one test
  context, `tests/test_controller_adaptive_e2e.py::test_gate_fires_via_real_iteration_loop`
  — a broad end-to-end test that exercises a full adaptive pipeline
  iteration, incidentally touching both the MinHash and SimHash blocking
  paths. Nothing in that test compares the two functions.
- `_do_transform_columnar` and `_do_transform` take **incompatible input
  shapes** (`dict[str, list]` vs `pl.DataFrame`) — also already flagged by
  C1 as a pattern claim, not a real one. Confirmed sharing two real test
  contexts (`test_controller_adaptive_e2e.py::test_adaptive_budget_picks_correct_tier_at_call_time`,
  `test_memory_e2e.py::test_e2e_happy_path_reject_overrides_score`), both
  broad e2e tests, neither comparing the two functions.
- `_migrate_cluster_decision_columns` and `_migrate_field_correction_columns`
  are unconditionally called back-to-back in `MemoryStore.__init__`, so
  *every* test using the `store` fixture trivially co-executes both — dozens
  of shared contexts, guaranteed, and meaningless. They migrate different
  columns for different features; there is nothing to compare. Worth noting
  on its own: the claimant here is a genuine class method, correctly resolved
  through the dotted-name fix (`_resolve_dotted_name`, this session's earlier
  find) — a real validation of that fix at production scale, on a finding
  that happens to also be a false positive for an unrelated reason.

**What this means for the headline number.** 2 of 5 (40%) checked rescues
are real. If that rate holds across all 25, roughly **10 of 53 (≈19%)** of
the high-confidence population are genuine coverage-confirmed false
negatives — not 25 (47%). This is a small sample (n=5) and the true rate
could reasonably fall anywhere in a wide band around 19%; it is not a number
to treat as more precise than it is. What is solid: the true rate is
**meaningfully below** the mechanical count, by a factor of roughly 2, and
the false-positive class is exactly the one the spec predicted, now with
concrete instances rather than a hypothetical.

## Two mechanism blind spots found, neither anticipated by the original design

Checking two of the 28 still-unenforced claims surfaced limitations outside
what the coverage-enforcement spec's "Being Wrong" section named — not more
false negatives of the known kind, but two structurally different gaps.

**1. A non-Python target is invisible to a Python-only scanner.**
`utils/transforms.py:86 canonical_soundex` claims to be "byte-identical to
score-core ``soundex``" — score-core is the Rust crate
(`packages/rust/extensions/score-core/src/lib.rs`), not a Python module.
`_resolve_target`'s bare-word matching mis-picked the fragment "score" out
of "score-core soundex" as the resolved target, and even correctly resolved,
`function_spans()` only scans `.py` files under the goldenmatch package
root — a Rust function can never register a coverage context, however
thoroughly it's tested. Real verification for this claim genuinely exists
(`tests/test_native_soundex_parity.py` batters the native kernel against the
Python wrapper across thousands of adversarial pairs, and score-core's own
Cargo tests pin the exact codes) — the claim was already correctly
identified as enforced by C1's original manual read (`docs/superpowers/specs/
2026-09-02-c1-read-of-55.md`, item 2). "Still unenforced" here is not a
finding about the code; it's a scope limitation of the tool. A cross-language
target should either resolve to `None` (falling into `unresolvable`, not
`unenforced`) or the report should flag it distinctly rather than letting it
read as an ordinary miss.

**2. A test that references a function name as string DATA satisfies
neither check.** `identity/snowflake_backend.py:501 _rel_expr` is the exact
same claim PR #2849 ("test(identity): enforce the Snowflake
relationship-transform parity claim") closed — same claimant, same line,
same target. The C1 doc's "is done -- PR #2849" was correct about intent and
wrong about mechanical visibility: `tests/identity/test_relationship_transform_parity.py`
deliberately never calls either function (instantiating the Snowflake
backend needs a live client) — instead it `ast.parse`s both source files and
compares each function's dispatch-vocabulary string literals, referencing
`"_rel_value_expr"` and `"_rel_expr"` only as `ast.Constant` string
arguments, never as executed code and never as a named identifier
(`ast.Name`/`ast.Attribute`). Both the text-reference check
(`executable_references()` collects identifiers, not string constants) and
the coverage check (nothing inside either function's body ever executes)
are structurally blind to this pattern. This is a third false-negative
shape, distinct from the wrapper-indirection one C1 found and the two
false-positive shapes found above — a genuinely well-verified claim that
neither mechanism this programme has built can see.

Neither of these two is counted in the "still unenforced" 28 as if it were
a real gap; both are already, separately, confirmed enforced by direct human
reading (C1 for #1, PR #2849's own review for #2). They are recorded here as
limitations of the AUDIT TOOLING, not as findings about the codebase.

## What this changes about the plan going forward

- **C3's ratchet should not be armed on the mechanical rescue count.**
  A floor built on "25 rescued, therefore 25 fewer real gaps" would already
  be wrong by roughly half, on the evidence above. This reinforces, with
  real numbers now instead of a suspicion, the standing position from C1 and
  the original coverage-enforcement spec: Stage 5 stays deferred.
- **The `unresolvable` vs `unenforced` distinction needs to account for
  cross-language targets.** A `_resolve_target` that can identify "this
  target isn't a Python symbol at all" (rather than mis-picking a nearby
  English word) would remove class-1 blind-spot claims from the
  `unenforced` bucket entirely, where they currently masquerade as ordinary
  misses.
- **AST-introspection-style tests are a real, if rare, third gap shape.**
  Not urgent to fix generally — this is the only instance found — but worth
  naming so a future reader doesn't rediscover it from scratch.
- **A larger spot-check sample would tighten the ≈19% estimate**, if a
  future pass wants a number precise enough to found a ratchet on. Not done
  here: the approved design called for a 5-8 item sample matching C1's own
  precedent, not a full re-verification of all 25 — this document reports
  the honest uncertainty rather than manufacturing false precision by
  stopping the sampling discipline partway through and rounding up.

## Updates to the C1 documents

- `docs/superpowers/specs/2026-09-02-c1-triage-findings.md` and
  `2026-09-02-c1-read-of-55.md`: both should be read alongside this document
  now, not as the final word on the false-negative rate — their extrapolation
  from 8 checked items ("5 confirmed false negatives... not a random sample")
  is superseded by the population-scale number above, which is lower than
  either document's framing implied, for a reason (the false-positive class)
  neither document had evidence for yet.
- The "worth enforcing" priority list in `2026-09-02-c1-read-of-55.md`
  should be considered closed: `_alias_score_matrix`, all four (not three)
  `core/survivorship/native.py` claims, and `canonical_soundex` are now all
  confirmed enforced by one mechanism or another; `simhash_band_hashes` and
  `_do_transform_columnar` were already correctly reclassified as pattern
  claims by that same document and this pass confirms that classification
  held even under real coverage data.

## Being wrong about this document

The spot-check sample is 5 items, not the full 25 — the ≈19% true-rescue
estimate is a real, evidence-based correction to the mechanical 47%, not a
replacement precise number. A larger sample could move it meaningfully in
either direction. What should NOT move: the finding that the false-positive
class is real (not hypothetical) and material (60% of this sample), and that
it was found by doing exactly what C1 did the first time — reading the
claims by hand instead of trusting the tool's count — applied one level
further out, to the tool this programme itself just finished building.
