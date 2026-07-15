# 0039 -- Precision-anchor threshold raise with commit-time demotion (labels-free precision-collapse detection)

**Status:** Accepted. **Shipped:** goldenmatch main, unreleased (PR #1786; closes #1207 / #1319)

## Context

The #1207 over-merge shape: a name-dominated weighted matchkey where name
evidence pushes nearly all scored mass above the threshold, so precision
collapses while every health signal the controller already watched stayed
plausible. A first cut of the rule (parked B1) passed unit fixtures on a
config shape the real controller never emitted; and even once the rule fired
through the real controller, `pick_committed` discarded the raised entry --
the raise was fired but never committed. Separately, the scoring-health
unimodality (dip) gate treated a flat dip over a handful of scored pairs as
hard-RED, which is sampling noise, not evidence.

## Decision

**The rule's trigger doubles as a labels-free precision-collapse detector at
commit time, and the dip statistic requires minimum support.** Three pieces,
probe-validated together (either commit-dynamics fix alone still commits the
over-merging config):

- `rule_precision_anchor_threshold_raise` (new DEFAULT rule): on
  `mass_above_threshold >= 0.95` + a name-only weighted matchkey
  (`name_freq_weighted_jw` / `given_name_aliased_jw`) + a strong exact
  identity anchor + a live TF table + threshold < 0.9, raise the weighted
  threshold to 0.9.
- Dip minimum-support guard: `_MIN_DIP_SUPPORT = 30` in
  `complexity_profile.py` (the `_MIN_TRIPLE_SUPPORT` precedent) -- a flat dip
  over fewer than 30 scored pairs no longer reads hard-RED.
- Precision-suspect commit demotion: `precision_anchor_would_fire` is the
  single source of truth for the five trigger conditions; `pick_committed`
  gains `demote_suspect` (rank +1 in all three key() paths, default None =
  byte-identical), passed by the controller only when the rule fired this run.

Ride-along fix: a latent `n_rows` shadow in `AutoConfigController.run()`
(the suspicious-tight-blocking GREEN branch rebound the full-frame height to
the sample height) silently disabled the `REFUSE_AT_N` RED-refuse gate after
that branch; the >= 100k `ControllerNotConfidentError` refusal now actually
enforces on runs that previously slipped through.

## Consequence

- Measured on the crafted #1319 over-merge fixture: precision 0.009 -> 0.9868
  at recall 1.0. NCVR results unchanged.
- The fire-through-the-real-controller failure mode is pinned executable:
  `test_rule_fires_through_real_controller` requires the COMMITTED config to
  carry the 0.9 threshold on a stratified 399-row fixture, with a sensitivity
  twin (rule removed from `DEFAULT_RULES`) pinning the committed threshold
  below 0.9.
- `demote_suspect` never raises and defaults to byte-identical commit
  ordering, so the demotion is inert unless the rule fired.
