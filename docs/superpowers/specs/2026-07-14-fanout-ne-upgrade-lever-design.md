# Fan-out / Negative-Evidence Upgrade Lever (Splink Migration v2)

**Date:** 2026-07-14
**Status:** Approved (design)
**Thesis phase:** Python POC (Rust/TS surfaces are later phases, out of scope)
**Predecessors:**
- The Splink migration upgrade pass (`specs/2026-07-14-splink-migration-upgrade-design.md`,
  shipped as PR #1760) -- this lever is the "v2 lever" that spec deferred.
- FS negative evidence (`specs/2026-07-14-fs-negative-evidence-design.md`, shipped as PR #1764) --
  the core capability this lever consumes; that spec's "fan-out upgrade lever will compute
  `__ne__` entries at import time" pointer is THIS design.

## Problem

Converted Splink configs have no defense against fan-out/homonym snowballs: two distinct people
sharing name+city merge because name evidence dominates, and a hard phone/email disagreement --
present in the user's data but absent from the Splink comparison set -- never gets a vote. The
converter can't fix this (a settings file has no data); FS-NE (#1764) provides the mechanism but
nothing suggests or parameterizes NE fields at migration time. Additionally,
`golden_rules.max_cluster_size` defaults to 100, so on person-shaped data (true clusters of 2-5)
an 80-record snowball sails under the existing `auto_split` guard untouched.

The upgrade pass's calibration lever carries an explicit tripwire for this feature: it warns +
skips on any NE-bearing matchkey because its pair-weight sum and model range cover regular fields
only ("skip until this lever is taught `fs_weight_range`"). This lever emits exactly that shape,
so the tripwire is replaced as part of this work.

## Decisions (from brainstorming)

- **Scope (v1):** NE suggestions with computed `__ne__` weights + data-driven cluster-guard
  tuning. Calibration NE-awareness comes along as required plumbing. Fan-out diagnosis evidence
  ships as findings (the gate's measurements), not as a separate report-only sub-lever.
- **NE weights:** posterior-weighted estimation from the imported model (chosen over fixed
  `penalty_bits` and over estimate-with-fallback). Writes REAL `__ne__` entries into the upgraded
  model copy; imported regular-field m/u untouched; no full retrain.
- **Gating:** NE fields are added only when the data shows measured fan-out risk (confident-merge
  pairs contradicted by a hard identity signal). No risk = no NE + info finding.
- **Default-on:** the lever joins the default lever set (`levers=None` runs all four). The pass is
  already opt-in via `--upgrade`, the faithful baseline is always kept, measurement reports the
  delta, and risk-gating keeps it quiet on clean data.
- **Approach A:** ONE `fan_out` lever (shared risk diagnosis feeds both NE and guards) + teaching
  the calibration lever `fs_weight_range`. Rejected: two separate levers (duplicated diagnosis or
  a cross-lever context object, multiplied ordering constraints); guard tuning inside the
  measurement stage (measurement is deliberately read-only and runs after the upgraded config is
  final).

## Shape and placement

New lever `fan_out` in `goldenmatch/config/splink_upgrade.py`'s registry, ordered:

```
tf_tables -> distance_thresholds -> fan_out -> calibration
```

so calibration always calibrates the final (possibly NE-bearing) model. Same contract as the
existing levers: findings under an `upgrade:fan_out` splink_path, warn+skip on anything
unrecoverable, never fails the pass, individually skippable via the `levers` set (new valid name
`"fan_out"`). No new CLI flags: `--upgrade` picks it up; the existing `--splink-clusters` /
`--labels` inputs gain a second job as the guard-tuning reference (they must therefore be
threaded from `upgrade_splink_conversion`'s measurement-stage arguments into the lever context --
today they are measurement-only).

**Bare-settings inputs (`conversion.em_model is None`):** the lever skips with the standard
bare-settings info note (posterior estimation needs a model; run-time EM training + autoconfig
own that case). Guard tuning also skips in this case -- keeping the lever's activation condition
single and simple.

## The shared risk diagnosis

Reuses the exact pair machinery the calibration lever already uses: `build_blocks` on the sampled
frame (same `__row_id__` wiring) + `_sample_blocked_pairs(blocks, n_pairs=_CALIBRATION_MAX_PAIRS,
seed)`. For each sampled pair, compute the match posterior under the imported model: total FS
weight over regular fields + `posterior_from_weight` with the equal-odds within-block prior
re-estimation that the calibration lever introduced (dogfood-bench lesson: the imported
`probability_two_random_records_match` is a random-pair prior, NOT the within-block rate).
**That prior re-estimation is extracted into a shared module-level helper** both levers call,
since `fan_out` now runs first. Pinned reading: the per-pair posterior is
`posterior_from_weight(total_weight, prior_weight(within_block_rate))` where `within_block_rate`
comes from the extracted re-estimation helper -- NOT the equal-odds (prior_w=0) posterior the
re-estimation uses internally as its own bootstrap. Blocked-pair scoring work is computed once
per pass and shared where practical (the two levers run back-to-back on the same sample and
seed).

The lever's internal posterior math is independent of the runtime scoring-calibration mode:
`fan_out` RUNS (does not skip) under `GOLDENMATCH_FS_CALIBRATED=posterior` -- only the
calibration lever's existing posterior-mode skip is mode-sensitive.

**NE candidate columns** -- ALL of:
- present in the data, NOT a comparison field (`f.field` of the matchkey), NOT `__record__`,
  NOT a blocking-key field (via `core.blocker.collect_blocking_fields(config.blocking)` --
  the helper takes the `BlockingConfig`; when the config has no blocking at all the lever
  warn+skips before any pair work, mirroring the calibration lever's no-blocking guard);
- cardinality ratio (n_unique / n_rows on the sample) >= 0.5;
- identity-grade name/type, reusing the `_pick_scorer_for_column` name-matching pattern from
  `core/autoconfig_negative_evidence.py` (phone/email/address/id vocabularies) -- the same
  function supplies the suggested transforms + scorer for the NE field;
- non-null rate >= a floor (default 0.5) so mostly-empty columns are not suggested (NE requires
  both sides present, so a sparse column would rarely fire anyway -- the floor keeps the config
  clean rather than protecting correctness).

**The risk gate, per candidate:** among *confident-merge* pairs (posterior >= 0.9), measure the
NE firing rate, where firing = both values present post-transform AND scorer similarity STRICTLY
`<` threshold -- via the shipped `_ne_fired` predicate from `core/probabilistic.py` (no
re-implementation). Risk is confirmed when:
- firing rate among confident-merge pairs >= a floor (default 2%), AND
- absolute count of firing confident-merge pairs >= 10 (estimation support).

This directly measures the failure mode: "the imported config would merge pairs that a hard
identity signal contradicts." Findings report the measured contradiction rate and counts whether
or not the gate passes. No gated candidate -> info finding ("no fan-out risk detected"), no NE;
guard tuning still runs.

## NE weight estimation (posterior-weighted)

For each gated candidate, over the sampled blocked pairs (posteriors `p_i`, firing indicator
`fired_i`):

```
m_fire = sum(p_i * fired_i) / sum(p_i)          # posterior-weighted firing rate
u_fire = firing rate on RANDOM row pairs          # same random-pair route train_em uses for u
```

both epsilon-clamped away from 0/1 (mirroring train_em's floors). Entries written into the
UPGRADED model copy exactly in the FS-NE storage schema (so `validate_for` passes and TS serde
round-trips):

```
m_probs["__ne__<field>"]       = [m_fire, 1 - m_fire]     # [fired, not_fired]
u_probs["__ne__<field>"]       = [u_fire, 1 - u_fire]
match_weights["__ne__<field>"] = [log2(m_fire / u_fire), 0.0]
```

**Sanity check:** if the estimated `w_fired` comes out >= 0 (firing is not rarer among matches --
the column doesn't discriminate on this data), warn + drop that candidate (no NE field, no model
entries). The NE field lands on the matchkey as
`NegativeEvidenceField(field=..., transforms=..., scorer=..., threshold=...)` with the
`_pick_scorer_for_column`-derived scorer/transforms and the default NE threshold (0.4, matching
autoconfig's `_DEFAULT_NE_THRESHOLD`); no `penalty` / no `penalty_bits` (EM-learned shape).
Finding per field: m/u/bits + the observed contradiction rate.

The same threshold/scorer/transform tuple used by the GATE must be used at RUNTIME (the
NegativeEvidenceField carries them) -- the gate measures the exact predicate that will fire in
scoring.

## Guard tuning

Reference priority: `labels` (true cluster sizes; group by label id) -> `splink_clusters` (the
user's old output's cluster sizes) -> skip with an info finding (no invented reference). With a
reference: join the reference to the sampled rows the same way measurement does (the
`id_column` mechanism; when ids cannot be joined, skip guard tuning with the same info finding
measurement emits for an unjoinable reference), compute the reference max cluster size
restricted to the sampled ids, and set

```
golden_rules.max_cluster_size = max(10, 2 * reference_max_cluster_size)
```

on the upgraded config -- symmetric: person-shaped data tightens the default 100 down to ~10-20
(letting the existing `auto_split` MST-split actually catch mid-size snowballs), while
genuinely-large-cluster domains loosen the cap and avoid wrongly splitting real clusters. Finding
reports old -> new cap and the reference evidence (max/p99 sizes, which reference was used).
`GoldenRulesConfig` is created on the upgraded config when absent.

Block-size distribution (p50/p95/max over the built blocks on the sample) is reported as findings
only -- `blocking.max_block_size` stays untouched in v1 (oversized blocks are processed, not
dropped, so tuning it has murky semantics; YAGNI).

## Calibration lever NE-awareness (the tripwire payoff)

In `_lever_calibration`:
- Replace the hand-rolled `max_weight`/`min_weight` sums with `fs_weight_range(em, mk)` from
  `core/probabilistic.py`.
- Extend the per-pair total-weight sum with NE contributions: fired -> `w_fired` (from the
  `__ne__` entry) or `-abs(penalty_bits)`, else exactly 0 -- reusing the core scalar NE
  contribution helper (`_ne_scalar_contribution` / `_ne_fired`), not a re-implementation.
- DELETE the warn+skip tripwire.

Calibrated thresholds then live on the same normalized scale runtime scoring uses for NE-bearing
configs. (Runtime scoring already normalizes via `fs_weight_range` since #1764 -- this closes the
last consumer.)

## Measurement, errors

Measurement needs zero changes: it already runs baseline + upgraded configs, and the upgraded one
now carries NE. Note: NE declines native/fused kernels (per #1764's capability gates), so the
upgraded measurement run takes the pure-Python FS path -- acceptable at the 100K sample cap; the
wall delta is visible in `RunStats.wall_seconds`. Error posture identical to existing levers
(warn+skip per candidate / per sub-step; the lever never fails the pass).

## Testing / success bar

Unit tests per component:
- candidate eligibility matrix (comparison-field / blocking-field / low-cardinality / high-null /
  non-identity-name exclusions);
- risk gate on/off (rate floor, support floor) on synthetic pairs with known posteriors;
- estimation math against hand-computed m/u/w on tiny fixtures; the w >= 0 drop;
- guard tuning from labels vs splink_clusters vs neither; the max(10, 2x) clamp both directions;
- calibration on an NE-bearing config: thresholds in range, tripwire gone, parity with a
  no-NE config when no NE field fires;
- copy-on-write invariants (baseline config + baseline EMResult untouched; deepcopy semantics);
- skip paths: bare settings, posterior calibration mode ordering, no candidates, insufficient
  pairs.

**Success bar (deterministic E2E test):** a homonym-shaped fixture in the `test_fs_ne_e2e` style
-- distinct people sharing name+city but differing on a phone column that the Splink settings
never referenced -- run through convert -> `upgrade_splink_conversion` with labels: the baseline
conversion merges the homonym traps; the lever detects the risk, adds phone NE with estimated
weights; the measured upgraded run separates the traps while true duplicates still merge; and
`vs_labels` pairwise F1 strictly improves baseline -> upgraded.

Integration: the 3 wild bench pairs (D:\ER\splink_convert_dogfood) re-run under `--upgrade` with
no pairwise-F1 regression (no-op passes -- risk not detected -- count as passes).

## Out of scope

- Rust/TS ports (thesis phases 2-3 for this feature); the TS phase must OPEN with a loud NE
  decline (TS FS scoring silently ignores NE today).
- MCP surface for the upgrade pass.
- Autoconfig-time NE promotion on probabilistic matchkeys (this lever is migration-path only;
  `promote_negative_evidence` keeps skipping probabilistic).
- `blocking.max_block_size` tuning (findings only in v1).
- Corrections-based refinement of the m/u estimates.
- Multi-matchkey configs (converted configs always carry exactly one probabilistic matchkey; the
  lever asserts this like the other levers do).
