# Precision-Anchor Threshold Raise (#1319 PR2b, redesigned)

**Date:** 2026-07-15
**Status:** Approved (design)
**Issue:** #1319 (the #1207 precision-collapse follow-up). Prerequisite: #1781 fixed via PR #1782
(bucket fast path threads tf_freqs) -- this rule's remedy is inert without it.
**Supersedes:** the parked B1 rule on branch `feat/1207-pr2b-precision-anchor` (commits
2b36a8dc + d2ca98a9) -- proven unable to fire on real controller output (#1319's deferral
analysis); DISCARD, do not merge or extend it.

## Problem

On null-sparse multi-source person data (#1207 observation 2), the controller commits a config
whose weighted matchkey scores names only, and identical common full names over-merge distinct
people. The 2026-07-15 measurement pass (#1319 comment) quantified it on a crafted 2600-row
common-name/strong-email fixture: precision 0.009 at the committed 0.8 threshold, all 15,058
same-name-stranger pairs merged, `mass_above_threshold` pinned at 1.0. With #1782's fix live and
the weighted threshold raised to 0.9, the same fixture measures P 0.987 / R 1.0 / F1 0.993 --
the recall stays free because the controller's own `exact_email` matchkey anchors every true
duplicate. The remedy is validated; this feature makes the controller apply it.

## Decisions (from brainstorming + the measurement)

- **Approach A: single-shot raise to 0.9.** The trigger includes `threshold < 0.9`, so the rule
  fires at most once (trivial convergence). Rejected: incremental +0.05 steps (burns controller
  budget iterations -- the #1654/#1680 lesson -- and the measurement shows intermediate points
  don't help: 0.85 left P at 0.03); reviving the parked demote/promote rule (cannot fire).
- **The trigger is the CONFIG SHAPE, not the mass signal alone**: the healthy NCVR run (P 0.96)
  also reads `mass_above_threshold = 1.0`, so mass is necessary but not sufficient. NCVR is
  excluded by the shape condition instead (its weighted matchkey carries address/gender -- not
  name-only).

## The rule

`rule_precision_anchor_threshold_raise` in `core/autoconfig_rules.py`, matching the existing
`rule_*` signature/registration pattern, registered in `DEFAULT_RULES` (position: see Registration order below).

The rule takes the 4-arg signature (accepting the optional `ctx: IndicatorContext | None` --
`_call_rule` in autoconfig_policy.py only passes `ctx` when the signature accepts it) and
returns `None` when `ctx is None` (the `rule_sparse_match_expand` precedent; condition 3 needs
`ctx.column_priors`).

**Trigger -- ALL of:**
1. `profile.scoring.mass_above_threshold >= 0.95` (pathology gate; necessary, not sufficient --
   the healthy NCVR run also reads 1.0).
2. The config has a weighted matchkey whose fields ALL carry a NAME-CLASS SCORER --
   membership in `{"name_freq_weighted_jw", "given_name_aliased_jw"}` (rules do NOT receive the
   auto-config column classifier's output; scorer names are the config-visible proxy, and
   auto-config assigns these scorers exactly to name fields. The fixture's
   `first_name`/`last_name` qualifies; NCVR's 5-field weighted with address/gender scorers does
   not -- the shape exclusion that keeps healthy real data safe).
3. At least one EXACT matchkey whose field has `ctx.column_priors[field].identity_score >= 0.75`
   coexists (the strong-identifier recall anchor that makes the raise safe -- without it the
   raise could cost recall and the rule must not fire).
4. At least one of the weighted matchkey's name fields carries `tf_freqs` (the #1318 downweight
   must be live for the raise to separate same-name strangers; identical names without the
   table score 1.0 and clear any threshold < 1, so firing would be pure recall risk for typo'd
   dups with zero precision gain).
5. That weighted matchkey's `threshold < 0.9`.

**Remedy:** propose a copy-on-write config with the name-only weighted matchkey's threshold set
to 0.9 (the measured operating point). Copy-on-write template: the shallow
`mk.model_copy(update=...)` + rebuilt matchkeys list + `current.model_copy` pattern of
`rule_matchkey_demote_high_cardinality_field` (autoconfig_rules.py:1106) -- NOT the parked
branch's deep-copy-then-mutate. Decision recorded through the rule's standard decision-trail
mechanics (`RunHistory.decisions`) with the trigger evidence in the reason.

**Registration order:** place the rule BEFORE `rule_sparse_match_expand` in `DEFAULT_RULES`
(the parked branch's deliberate placement): sparse-expand LOWERS thresholds, and the policy's
first-proposal-wins ordering must let the precision raise pre-empt a loosen on the pathological
shape.

**Convergence:** single-fire by construction (condition 5). No env flag -- a default rule like
its siblings; the CI regression net is the same as for every controller-rule change
(DQbench/#528/synthetic gates), and the NCVR shape structurally cannot trigger it.

## Testing / success bar

- **Unit trigger matrix** (new file `tests/test_precision_anchor_1319.py` -- the parked
  branch's `test_precision_anchor_1207.py` name stays free since that branch is discarded):
  each of the five conditions independently falsified -> rule returns None; all satisfied ->
  proposal with threshold 0.9; threshold already >= 0.9 -> None (convergence); `ctx is None` ->
  None. The matrix builds a `ctx` (or a faked `column_priors` mapping) since condition 3 reads
  it. Copy-on-write pinned (input config unmutated).
- **Through the REAL controller** (the parked-B1 lesson: verify via `auto_configure_df`, never
  only hand-built configs): the #1319 crafted-fixture shape commits a config whose weighted
  matchkey threshold is 0.9, with the rule's decision visible in
  `result.postflight_report.controller_history.decisions`.
- **Success bar (the measurement close-out):** re-run the #1319 Leg-A harness on the branch
  (which includes #1782 via fresh main): bucket path, flag default-on -> precision recovers to
  ~0.99 with recall 1.0 on the crafted fixture. NCVR 10k control: the rule did NOT fire
  (committed config identical to main's).
- **Close-out:** post the numbers to #1319 and #1207; close BOTH when the bar holds (#1316 and
  #1317 remain open as their own tracks).

## Addendum (2026-07-15, approved): commit dynamics -- fired-then-discarded

P2's through-the-controller work found that on the ORIGINAL Leg-A fixture the rule FIRES but
`pick_committed` discards the raised entry. Probe evidence (entry table, gm-pr2b at f1cfba2db):

- iter0 (thr 0.8): rule fires; overall YELLOW; 18,769 pairs scored; dip 0.0619.
- iter1 (thr 0.9, the raise): only **2 pairs** survive scoring (kernel prefilter at the raised
  threshold) -> `dip_statistic = 0.0` -> the `< 0.005` unimodality gate
  (complexity_profile.py:385) reads RED -> entry rank 2, discarded.
- Even with the dip gate neutralized, iter1 ties v0 at (YELLOW, separation 0) and loses the
  ascending-iteration tiebreak (`autoconfig_history.py:209`) -- the profile metrics cannot
  distinguish the over-merged v0 from the correct raise (both read mass=1.0, bord=1.0).

Both patches were validated empirically through the real controller on the original fixture
(scratchpad probes `probe_dipfix.py`, `probe_demote.py`): with the two changes below the
committed weighted threshold is **0.9**; either change alone still commits 0.8.

### Change A: dip minimum-support guard (production, `complexity_profile.py`)

A dip statistic over a handful of pairs is sampling noise, not a unimodality signal -- the same
rationale as `_MIN_TRIPLE_SUPPORT = 30` for transitivity (`_profile_helpers.py:43-46`). In
`ScoringProfile.health()`, the dip clause becomes:

```python
if self.dip_statistic < 0.005 and self.n_pairs_scored >= _MIN_DIP_SUPPORT:
    return HealthVerdict.RED
```

with `_MIN_DIP_SUPPORT = 30` (module-level constant next to the class, comment citing the
`_MIN_TRIPLE_SUPPORT` precedent). Profiles with 1-29 scored pairs and a flat dip fall through to
the borderline/GREEN clauses instead of hard-RED. The `n_pairs_scored == 0` "nothing happened"
clause above it is unchanged.

### Change B: precision-suspect commit demotion (production, three files)

The rule's trigger is a labels-free precision-collapse detector; the commit stage must not
discard that knowledge. Mirrors the existing `precision_collapse_floor` philosophy (demote
pathological entries at commit).

- `autoconfig_rules.py`: extract the five trigger conditions into a module-level helper
  `precision_anchor_would_fire(cfg, profile, ctx) -> bool`; the rule becomes
  trigger-helper + remedy (single source of truth -- no drift possible).
- `autoconfig_history.py`: `pick_committed` gains
  `demote_suspect: Callable[[HistoryEntry], bool] | None = None`. In `key()`, compute
  `demoted = 1 if (demote_suspect is not None and demote_suspect(e)) else 0`; the
  collapse-floor branch returns `(3 + demoted, 0.0, e.iteration)` and the normal path returns
  `(rank + demoted, -sep, e.iteration)` (zero-label branch likewise `rank + demoted`).
  Default None -> byte-identical to today.
- `autoconfig_controller.py` (~957): pass the closure ONLY when
  `any(d.rule_name == "precision_anchor_threshold_raise" for d in history.decisions)`:
  `demote_suspect=lambda e: precision_anchor_would_fire(e.config, e.profile, ctx)`.

Dynamics on the fixture: v0/iter0 (thr 0.8, trigger still holds) demote YELLOW->rank 2; iter1
(thr 0.9, condition 5 false) stays rank 1 -> commits. Safety: if the raised entry profiled RED
(raise genuinely hurt), it sits at rank 2 alongside the demoted v0 and v0 wins the iteration
tiebreak -- the remedy never beats v0 unless its measured profile is at least as healthy. If the
rule fired on the final budget iteration (raise never profiled), every entry is suspect, ranks
shift uniformly, commit unchanged. Runs where the rule never fires (NCVR): closure not passed,
byte-identical.

Known pre-existing inconsistency (unchanged): `autoconfig_verify.py:302` and
`suggest/surface.py:59` re-run `pick_committed()` bare (already omitting collapse floor and
zero-label); they keep doing so.

### Addendum testing

- Unit (health): `n_pairs_scored=2, dip=0.0` -> not RED; `n_pairs_scored=30, dip=0.0` -> RED
  (boundary unchanged); the zero-pairs clause unchanged.
- Unit (pick_committed): hand-built history -- two YELLOW entries where the suspect one is
  earlier-iteration: without `demote_suspect` the earlier wins (pins today's tiebreak); with it
  the non-suspect wins; RED-raise safety case (suspect YELLOW v0 vs RED raise -> v0 still wins);
  all-suspect -> same pick as without.
- Integration: the success bar itself -- the ORIGINAL Leg-A harness commits threshold 0.9
  (covered by P3's measurement; the existing 399-row integration test must stay green).

## Out of scope

- #1316 (learned-blocking vs union reconciliation at >= 50k) and #1317 (TS parity of the
  blocking union).
- The parked B1 branch (discarded).
- Any TF-weighting changes beyond what #1318/#1782 shipped; any new env flags.
- The `unimodal_scoring` RULE still fires on the raised iteration's raw dip (it reads
  `dip_statistic` directly, not `health()`), burning budget iterations 2-3 on the fixture. The
  entries it produces are RED/discarded; harmless. Candidate follow-up, not this feature.
- Making `autoconfig_verify`/`suggest` pass commit-selection args to their bare
  `pick_committed()` calls (pre-existing).
