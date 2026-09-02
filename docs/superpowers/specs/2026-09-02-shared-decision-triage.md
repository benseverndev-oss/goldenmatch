# Phase B1 — Shared-Decision Triage

**Status:** complete
**Date:** 2026-09-02
**Spec:** `docs/superpowers/specs/2026-09-02-duplication-drift-audit-design.md`
**Predecessor:** phase B0a, the shared-decision inventory (PR #2843)

B0a reported 73 config fields accessed by more than one module. This is the
triage the spec requires: **every finding carries a classification and a
reason.** 64 are recorded as agreed in `parity/shared_decisions.allow`; 9 are
held out so the inventory keeps reporting them.

## What separated the 64 from the 9

B0a's inventory answers "which modules touch this field". It does not answer
whether those modules had to AGREE about anything, and mostly they did not:
five modules iterating `config.blocking.keys` share a field but no decision.

`scripts/shared_decisions/shapes.py` asks the second question. A **decision** is
a reader supplying something the field does not carry — a fallback value, or a
threshold. Two modules supplying different ones is the `1c843c8a5` shape.

| signal | definition | fields |
| --- | --- | ---: |
| A — fallback divergence | >1 module falls back to a DIFFERENT value | 3 |
| B — unguarded optional | field is nullable; some module falls back, another reads it bare | 6 |
| neither | no reader supplies anything, or they all supply the same thing | 65 |

64 of that 65 are allowlisted; `strategy` is the exception, held out by hand
(below).

(A and B overlap on `golden_rules`, so the two signals name 8 distinct fields.)

**`strategy` is held out as a ninth, against the signals.** Neither fires on it
-- no reader supplies a fallback; its 14 modules only compare it against
different literals, which this triage classifies as dispatch. But `strategy` is
the DISCRIMINATOR the incident turns on: it decides which of `keys`/`passes` is
correct, and F2 and F3 below are both defects in modules that do not consult it.
B0a's `test_known_incident_fields_rank_near_the_top` names `strategy` alongside
`keys` and `passes` and warns against letting it sink. Allowlisting it on a
syntactic signal would have been a suppression of an explicit earlier judgement
-- the "wrong merge" the phase-B spec warns about -- so it stays visible.

**Comparison divergence is deliberately NOT a signal.** `strategy == "static"`
in one module and `== "learned"` in another is dispatch on an enum-ish field,
which is what those fields are for. An early cut counted it and produced 13
candidates of which 10 were exactly that — `strategy` alone contributed 20
"distinct decisions" and no defect.

The classifier finds `1c843c8a5` from the checked-in fixture, not from git
history: on `scripts/fixtures/incident_1c843c8a5/`, `blocker_prefix.py` falls
back to `[]` and `score_buckets_prefix.py` to `blocking_config.keys`, on
`passes`. That is `test_the_motivating_incident_is_reported_from_the_checked_in_fixture`.

## Findings

### F1 — `distributed/pipeline.py:281` constructs a config that raises

CONFIRMED. Severity: crash, after the expensive work is done.

Four lanes build the same fallback when `config.golden_rules` is unset:

| lane | fallback |
| --- | --- |
| `core/pipeline.py:4631` | `GoldenRulesConfig(default_strategy="most_complete")` |
| `backends/datafusion_spine.py:134` | `GoldenRulesConfig(default_strategy="most_complete")` |
| `spark/config_pipeline.py:1486` | `GoldenRulesConfig(default_strategy="most_complete")` |
| `distributed/pipeline.py:281` | `GoldenRulesConfig()` |

`GoldenRulesConfig()` does not construct. `_validate_default` raises
`GoldenRulesConfig requires 'default_strategy' or 'default'.`

`golden_rules` is `GoldenRulesConfig | None` (`config/schemas.py:2444`), so it
is None on any run that does not configure survivorship, and line 281 sits on
the main path of `_run_phase5_pipeline` — not behind `_skip_golden`. The Ray
distributed lane therefore dies at the golden step **after** phase-4 matching
and phase-5 clustering have completed. At 100M rows that is hours of work
discarded on a config default.

Fix: one line, matching the other three lanes. Not applied here — the spec puts
remediation in B2, one per PR.

### F2 — `distributed/scoring.py:535` blocks on the wrong field

CONFIRMED, narrow reachability. Severity: silent recall loss.

The canonical dispatch (`core/blocker.py:560-607`, restated by the `1c843c8a5`
fix) is: `multi_pass` → `passes`, otherwise → `keys`. Never unconditional
`passes or keys`.

```
blocking.passes or blocking.keys or []      # distributed/scoring.py:535
```

Measured on a schema-valid `strategy="static"` config carrying
`keys=[org_name]` and `passes=[postcode, record_id]`:

| module | blocks on |
| --- | --- |
| `core/blocker.py` | `[org_name]` |
| `backends/score_buckets.py` | `[org_name]` |
| `distributed/scoring.py` | `[postcode, record_id]` |

`BlockingConfig._validate_keys_or_passes` forbids `keys`/`passes` only for the
`lsh`, `token` and `simhash` strategies. For `static` it requires `keys` and
does not forbid `passes`, so that config validates.

**Reachability is the caveat, and it is load-bearing.** Every one of the ten
in-repo constructions that sets both `keys` and `passes` also sets
`strategy="multi_pass"` (`config/from_dbt.py`, `config/from_splink.py`,
`core/autoconfig.py` ×8), and for `multi_pass` the unconditional form gives the
same answer. So this is reachable only through a user-authored config, not
through anything auto-config emits today. That is a narrower exposure than the
original incident, which auto-config produced directly.

### F3 — `core/blocker.py:85-88` and `:311-314` report a key set they did not block on

CONFIRMED. Severity: wrong telemetry, no wrong records.

Both compute `keys_used` as `passes` if truthy else `keys`, with no strategy
branch, for the emitted `BlockingProfile` and the block-size estimate. On a
static config carrying both, the profile names `passes` while `:560-607`
actually blocked on `keys`. At `:311-314` the disagreement is inside one
function: the sizes come from `_fast_static_block_sizes`, which reads
`config.keys` correctly at `:152`, while the label beside them says `passes`.

The consumer is auto-config's controller, so a wrong profile feeds a planning
decision. Same reachability caveat as F2.

### F4-F8 — nullable fields read bare by some modules

Signal B only, NOT confirmed. Each needs a per-module reachability check that
this triage did not do.

| field | falls back | reads bare |
| --- | --- | --- |
| `blocking` | `web/preview.py` | 9 modules incl. `core/fused_match.py`, `core/refit.py`, `spark/config_pipeline.py` |
| `matchkeys` | 5 modules | `cli/demo.py`, `cli/import_splink.py`, `config/schemas.py`, `core/pipeline.py` |
| `mode` | `core/autoconfig.py` | `core/lsh_blocker.py` |
| `path` | `core/pipeline.py` | `cli/dedupe.py`, `semantic/crosswalk.py` |
| `source_priority` | `core/survivorship/native.py` | `core/golden.py` |

Signal B says one reader has thought about None and another has not. It does
not say the bare reader can ever SEE None. Recorded as open rather than
confirmed or dismissed.

## What the 64 allowlist entries do and do not claim

An entry claims: **no two modules supply different fallbacks for this field.**

It does not claim the readers agree about anything a fallback cannot express —
units, ordering, normalisation, or the meaning of a value both modules read
identically. A field where every module does `for k in cfg.keys` passes this
triage and could still carry a semantic disagreement.

That bound is the honest limit of a syntactic pass, and it is why the entries
say what was checked rather than "agreed".

## A defect this triage exposed in B0a

`report.py` computed stale allowlist entries by comparing the allowlist against
whatever `--root` was scanned. The allowlist describes `DEFAULT_ROOT`'s field
population, so under any other root -- a test fixture, one package of several --
nearly every entry names a field that tree does not contain, and the whole
allowlist read as stale with `main` exiting 1.

**An empty allowlist could never show this.** An empty set has no stale members
whatever it is compared against, so the branch was vacuous for exactly as long
as B0a shipped it empty, and five of B0a's own tests turned red the moment B1
populated the file. Staleness is now judged against `DEFAULT_ROOT` regardless of
what was scanned.

The `shared_fields`-called-once test had to change with it: a run under a custom
root now legitimately parses two DISTINCT trees. Its invariant is that no tree is
parsed twice, so it pins the roots rather than the tally -- a bare count of 1
would have forced the fix to either re-couple staleness to the scanned root or
drop the guard.

## Two false-positive sources found and removed

Both were defects in this triage's own first cut, and both are pinned by tests.

1. **Write-only modules.** `cli/dedupe.py` and `cli/match.py` were reported as
   unguarded readers of `format` and `run_name`. Their only access is the write
   that sets the field from a command-line flag. A writer never has to cope
   with None.
2. **Validator-total fields.** `default_strategy` is `str | None` by annotation
   but `GoldenRulesConfig._validate_default` raises unless it resolves, so the
   three plain reads in `core/survivorship/` and `identity/survivorship.py` are
   correct — and `core/golden.py:645`'s `or "most_complete"` is unreachable
   defence. `VALIDATOR_TOTAL` records the exclusion, and a test constructs
   `GoldenRulesConfig()` and asserts it still raises, so relaxing the validator
   turns the exclusion back into a reported finding rather than a silent
   suppression.

## What B1 does not cover

- The 64 are cleared syntactically, not semantically (above).
- Scope is `packages/python/goldenmatch/goldenmatch` only. `goldenflow`,
  `scripts/`, and the TypeScript port are out of reach by construction, and
  their silence is not a clean bill.
- Field names come from `config/schemas.py` alone; `web/settings.py`'s
  BaseModels are not read.
- F1 is the only finding proven to fire on a config the repo itself produces.
  F2 and F3 need a user-authored config.
- No remediation. F1 is a one-line fix and is deliberately not applied here:
  the spec puts fixes in B2, one per PR, so a wrong one reverts cleanly.
