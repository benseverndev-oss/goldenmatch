# Negative Evidence on Fellegi-Sunter Matchkeys (Formulation B, EM-learned)

**Date:** 2026-07-14
**Status:** Approved (design)
**Thesis phase:** Python POC (Rust kernel port + TS surface are later phases, out of scope)
**Supersedes:** the deferral in `docs/superpowers/specs/2026-05-21-ne-fs-investigation.md` (Wave D,
issue #126). That doc ruled Formulation B (Bayesian factor) mathematically correct but deferred it
because `P(disagree_NE | match)` was thought to require labeled pairs. The rationale is stale: EM
estimates match-conditional probabilities for every regular FS field without labels -- the same
machinery estimates them for NE dimensions. This spec implements Formulation B with EM-learned
parameters. The investigation doc gets a superseded-by note; the vaporware
`GOLDENMATCH_NE_FS_ESCAPE_MODE` doc line (never implemented) is removed rather than implemented.

## Problem

`negative_evidence` is silently ignored on `type: probabilistic` matchkeys (weighted/exact only).
The Splink-converter output is exactly one probabilistic matchkey, so migrated configs have no
negative-evidence defense against fan-out/homonym snowballs (two distinct people sharing
name+city merge because name evidence dominates; a hard phone/email disagreement should veto).
The planned fan-out upgrade lever (v2) is blocked on this core capability.

## Formulation (from the Wave D investigation, Formulation B)

Treat an NE disagreement as an additional likelihood factor preserving LLR additivity:

```
fired      = both values present (post-transform) AND scorer(a, b) <= threshold
m_ne       = P(fired | match)        # EM-learned (or penalty_bits override)
u_ne       = P(fired | non-match)    # from random pairs, like every u
w_fired    = log2(m_ne / u_ne)       # negative when matches rarely fire
contribution = w_fired if fired else 0.0
```

The `else 0.0` clamp is what makes it NEGATIVE evidence: agreement (or a missing value on either
side) never boosts the score. This differs from adding the field as a regular FS dimension, which
would credit agreement.

## Config surface

- `MatchkeyConfig._validate_weighted`: probabilistic matchkeys may carry `negative_evidence`.
- `NegativeEvidenceField` changes (`config/schemas.py:204`):
  - `penalty: float | None` -- RELAXED to optional at the schema level. The matchkey validator
    enforces per-type rules: weighted/exact REQUIRE `penalty` (existing semantics, byte-untouched);
    probabilistic REJECTS `penalty` (validation error with a message naming `penalty_bits`) --
    no silent no-op knobs.
  - NEW `penalty_bits: float | None` -- fixed LLR override in log2 units, probabilistic-only
    (weighted/exact REJECT it). When set, the NE dimension skips EM and contributes
    `-abs(penalty_bits)` when fired.
- Stale docs removed: the v1.13 "intentionally NOT extended" comment block and the
  `GOLDENMATCH_NE_FS_ESCAPE_MODE` mention in `config/schemas.py`.
- `derive_from` (synthesized columns) works for FS NE the same way it does for weighted NE.

## EM integration

- NE dimensions join `train_em` as constrained 2-state dimensions appended after the regular
  fields in the comparison matrix. Event encoding: state 0 = fired, state 1 = not-fired
  (INCLUDING nulls -- NE requires both sides present; v1.11 semantics preserved). Note this null
  handling deliberately differs from regular fields (where null -> disagree).
- u for NE dimensions comes from the same random-pair sample as regular u; m via the same EM loop.
  Blocking-field neutralization does NOT apply to NE dimensions (they are never blocking keys).
- `penalty_bits` NE fields are excluded from EM entirely.
- Storage: entries in the EXISTING `m_probs` / `u_probs` / `match_weights` dicts keyed
  `__ne__<field>` (no collision when a field is both a comparison field and an NE field; e.g.
  phone). Two-element lists indexed [fired, not_fired] for m/u; `match_weights["__ne__<field>"]`
  stores `[w_fired, 0.0]`. This rides EMResult schema v1 unchanged (plain dict entries; TS serde
  passes them through; `to_dict`/`from_dict`/`save_json` untouched) -- cross-surface compatible
  for free.
- The monotone-weights repair (`GOLDENMATCH_FS_MONOTONIC`) must NOT touch `__ne__` entries
  ([w_fired, 0.0] is intentionally "decreasing" when w_fired < 0... it is [fired, not_fired]
  ordered, not level-ordered -- exclude NE keys from the repair pass).

## Scoring

- Scalar path (`comparison_vector` consumers) and vectorized path (`score_probabilistic` /
  `score_probabilistic_vectorized` / the block scorer): after summing regular field weights, add
  the NE contributions (`w_fired` when fired else 0). NE firing uses the SAME scorer machinery
  (`score_field` / the vectorized similarity matrices) with the NE field's transforms + scorer +
  threshold.
- Normalization/calibration: the min/max total-weight range used by linear normalization and
  `compute_thresholds` must include NE ranges -- min includes `sum(min(w_fired, 0))`, max includes
  `sum(max(w_fired, 0))` (w_fired is normally negative, so max adds 0). Posterior calibration
  needs no change (weights are weights).
- Continuous/Winkler path (`train_em_continuous` / `score_probabilistic_continuous`): OUT of
  scope -- NE fields are rejected with a clear error if that path is selected with NE present
  (document; that path is already N-level-untested).

## Native + fused guards

Same playbook as level_thresholds: `_fs_native_eligible` declines matchkeys with
`negative_evidence` (pure-Python fallback; a future kernel port adds `FS_SUPPORTS_NE`);
`match_fused_fs_ready` declines likewise. Both pinned by tests.

## Persisted / imported models

`EMResult.validate_for` extended: a probabilistic matchkey with NE fields requires
`match_weights["__ne__<field>"]` for each NE field WITHOUT `penalty_bits`; missing ->
`FSModelMismatchError` naming the field and the two remedies (retrain, or set `penalty_bits`).
Models trained before this feature and imported Splink models therefore fail loudly instead of
silently scoring NE at weight 0. (The fan-out upgrade lever will compute `__ne__` entries at
import time -- out of scope here.)

## Testing / success bar

- Schema validation matrix (penalty/penalty_bits x matchkey types; both-set; neither-set).
- Event encoding unit tests: fired / not-fired / null-on-either-side / transforms applied.
- EM: `__ne__` m/u entries sum to 1 across [fired, not_fired]; penalty_bits fields absent from
  EM; monotone repair leaves `__ne__` entries alone.
- Scoring parity: scalar vs vectorized identical totals on NE-bearing matchkeys; penalty_bits
  override honored; normalization range includes NE.
- Guards: native + fused decline; validate_for missing-NE-keys error; continuous-path rejection.
- Back-compat: weighted/exact NE behavior byte-untouched (existing NE tests green, no edits).
- **Success bar (deterministic E2E test):** a synthetic homonym fixture -- two distinct people
  sharing name+city but differing on phone -- where FS WITHOUT NE merges them and FS WITH NE
  (phone as NE field) separates them while true duplicate pairs still merge. The fan-out failure
  mode this feature exists to kill, pinned as a test.

## Out of scope

- Rust kernel port (`FS_SUPPORTS_NE`) and TS surface (thesis phases 2-3).
- The fan-out upgrade lever (next feature; consumes this).
- Corrections-based refinement of m_ne (compatible later -- improves the estimate, not the
  mechanism).
- Autoconfig suggesting NE fields on probabilistic matchkeys.
- Continuous/Winkler-path NE.
